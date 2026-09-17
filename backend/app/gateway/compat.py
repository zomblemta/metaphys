"""v1 事件 → M4 旧帧的翻译。纯函数，无状态、无策略。

旧接口不是第二套执行逻辑，它只是同一串 v1 事件的另一种**呈现**。差别全部
收在这一个函数里，因此"两套接口行为一致"是结构上的事实，而不是靠两处代码
写得一样来维持。

## 为什么可以丢掉一半事件

M4 的帧只有三种，而 v1 有八种。丢掉 ``run.accepted`` / ``run.started`` /
``run.cancelled`` / ``run.end`` 对旧客户端是无感的 —— 它们本来就没见过这些
概念。但 ``run.end`` 例外：返回 ``None`` 之后由调用方结束响应，这正好复现
了 M4 "终态帧之后立即 EOF" 的行为，而 ``tests/test_gateway.py`` 里
"最后一个 data 帧是 final/error" 的断言依赖的正是这一点。

## 为什么 data 要重建而不是透传

v1 的 ``run.progress`` 带 ``node``（诊断用），旧帧的 ``data`` 必须**恰好**
是 ``{"status":"running"}`` —— 既有测试逐字比对它。透传会把诊断字段泄漏到
旧契约里，而这种改动的表现是前端某处安静地不再更新，不报错。
"""

from __future__ import annotations

from typing import Any

from app.application import messages
from app.domain.models import RunEvent

#: 会被翻译的 v1 事件类型。其余一律丢弃。
_TRANSLATED = frozenset({"run.progress", "run.final", "run.failed"})


def legacy_frame(event: RunEvent) -> dict[str, Any] | None:
    """翻译一条 v1 事件；该轮次对旧客户端没有内容时返回 ``None``。

    ``run_id`` 是**增量**加上的：旧客户端不读它，但它让"用户报错的那一次"
    在浏览器网络面板里就能和日志对上，而字段只增不改不会破坏任何旧读取方。
    """
    if event.type not in _TRANSLATED:
        return None

    if event.type == "run.progress":
        return {
            "event": "update",
            "schema_version": 1,
            "run_id": event.run_id,
            "node": event.data.get("node", ""),
            "data": {"status": "running"},
        }

    if event.type == "run.final":
        return {
            "event": "final",
            "schema_version": 1,
            "run_id": event.run_id,
            "data": event.data,
        }

    # run.failed：只带通用文案。失败原因（供应商异常、路径、原始输入）
    # 留在服务端日志，旧接口从来不回显它们。
    return {
        "event": "error",
        "schema_version": 1,
        "run_id": event.run_id,
        "data": {"message": messages.GENERIC_RUN_ERROR},
    }


__all__ = ["legacy_frame"]
