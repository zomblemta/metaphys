"""受控制品的授权读取。

阶段 A 只有一样制品：当前会话最新命盘的 SVG。授权链路是
**会话 → 归属校验 → 该轮结果里的命盘 → 文件**，四步缺一不可。

## 为什么不能凭文件名授权

文件名是出生资料 + 排盘配置的摘要，因此同一份出生资料在不同会话里会得到
同一个文件名。于是"能拼出文件名"不等于"有权读它" —— 归属校验必须在拿到
文件之前完成，且以会话为单位，不以文件为单位。
"""

from __future__ import annotations

from pathlib import Path

from app.domain.errors import NotFound
from app.domain.models import Session


class ArtifactService:
    """按会话定位受控文件。"""

    def __init__(self, *, sessions, store) -> None:
        self._sessions = sessions
        self._store = store

    def chart_svg(self, session: Session, conversation_id: str) -> Path:
        """取当前会话最新星盘图的文件路径。

        两种"没有"给不同的文案：还没有排过星盘，与排过但文件没了。前者用户
        该去排盘，后者只能看文字命盘 —— 合并成一句，用户就不知道该做什么。
        """
        conversation = self._sessions.owned(session, conversation_id)

        chart = ((conversation.result or {}).get("charts") or {}).get("astro")
        if not chart:
            raise NotFound("当前会话没有星盘图。")

        path = self._store.locate(chart)
        if path is None:
            raise NotFound("星盘图不可用，文字命盘仍可查看。")
        return path


__all__ = ["ArtifactService"]
