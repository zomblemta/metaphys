"""受邀内测部署入口：Postgres、HTTPS、逐请求账号认证、单实例运行锁。"""

import asyncio
import base64
import os
import secrets
import time
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.application.durable_runs import DurableRuns
from app.gateway.errors import error_body, install_error_handlers
from app.gateway.middleware import install_middleware
from app.gateway.routers import frontend
from app.gateway.routers.durable import router
from app.infrastructure.database.repository import Database
from app.infrastructure.execution import persistent_executor
from app.infrastructure.identity import hash_password, verify_password
from app.infrastructure.storage import SvgArtifactStore
from app.settings import Settings


def create_app(*, dsn=None, origin=None, executor=None):
    dsn = dsn or os.environ["METAPHYS_DATABASE_URL"]
    origin = origin or os.environ["METAPHYS_PUBLIC_ORIGIN"]
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username
    ):
        raise ValueError("METAPHYS_PUBLIC_ORIGIN 必须为 HTTPS 站点源地址")
    origin = origin.rstrip("/")
    if int(os.environ.get("WEB_CONCURRENCY", "1")) != 1:
        raise ValueError("当前部署仅支持单 worker")
    database = Database(dsn)
    runtime = SimpleNamespace(db=database, runs=None, executor=None, artifacts=None)

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            stack.push_async_callback(database.close)
            await database.open()
            active_executor = executor or await stack.enter_async_context(persistent_executor(dsn))
            runtime.executor = active_executor
            from metaphys.tools.builtins.astro_chart import resolve_svg_dir

            runtime.artifacts = SvgArtifactStore(resolve_svg_dir)
            runtime.runs = DurableRuns(database, active_executor, artifacts=runtime.artifacts)
            await runtime.runs.start()
            stack.push_async_callback(runtime.runs.shutdown)
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.durable = runtime
    app.state.container = SimpleNamespace(frontend_dir=lambda: Path(__file__).resolve().parents[2] / "frontend" / "out")
    app.include_router(router)
    app.include_router(frontend.router)
    install_error_handlers(app)

    @app.exception_handler(DatabaseError)
    @app.exception_handler(PoolTimeout)
    async def database_error(request: Request, exc):
        if runtime.runs:
            runtime.runs.ready = False
        return JSONResponse(
            error_body(request, code="unavailable", message="数据服务暂时不可用。", retryable=True), status_code=503
        )

    failed = {}
    slots = asyncio.Semaphore(2)
    dummy = hash_password(secrets.token_urlsafe(32))

    @app.middleware("http")
    async def identity(request, call_next):
        if request.url.path in ("/health/live", "/health/ready"):
            return await call_next(request)
        if request.url.scheme != "https":
            return JSONResponse({"detail": "需要 HTTPS。"}, status_code=400)
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("origin") not in (None, origin):
            return JSONResponse({"detail": "不允许跨站请求。"}, status_code=403)
        peer = request.client.host if request.client else "unknown"
        now = time.monotonic()
        for key, (_, until) in list(failed.items()):
            if until < now:
                del failed[key]
        count, until = failed.get(peer, (0, now + 60))
        if count >= 20 or len(failed) >= 1024:
            return JSONResponse(
                {"detail": "登录请求较多，请稍后重试。"}, status_code=429, headers={"Retry-After": "60"}
            )
        username = password = ""
        header = request.headers.get("authorization", "")
        if header.startswith("Basic ") and len(header) < 4096:
            with suppress(ValueError, UnicodeError):
                username, password = base64.b64decode(header[6:], validate=True).decode().split(":", 1)
        # Database outages must not get mistaken for bad credentials.
        try:
            row = await database.authenticate(username) if len(username) <= 64 else None
        except (DatabaseError, PoolTimeout):
            return JSONResponse({"detail": "数据服务暂时不可用。"}, status_code=503)
        async with slots:
            valid = await asyncio.to_thread(verify_password, password, row["password_hash"] if row else dummy)
        if not row or not valid:
            failed[peer] = (count + 1, until)
            return JSONResponse(
                {"detail": "请使用受邀账号登录。"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Metaphys", charset="UTF-8"'},
            )
        request.state.owner = username
        return await call_next(request)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[parsed.hostname, "localhost", "127.0.0.1"])
    install_middleware(app, Settings())
    return app
