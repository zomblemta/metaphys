"""持久化部署的 HTTP 契约；旧 Next 客户端与独立 v1 Run 共享数据库执行器。"""

import asyncio
import json
import secrets
import time
from hashlib import sha256
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from app.domain.errors import Forbidden, InvalidInput, NotFound, Unavailable
from app.gateway.schemas.dto import RunInput

router = APIRouter()
COOKIE = "metaphys_session"


def state(request):
    return request.app.state.durable


def ids(*values):
    try:
        for value in values:
            UUID(value)
    except ValueError as exc:
        raise NotFound("资源不存在。") from exc


async def writer(request):
    csrf = await state(request).db.check_session(request.state.owner, request.cookies.get(COOKIE, ""))
    if not secrets.compare_digest(csrf, request.headers.get("x-csrf-token", "")):
        raise Forbidden("请求校验失败，请刷新页面。")


@router.get("/health/live")
async def live():
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request):
    if not state(request).runs.ready:
        raise Unavailable("服务未就绪。")
    await state(request).db.ready()
    return {"status": "ready"}


@router.get("/api/session")
@router.get("/api/v1/session")
async def session(request: Request, response: Response):
    token = request.cookies.get(COOKIE) or secrets.token_urlsafe(32)
    # Tokens are opaque and bounded; a supplied token never conveys ownership.
    if len(token) > 128:
        token = secrets.token_urlsafe(32)
    token, csrf = await state(request).db.session(request.state.owner, token, secrets.token_urlsafe(32))
    response.set_cookie(COOKIE, token, httponly=True, secure=True, samesite="strict", max_age=86400)
    return {
        "csrf_token": csrf,
        "storage": "database",
        "conversations": await state(request).db.conversations(request.state.owner),
        "capabilities": {"persistence": "postgres", "idempotency": True, "multi_worker": False, "cancel": False},
    }


@router.post("/api/conversations", status_code=201)
@router.post("/api/v1/conversations", status_code=201)
async def create(request: Request):
    await writer(request)
    return await state(request).db.create_conversation(request.state.owner)


@router.get("/api/conversations/{cid}")
@router.get("/api/v1/conversations/{cid}")
async def conversation(cid: str, request: Request):
    ids(cid)
    return await state(request).db.conversation(request.state.owner, cid)


@router.delete("/api/conversations/{cid}", status_code=204)
@router.delete("/api/v1/conversations/{cid}", status_code=204)
async def delete(cid: str, request: Request):
    ids(cid)
    await writer(request)
    await state(request).db.mark_deleting(request.state.owner, cid)
    # Durable tombstone already rejects new runs. Restart resumes this cleanup.
    await state(request).executor.delete_execution(cid)
    await state(request).db.remove(cid)
    return Response(status_code=204)


async def admit(cid, body, request):
    ids(cid)
    await writer(request)
    if not state(request).runs.ready or state(request).runs.closing:
        raise Unavailable("服务未就绪。")
    key = request.headers.get("idempotency-key")
    if key is not None:
        if not key.strip() or len(key) > 255:
            raise InvalidInput("幂等键必须为 1–255 字符。")
        key = sha256(key.encode()).hexdigest()
    return await state(request).db.admit(request.state.owner, cid, body.message, key)


async def feed(request, cid, rid, seq=0, legacy=False):
    last_heartbeat = time.monotonic()
    while True:
        events = await state(request).db.events(request.state.owner, cid, rid, seq)
        for event in events:
            seq = event["seq"]
            kind = event["kind"]
            data = event["data"]
            if legacy:
                if kind == "run.end":
                    return
                name = {"run.progress": "update", "run.final": "final", "run.failed": "error"}.get(kind)
                if not name:
                    continue
                data = (
                    {"event": name, "schema_version": 1, "run_id": rid, "data": data}
                    if name != "error"
                    else {"event": "error", **data}
                )
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            else:
                data = {"schema_version": 1, "run_id": rid, "seq": seq, "type": kind, "data": data}
                yield f"id: {rid}:{seq}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            if kind == "run.end":
                return
        if not events:
            run = await state(request).db.run(request.state.owner, cid, rid)
            if run["status"] not in ("queued", "running"):
                return
        if await request.is_disconnected():
            return
        if time.monotonic() - last_heartbeat > 15:
            yield ": keep-alive\n\n"
            last_heartbeat = time.monotonic()
        await asyncio.sleep(0.25)


def stream(iterator):
    return StreamingResponse(
        iterator, media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )


@router.post("/api/conversations/{cid}/runs")
async def run_legacy(cid: str, body: RunInput, request: Request):
    rid = await admit(cid, body, request)
    return stream(feed(request, cid, rid, legacy=True))


@router.post("/api/v1/conversations/{cid}/runs", status_code=202)
async def run_v1(cid: str, body: RunInput, request: Request):
    rid = await admit(cid, body, request)
    return {"run_id": rid, "events_url": f"/api/v1/conversations/{cid}/runs/{rid}/events"}


@router.get("/api/v1/conversations/{cid}/runs/{rid}")
async def get_run(cid: str, rid: str, request: Request):
    ids(cid, rid)
    return await state(request).db.run(request.state.owner, cid, rid)


@router.get("/api/v1/conversations/{cid}/runs/{rid}/events")
async def events(cid: str, rid: str, request: Request):
    ids(cid, rid)
    await state(request).db.run(request.state.owner, cid, rid)
    cursor = request.headers.get("last-event-id") or request.query_params.get("cursor")
    seq = 0
    if cursor:
        prefix, _, number = cursor.partition(":")
        if prefix != rid or not number.isascii() or not number.isdecimal() or len(number) > 10:
            raise InvalidInput("事件游标无效。")
        seq = int(number)
        rows = await state(request).db.events(request.state.owner, cid, rid, seq - 1)
        if seq and (not rows or rows[0]["seq"] != seq):
            raise InvalidInput("事件游标超出范围。")
    return stream(feed(request, cid, rid, seq))


@router.get("/api/conversations/{cid}/chart.svg")
async def chart(cid: str, request: Request):
    ids(cid)
    conversation = await state(request).db.conversation(request.state.owner, cid)
    chart = (conversation.get("result") or {}).get("charts", {}).get("astro")
    path = state(request).artifacts.locate(chart) if chart else None
    if path is None:
        raise NotFound("星盘图片不存在。")
    return FileResponse(
        path,
        media_type="image/svg+xml",
        headers={"Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'"},
    )
