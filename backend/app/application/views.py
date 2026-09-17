"""线上形状 —— 纯函数，无状态、无策略。

把"返回给客户端的 JSON 长什么样"从路由里抽出来，是因为同一份形状有两个
消费者（``/api/*`` 与 ``/api/v1/*``），也因为它是**最容易悄悄破坏**的东西：
改一个键名，前端不会报错，只会安静地显示不出东西。

因此这里的规则是：**只做字段映射，不做判断**。要不要拒绝、要不要 409，
属于 ``application/`` 的服务层；这里只回答"给定这些实体，JSON 是什么"。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.models import Conversation, Run, RunEvent

# --------------------------------------------------------------------------- #
# 旧 /api/* 形状（M4 契约，逐字节保持）
# --------------------------------------------------------------------------- #


def conversation_summary(conversation: Conversation) -> dict[str, Any]:
    """列表项。``busy`` / ``failed`` 是前端判断轮询与重试按钮的依据。"""
    return {
        "id": conversation.id,
        "title": conversation.title,
        "busy": conversation.busy,
        "failed": conversation.failed,
    }


def conversation_public(conversation: Conversation) -> dict[str, Any]:
    """详情。前端拿到它就直接渲染，不再读 SSE 的 final 载荷。"""
    return {
        **conversation_summary(conversation),
        "messages": conversation.messages,
        "result": conversation.result,
    }


# --------------------------------------------------------------------------- #
# v1 形状
# --------------------------------------------------------------------------- #


def _iso(timestamp: float | None) -> str | None:
    """UTC epoch 秒 → ISO-8601。应用时间戳一律 UTC，不含出生钟表时间语义。"""
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def run_view(run: Run) -> dict[str, Any]:
    """固定于该 Run 的状态与结果，不随会话后续轮次变化。"""
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "status": str(run.status),
        "input": run.input,
        "result": run.result,
        "error_code": run.error_code,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
    }


def conversation_view(conversation: Conversation) -> dict[str, Any]:
    """v1 会话元信息。**不夹带全部消息历史** —— 那是 /messages 的事。"""
    return {
        "id": conversation.id,
        "title": conversation.title,
        # 阶段 A 没有软删除：DELETE 是同步删除并直接返回 204，
        # 因此不存在 deleting 中间态。异步删除与 /deletions 属阶段 D。
        "status": "active",
        "result": conversation.result,
        "active_run_id": conversation.active_run_id,
        "latest_run_id": conversation.latest_run_id,
        "created_at": _iso(conversation.created_at),
        "updated_at": _iso(conversation.updated_at),
    }


def event_view(event: RunEvent) -> dict[str, Any]:
    """SSE ``data:`` 载荷。与 ``RunEvent.as_dict()`` 同源，避免两处各写一遍字段。"""
    return event.as_dict()


def message_page(conversation: Conversation, *, cursor: int, limit: int) -> dict[str, Any]:
    """公开消息分页。游标是消息下标 —— 阶段 A 的历史是内存里的有序列表。

    ``next_cursor`` 为 ``None`` 表示已到末尾。返回的是**新页**，不是累计。
    """
    total = len(conversation.messages)
    start = max(0, min(cursor, total))
    end = min(start + limit, total)
    return {
        "messages": conversation.messages[start:end],
        "next_cursor": end if end < total else None,
        "total": total,
    }


def conversation_page(
    conversations: list[Conversation], *, cursor: tuple[float, str] | None, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    """按 ``updated_at`` 降序的稳定游标分页。

    排序键带上 ``id`` 才能稳定：同一毫秒内更新的两个会话，只按时间排会在
    两次请求间换位置，客户端就会看到重复或漏掉。返回 ``(items, next_cursor)``。
    """
    ordered = sorted(conversations, key=lambda c: (c.updated_at, c.id), reverse=True)
    if cursor is not None:
        ordered = [c for c in ordered if (c.updated_at, c.id) < cursor]
    page = ordered[:limit]
    next_cursor = encode_cursor(page[-1]) if len(ordered) > limit and page else None
    return [conversation_view(c) for c in page], next_cursor


def encode_cursor(conversation: Conversation) -> str:
    """游标编码。不加密 —— 它只含 ``updated_at`` 与已在 URL 里的 id。

    ``repr`` 而不是 ``:.6f``：后者会**四舍五入**，而下一页的过滤是
    ``(updated_at, id) < 游标``。向上舍入会让游标严格大于真实时间戳，于是
    所有时间戳相同的会话都满足条件 —— 包括已经翻过去的那两个。表现是
    "翻页偶尔出现重复"，只在同一时刻创建多个会话时才复现。

    ``repr(float)`` 保证往返精确，这类错误就不会再出现。
    """
    return f"{conversation.updated_at!r}:{conversation.id}"


def decode_cursor(raw: str) -> tuple[float, str]:
    """解析游标。格式非法抛 :class:`ValueError`（由路由转 400）。"""
    timestamp, _, conversation_id = raw.partition(":")
    if not conversation_id:
        raise ValueError(f"非法会话游标：{raw!r}")
    return float(timestamp), conversation_id


# --------------------------------------------------------------------------- #
# 能力清单
# --------------------------------------------------------------------------- #

#: ``GET /api/v1/session`` 汇报的能力。
#:
#: 存在的意义是**让延期可见**：``False`` 的每一项要么在 ``routers/deferred.py``
#: 有对应路由返回 501，要么是 ``multi_worker`` / ``persistence`` 这类非端点能力。
#: ``tests/test_gateway_v1.py`` 交叉校验这两张清单，防止各说各话。
CAPABILITIES: dict[str, Any] = {
    "phase": "A",
    "persistence": "memory",
    "replay": True,
    "cancel": False,
    "idempotency": False,
    "profiles": False,
    "charts": False,
    "artifacts": False,
    "deletions": False,
    "stream_gap": False,
    "multi_worker": False,
}


__all__ = [
    "CAPABILITIES",
    "conversation_page",
    "conversation_public",
    "conversation_summary",
    "conversation_view",
    "decode_cursor",
    "encode_cursor",
    "event_view",
    "message_page",
    "run_view",
]
