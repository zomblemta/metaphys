"""应用装配 —— 组装容器、挂上中间件、错误处理器与路由。

这个模块**只做装配**。业务规则在 ``app/application/``，存储实现在
``app/infrastructure/``，具体怎么接在 ``app/bootstrap.py``。这里剩下的
东西有两类，都不能随便挪：

1. **模块级部署常量**（``COOKIE`` / ``FRONTEND``）—— 集成测试用
   ``monkeypatch.setattr("app.gateway.main.FRONTEND", …)`` 替换它们。补丁打在
   *模块属性*上，所以引用必须以"每次请求重新读全局名"的形式出现，
   不能在 ``create_app`` 里固化成一个局部变量。这就是 ``_frontend_dir()`` /
   ``_svg_dir()`` 写成闭包而不是直接传值的原因。
2. **``resolve_svg_dir`` / ``svg_filename`` 的再导出** —— 前者被
   ``tests/test_gateway.py`` 补丁，需要这个名字在模块里存在；两者也是这个模块
   历史上对外暴露过的东西，删掉等于对调用方做一次静默破坏。

``create_app`` **不读配置**：集成测试注入脚本模型正是为了不需要模型配置，
启动期唯一需要配置的动作（构造图）在 ``Container.startup`` 里按需触发。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI
from metaphys.runtime import RunService
from metaphys.tools.builtins.astro_chart import resolve_svg_dir, svg_filename  # noqa: F401 —— 见模块 docstring
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.bootstrap import Container, build_container
from app.gateway.errors import install_error_handlers
from app.gateway.middleware import install_middleware
from app.gateway.routers import include_routers
from app.infrastructure.memory import SessionStore
from app.settings import Settings

logger = logging.getLogger(__name__)

COOKIE = "metaphys_session"
"""会话 Cookie 名。前端不需要知道它（浏览器自己带），但测试要。"""

FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "out"
"""Next 静态导出目录。测试在应用构造**之后**替换它，故按请求读取。"""


def _frontend_dir() -> Path:
    return FRONTEND


def _svg_dir() -> Path:
    """星盘图输出目录。

    必须**按请求**解析，两个原因：``resolve_svg_dir()`` 内部会读应用配置，
    而配置在构造期可能还不存在；测试也在请求之前才替换它。
    """
    return resolve_svg_dir()


def create_app(
    *,
    service: RunService | None = None,
    store: SessionStore | None = None,
    run_timeout: float = 180,
    settings: Settings | None = None,
) -> FastAPI:
    """构造应用。可注入脚本模型做完整 HTTP 验证。

    ``service`` / ``store`` 为 ``None`` 时才用真实实现（不是 falsy 判断）：
    注入的替身是鸭子类型的裸对象，其中一些可能定义 ``__len__`` 而为假值，
    被换成真实实现后表现为"测试通过了但跑的是另一套代码"。

    ``settings`` 与 ``run_timeout`` 同时给出时以 ``settings`` 为准；保留
    ``run_timeout`` 是因为它是 M4 就有的调用方式。
    """
    if settings is None:
        settings = replace(Settings(), run_timeout=float(run_timeout))

    container = build_container(
        service=service,
        store=store,
        settings=settings,
        cookie_name=COOKIE,
        frontend_dir=_frontend_dir,
        svg_dir=_svg_dir,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await container.startup()
        try:
            yield
        finally:
            # 关不掉的过程序比丢掉一轮运行更糟：这里只等到截止时间。
            await container.shutdown()

    app = FastAPI(title="metaphys", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.container = container

    # TrustedHost 先注册 → 它在用户中间件里最靠内，与 M4 的相对顺序一致。
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
    install_middleware(app, settings)
    install_error_handlers(app)
    include_routers(app)

    return app


__all__ = ["COOKIE", "FRONTEND", "Container", "create_app", "resolve_svg_dir", "svg_filename"]
