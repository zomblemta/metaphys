"""静态前端：首页与 Next 构建产物。

两条路由都从容器里取目录，**按请求解析** —— 集成测试在应用构造之后替换
``app.gateway.main.FRONTEND``，构造期固化的路径看不见那次替换。

首页每次读盘并**重算** CSP 哈希：构建产物换了，内联脚本的哈希就变了，
缓存它会让新构建的页面被自己的策略拦下，而症状是"页面白屏、控制台里一条
CSP 报错"，与改动本身看不出关系。
"""

from __future__ import annotations

import base64
import hashlib
import re

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.domain.errors import NotFound, Unavailable
from app.gateway.dependencies import container

router = APIRouter(tags=["frontend"])

_BUILD_HINT = "前端尚未构建，请在 frontend 执行 npm ci 和 npm run build。"


def _inline_script_hashes(html: str) -> list[str]:
    """静态导出的 Next.js hydration 脚本按内容授权，不放开 unsafe-inline。"""
    return [
        "'sha256-" + base64.b64encode(hashlib.sha256(code.encode()).digest()).decode() + "'"
        for code in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.DOTALL)
        if code
    ]


@router.get("/")
async def index(request: Request) -> FileResponse:
    path = container(request).frontend_dir() / "index.html"
    if not path.is_file():
        # 503 而非 404：地址没错，是这份部署还没准备好。
        raise Unavailable(_BUILD_HINT)
    html = path.read_text(encoding="utf-8")
    policy = (
        "default-src 'self'; script-src 'self' "
        + " ".join(_inline_script_hashes(html))
        + "; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return FileResponse(path, headers={"Content-Security-Policy": policy})


@router.get("/_next/{filename:path}")
async def asset(filename: str, request: Request) -> FileResponse:
    directory = (container(request).frontend_dir() / "_next").resolve()
    path = (directory / filename).resolve()
    # 解析后再比前缀：``..`` 与指向外部的符号链接都会在这里被挡掉。
    if not path.is_relative_to(directory) or not path.is_file():
        raise NotFound("资源不存在。")
    return FileResponse(path)


__all__ = ["router"]
