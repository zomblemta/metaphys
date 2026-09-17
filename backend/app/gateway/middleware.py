"""HTTP 请求边界：实际正文限额、同源校验、安全响应头与请求 ID。"""

from __future__ import annotations

import logging
import os
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.gateway.errors import error_body
from app.settings import Settings

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class BodyLimitMiddleware:
    """在路由运行前限制实际接收字节，包含无长度声明的分块请求。"""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                request = Request(scope)
                response = JSONResponse(
                    error_body(request, code="payload_too_large", message="请求过长。", retryable=False),
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def install_middleware(app: FastAPI, settings: Settings) -> None:
    """注册全部 HTTP 中间件。调用方只需调一次。"""

    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        # 声明长度用于提前拒绝；实际字节数由内层 ASGI 中间件限制。
        length = request.headers.get("content-length")
        if length and (not re.fullmatch(r"[0-9]{1,10}", length) or int(length) > settings.max_body_bytes):
            return JSONResponse(
                error_body(request, code="payload_too_large", message="请求过长。", retryable=False),
                status_code=413,
            )
        if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
            origin = request.headers.get("origin")
            allowed_origins = {str(request.base_url).rstrip("/")}
            # 开发代理仅在显式本地开发开关启用时接受，生产默认仍严格同源。
            if os.environ.get("METAPHYS_NEXT_DEV") == "1":
                allowed_origins.update({"http://127.0.0.1:3000", "http://localhost:3000"})
            if origin and origin not in allowed_origins:
                return JSONResponse(
                    error_body(request, code="forbidden", message="不允许跨站请求。", retryable=False),
                    status_code=403,
                )
        response = await call_next(request)
        return response

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        # 只在形状可信时才沿用调用方的值：这个 id 会进日志，接受任意长度的
        # 自由文本等于让外部决定日志行的长相。
        request.state.request_id = incoming if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", incoming) else uuid.uuid4().hex
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        return response


__all__ = ["REQUEST_ID_HEADER", "install_middleware"]
