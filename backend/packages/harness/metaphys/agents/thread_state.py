"""图状态 —— 模型与前端共读的那一份数据。

`create_agent` 默认的 `AgentState` 只有 `messages`。这里加的四条通道各自服务
一个消费者，**关键在 reducer**：

- ``charts`` 用 :func:`merge_charts`（**按 kind 覆盖**）而非追加。同一 thread 里
  用户改了出生时间重排盘，前端要看到新盘；若走 `operator.add` 就会得到两份
  命盘并排，模型引用哪一份都可能是过期的。
- ``messages`` 走 `add_messages`（`AgentState` 已带），给模型看。
- ``grounding_flags`` / ``safety_flags`` 走追加 —— 它们是**审计流水**，
  历史命中记录不能被后来的默认值抹掉。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, NotRequired

from langchain.agents import AgentState


def chart_profile_key(chart: dict[str, Any]) -> tuple[Any, ...] | None:
    """当前出生资料的版本标识。八字使用引擎转换后的公历钟表时间。"""
    profile = chart.get("profile") or {}
    if not profile:
        return None
    moment = chart.get("clock_time") or profile.get("birth_datetime")
    return (
        moment,
        profile.get("latitude"),
        profile.get("longitude"),
        profile.get("time_accuracy"),
        profile.get("name", ""),
    )


def merge_charts(
    existing: dict[str, dict[str, Any]] | None,
    new: dict[str, dict[str, Any] | None] | None,
) -> dict[str, dict[str, Any]]:
    """按 ``kind`` 覆盖合并命盘。

    ``{"bazi": {...}}`` 合并后仍是单条 —— 重排盘的语义是"替换"，不是"再来一张"。
    相同出生资料的盘种共存；出生资料变化时其它盘失效。None 可显式删除某种盘。
    """
    merged = dict(existing or {})
    for kind, chart in (new or {}).items():
        # None 是显式失效；空 dict 仍代表无变更。
        if chart is None:
            merged.pop(kind, None)
        elif chart:
            key = chart_profile_key(chart)
            if key is not None:
                merged = {k: v for k, v in merged.items() if chart_profile_key(v) == key}
            merged[kind] = chart
    return merged


class ThreadState(AgentState):
    """会话状态。

    命盘同时用于前端、核验和每次模型调用的当前有效盘上下文；
    历史 ToolMessage 保留用于追溯，不能视为当前盘。
    """

    #: 会话标题。首轮之后由总结生成，M2 只留通道。
    title: NotRequired[str | None]

    #: 最近一次排盘所用的出生信息（回显用户输入，并给后续工具调用复用）。
    birth_profile: NotRequired[dict[str, Any] | None]

    #: 已排出的命盘，按盘种索引：``{"bazi": {...}}``。
    charts: Annotated[dict[str, dict[str, Any]], merge_charts]

    #: Grounding 核验命中记录 —— 模型输出里出现了盘上没有的干支/神煞。
    grounding_flags: Annotated[list[dict[str, Any]], operator.add]

    #: 安全规则审计历史。当前答复标记由 AIMessage 的 safety_categories 提取。
    safety_flags: Annotated[list[str], operator.add]


__all__ = ["ThreadState", "merge_charts"]
