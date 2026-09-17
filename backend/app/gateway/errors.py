"""领域错误 → HTTP 的**唯一**映射表，以及三类异常处理器。

## 为什么是一张表

状态码散在各处时，"这个错误到底回 409 还是 429"只有一个地方能回答：运行
一次。收成一张表之后，`/api/v1` 的错误形状可以被逐个断言，而不必为每种
错误各造一个请求。

## 为什么必须注册 ``StarletteHTTPException`` 处理器

FastAPI 对**自己**产生的 404（未知路径）与 405（方法不对）走的是内置处理器，
不经过用户处理器。只注册 ``DomainError`` 的话，"统一错误响应"就只对主动
抛出的错误成立，而客户端最常撞上的恰恰是拼错路径那一种 —— 拿到的是另一套
形状，且没有任何地方写着这件事。所以这里把三类都接住。

## 双轨

``/api/v1/*`` 用信封 ``{"error": {code, message, retryable, request_id}}``；
其余路径保持 ``{"detail": ...}`` —— 前端 ``lib/api.ts`` 与既有测试都读
``detail``，改它等于改前端契约，而本次不动前端。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.application import messages
from app.domain.errors import (
    Conflict,
    ConversationLimit,
    DomainError,
    Forbidden,
    IllegalTransition,
    InvalidInput,
    NotFound,
    NotImplementedYet,
    RunCapacity,
    SessionCapacity,
    Unauthorized,
    Unavailable,
)

logger = logging.getLogger(__name__)

#: 领域错误 → 状态码。按**类型**精确匹配，未命中则沿 MRO 上溯。
#:
#: 上溯是必需的：``ConversationLimit`` 是 ``Conflict`` 的子类但语义不同
#: （客户端该做的是删掉旧会话，不是稍后重试），精确匹配不到时退回到父类，
#: 至少不会掉成 500。
DOMAIN_STATUS: dict[type[DomainError], int] = {
    Unauthorized: 401,
    Forbidden: 403,
    NotFound: 404,
    Conflict: 409,
    ConversationLimit: 409,
    # 400 而非 422：走到这里的都是**请求本身**有问题（URL 里的游标坏了），
    # 而不是请求体里的字段不合法 —— 后者由 RequestValidationError 处理，
    # 仍是 422。两者混用会让客户端分不清"改参数"和"改正文"。
    InvalidInput: 400,
    RunCapacity: 429,
    SessionCapacity: 503,
    Unavailable: 503,
    NotImplementedYet: 501,
    IllegalTransition: 500,
}

#: 框架自身抛出的状态码 → 稳定 code。只覆盖会真实出现的那几个。
STATUS_CODES: dict[int, str] = {
    400: "invalid_input",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "invalid_input",
    429: "run_capacity",
    500: "error",
    501: "not_implemented",
    503: "unavailable",
}

#: 这些状态码下重试是有意义的，客户端可以据此决定是否自动重试。
RETRYABLE_STATUS = frozenset({429, 503})


def is_v1(request: Request) -> bool:
    """该请求是否走 v1 契约。决定错误响应的形状。"""
    path = request.url.path
    return path == "/api/v1" or path.startswith("/api/v1/")


def status_for(exc: DomainError) -> int:
    """领域错误对应的状态码。未登记的类型落 500 —— 那确实是服务端的问题。"""
    for klass in type(exc).__mro__:
        if klass in DOMAIN_STATUS:
            return DOMAIN_STATUS[klass]
    return 500


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def error_body(request: Request, *, code: str, message: str, retryable: bool) -> dict:
    """按轨道构造响应体。"""
    if is_v1(request):
        return {
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "request_id": _request_id(request),
            }
        }
    return {"detail": message}


def install_error_handlers(app: FastAPI) -> None:
    """注册三类处理器。调用方只需调一次。"""

    @app.exception_handler(DomainError)
    async def on_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status = status_for(exc)
        if status >= 500:
            # 5xx 说明我们这边出了问题；详情只进日志，不进响应。
            logger.warning("领域错误 %s：%s", exc.code, exc.message)
        return JSONResponse(
            error_body(request, code=exc.code, message=exc.message, retryable=exc.retryable),
            status_code=status,
        )

    @app.exception_handler(RequestValidationError)
    async def on_invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic 默认响应会回显 input —— 没必要把出生资料再复制一份到错误里。
        return JSONResponse(
            error_body(request, code="invalid_input", message=messages.INVALID_MESSAGE, retryable=False),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = exc.status_code
        # 保留框架给的响应头：405 的 Allow 丢了，客户端就不知道能用什么方法。
        headers = getattr(exc, "headers", None)
        return JSONResponse(
            error_body(
                request,
                code=STATUS_CODES.get(status, "error"),
                message=str(exc.detail),
                retryable=status in RETRYABLE_STATUS,
            ),
            status_code=status,
            headers=headers,
        )


__all__ = [
    "DOMAIN_STATUS",
    "RETRYABLE_STATUS",
    "STATUS_CODES",
    "error_body",
    "install_error_handlers",
    "is_v1",
    "status_for",
]
