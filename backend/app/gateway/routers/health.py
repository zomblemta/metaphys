"""存活与就绪。

两者分开是因为它们回答的问题不同，且**该有不同的答案**：``live`` 问的是
"进程还在吗"（永远 200，否则编排系统会重启一个正在正常收尾的进程）；
``ready`` 问的是"现在能接流量吗"（启动校验没过、正在关闭时必须是 503，
否则流量会被送到一个还没有模型的进程上）。

阶段 A 的就绪只表示启动校验通过 —— 文档 §5.3 要求的"恢复未完成运行"属
阶段 C，见 ``bootstrap.Container.ready`` 的注释。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.gateway.dependencies import container

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """进程存活。不查任何依赖 —— 那正是 ``ready`` 的职责。"""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    """是否可以接流量。未就绪时 503，让编排系统把流量摘走而不是重试打满。"""
    state = container(request)
    if not state.ready:
        return JSONResponse({"status": "starting"}, status_code=503)
    return JSONResponse({"status": "ready"})


__all__ = ["router"]
