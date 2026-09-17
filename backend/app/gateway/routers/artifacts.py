"""受控制品下载。

只有 ``/api/conversations/{id}/chart.svg`` 一条，**不**另造 v1 版本：文档
把制品接口的最终契约（元数据、授权、删除补偿）排在阶段 D，现在就给它一个
``/api/v1`` 路径，等于把一个即将被替换的形状钉成"规范接口"。

响应头里的 CSP 是**给文件本身**的：SVG 是能带脚本的 XML，用
``sandbox; default-src 'none'`` 让它即使被直接打开也执行不了任何东西。
中间件那一条是全站默认，这里是针对这类文件收紧的补充。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.gateway.dependencies import OwnerDep, container

router = APIRouter(tags=["artifacts"])

#: 直接打开 SVG 时不给它任何能力：不用 unsafe-inline 是因为本服务生成的
#: 星盘图不依赖内联样式脚本，而放开它正好是 SVG 注入想要的那一项。
_SVG_POLICY = "sandbox; default-src 'none'; style-src 'unsafe-inline'"


@router.get("/api/conversations/{conversation_id}/chart.svg")
async def chart_image(conversation_id: str, request: Request, session: OwnerDep) -> FileResponse:
    path = container(request).artifact_service.chart_svg(session, conversation_id)
    return FileResponse(
        path,
        media_type="image/svg+xml",
        headers={"Content-Security-Policy": _SVG_POLICY},
    )


__all__ = ["router"]
