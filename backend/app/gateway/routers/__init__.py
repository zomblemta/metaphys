"""路由装配。

顺序即匹配顺序（Starlette 先到先得）。这里按"越具体越靠前"排，且把
``deferred`` 放最后 —— 它的路径带通配段（``/api/v1/charts/{chart_id}``），
排在前面会把将来真正实现的那条遮住，而症状是"新写的路由不生效"。

``frontend`` 必须在 ``deferred`` 之前、且它的 ``/_next/{filename:path}``
是贪婪匹配 —— 放到最后会让它吃掉所有未匹配路径。
"""

from __future__ import annotations

from fastapi import FastAPI

from app.gateway.routers import (
    artifacts,
    conversations,
    deferred,
    frontend,
    health,
    runs,
    session,
)

#: 装配顺序。改动这里之前先读模块 docstring。
ROUTERS = (
    health.router,
    frontend.router,
    artifacts.router,
    session.router,
    conversations.router,
    runs.router,
    deferred.router,
)


def include_routers(app: FastAPI) -> None:
    for router in ROUTERS:
        app.include_router(router)


__all__ = ["ROUTERS", "include_routers"]
