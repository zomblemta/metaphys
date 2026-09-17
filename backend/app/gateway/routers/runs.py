"""运行：发起、查询、订阅。旧 ``/api/*`` 与 ``/api/v1/*`` 同处一室。

这是本次拆分里风险最高的一块，因为 M4 的三条行为约束都落在这里，且**任何
一条破了都不会报错，只会让前端安静地不工作**：

1. **浏览器断开不取消运行。** 断开只让订阅停掉；执行是另一个 task，它照常
   跑完并落库。所以这里绝不把执行和响应绑在同一个协程里，也绝不在
   ``StreamingResponse`` 的收尾里取消它。
2. **终态帧之后立即结束。** 旧客户端靠"流结束"判断这一轮完了。
   ``compat.legacy_frame`` 对 ``run.end`` 返回 ``None``，正是这个 EOF。
3. **失败只给通用文案。** 供应商异常、路径、原始输入一律不出现在帧里。

## 两套路径为什么都留着

旧的是前端在用的；v1 是本次要立的规范接口。两者调用**同一个** ``RunsService``，
差别只在帧的翻译与响应头，所以不存在"旧接口某天行为和新的不一样"。

## 心跳

SSE 在空闲时发 ``: keep-alive``。实现上把订阅泵进队列、路由侧带超时地取，
而不是对订阅生成器直接 ``wait_for`` —— 后者会在超时时把 ``CancelledError``
抛进生成器并永久终结它，表现为"第一次心跳之后再也没有事件"。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.application.views import run_view
from app.domain.errors import NotImplementedYet
from app.domain.models import RunEvent
from app.gateway.compat import legacy_frame
from app.gateway.dependencies import OwnerDep, WriterDep, container
from app.gateway.schemas.dto import RunInput

router = APIRouter(tags=["runs"])

#: 幂等键头。阶段 A 没有唯一索引与幂等表（文档 §4），因此**出现即 501**。
#: 静默接受比拒绝更糟：调用方以为重试是安全的，于是真的会重试。
IDEMPOTENCY_HEADER = "idempotency-key"

_DONE = object()

#: SSE 响应头。``X-Accel-Buffering: no`` 关掉 nginx 的缓冲，否则事件会被
#: 攒到连接关闭才一次性送达 —— 流式界面看起来像卡住了。
_SSE_HEADERS = {"X-Accel-Buffering": "no", "Cache-Control": "no-store"}


def _frame(event: RunEvent) -> str:
    """v1 帧：``id`` 就是重放游标。"""
    return f"id: {event.cursor()}\nevent: {event.type}\ndata: {json.dumps(event.as_dict(), ensure_ascii=False)}\n\n"


def _legacy(event: RunEvent) -> str | None:
    translated = legacy_frame(event)
    if translated is None:
        return None
    return f"event: {translated['event']}\ndata: {json.dumps(translated, ensure_ascii=False)}\n\n"


async def _relay(
    events: AsyncIterator[RunEvent], render: Callable[[RunEvent], str | None], keep_alive: float
) -> AsyncIterator[str]:
    """把事件流翻译成 SSE 文本，空闲时发心跳。

    订阅泵在一个独立 task 里、经队列转手，路由侧因此能"等不到就发心跳"。
    对生成器本体用 ``wait_for`` 是行不通的：超时会向生成器抛
    ``CancelledError``，而它不会自己恢复 —— 第一次心跳之后流就死了。
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in events:
                queue.put_nowait(event)
        finally:
            queue.put_nowait(_DONE)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=keep_alive)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if item is _DONE:
                return
            text = render(item)
            if text is not None:
                yield text
    finally:
        # 客户端断开时走到这里：只停订阅，不碰运行本身。
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# --------------------------------------------------------------------------- #
# 旧 /api/* —— M4 契约
# --------------------------------------------------------------------------- #


@router.post("/api/conversations/{conversation_id}/runs")
async def run(conversation_id: str, body: RunInput, request: Request, session: WriterDep) -> StreamingResponse:
    state = container(request)
    run_ = await state.runs_service.start(session, conversation_id, body.message)
    start = state.runs_service.prepare_subscription(session, conversation_id, run_.id, None)
    return StreamingResponse(
        _relay(state.runs_service.subscribe(run_.id, start), _legacy, state.settings.keep_alive),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# --------------------------------------------------------------------------- #
# /api/v1/*
# --------------------------------------------------------------------------- #


@router.post("/api/v1/conversations/{conversation_id}/runs", status_code=202)
async def start_run(conversation_id: str, body: RunInput, request: Request, session: WriterDep) -> dict:
    """受理一轮运行并**立即**返回，不等结果。

    202 而不是 200：运行还没结束。事件另走 ``events_url``，因此"发起"与
    "观察"是两次独立的请求 —— 客户端刷新、换标签页、甚至换进程重连，都不
    影响这一轮，这也正是事件日志必须存在的原因。
    """
    if request.headers.get(IDEMPOTENCY_HEADER):
        raise NotImplementedYet("幂等键将在阶段 C 支持；当前请不要依赖重试去重。")
    state = container(request)
    run_ = await state.runs_service.start(session, conversation_id, body.message)
    return {
        "run_id": run_.id,
        "conversation_id": run_.conversation_id,
        "status": str(run_.status),
        "events_url": f"/api/v1/conversations/{run_.conversation_id}/runs/{run_.id}/events",
    }


@router.get("/api/v1/conversations/{conversation_id}/runs/{run_id}")
async def get_run(conversation_id: str, run_id: str, request: Request, session: OwnerDep) -> dict:
    state = container(request)
    return run_view(state.runs_service.get(session, conversation_id, run_id))


@router.get("/api/v1/conversations/{conversation_id}/runs/{run_id}/events")
async def stream_run_events(
    conversation_id: str, run_id: str, request: Request, session: OwnerDep
) -> StreamingResponse:
    """订阅或重放一轮的事件。

    ``Last-Event-ID`` 是浏览器 ``EventSource`` 自动重连时带上的头；手工重放
    也可以用 ``?cursor=``。两者等价，因为游标就是事件日志的下标 —— 重放是
    "按下标续读"，不是"重新跑一遍"。
    """
    state = container(request)
    cursor = request.headers.get("last-event-id") or request.query_params.get("cursor")
    # 校验必须在返回流之前：否则 404/400 会在 200 之后才出现。
    start = state.runs_service.prepare_subscription(session, conversation_id, run_id, cursor)
    return StreamingResponse(
        _relay(state.runs_service.subscribe(run_id, start), _frame, state.settings.keep_alive),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


__all__ = ["router"]
