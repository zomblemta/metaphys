"""会话引导。

两套路径返回的东西**故意不同**：旧接口带会话列表，因为它就是前端的首屏
数据；v1 只给 ``csrf_token`` 与能力清单，列表走 ``GET /api/v1/conversations``。
把列表塞进引导响应会让"新建一个会话"顺带传回二十个会话的全部元信息，
而调用方往往只想要一个 token。

``secure`` 跟着请求的 scheme 走：本地 HTTP 下带上它，Cookie 会被浏览器
直接丢掉，表现为"刷新后登录态没了"。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.application.views import CAPABILITIES, conversation_summary
from app.gateway.dependencies import container

router = APIRouter(tags=["session"])


def _set_session_cookie(response: Response, request: Request, token: str, name: str) -> None:
    response.set_cookie(name, token, httponly=True, samesite="strict", secure=request.url.scheme == "https")


@router.get("/api/session")
async def bootstrap(request: Request, response: Response) -> dict:
    state = container(request)
    session = await state.session_service.bootstrap(request.cookies.get(state.cookie_name))
    _set_session_cookie(response, request, session.token, state.cookie_name)
    return {
        "csrf_token": session.csrf,
        "storage": "memory" if CAPABILITIES["persistence"] == "memory" else "database",
        "conversations": [conversation_summary(c) for c in state.conversation_service.summary(session)],
    }


@router.get("/api/v1/session")
async def bootstrap_v1(request: Request, response: Response) -> dict:
    state = container(request)
    session = await state.session_service.bootstrap(request.cookies.get(state.cookie_name))
    _set_session_cookie(response, request, session.token, state.cookie_name)
    return {
        "csrf_token": session.csrf,
        "capabilities": CAPABILITIES,
    }


__all__ = ["router"]
