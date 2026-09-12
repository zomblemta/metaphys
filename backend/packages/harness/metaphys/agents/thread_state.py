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


def merge_charts(
    existing: dict[str, dict[str, Any]] | None,
    new: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """按 ``kind`` 覆盖合并命盘。

    ``{"bazi": {...}}`` 合并后仍是单条 —— 重排盘的语义是"替换"，不是"再来一张"。
    新增盘种（M3 的星盘）只需用新的 kind，互不干扰。
    """
    merged = dict(existing or {})
    for kind, chart in (new or {}).items():
        # 空值不覆盖：reducer 可能被传入空 dict 表示"无变更"，
        # 若照单全收会把已有的好盘抹成空。
        if chart:
            merged[kind] = chart
    return merged


class ThreadState(AgentState):
    """会话状态。

    除 ``messages`` 外的通道都是**给前端与审计用的**，不参与模型推理 ——
    模型只通过 ToolMessage 读到命盘。
    """

    #: 会话标题。首轮之后由总结生成，M2 只留通道。
    title: NotRequired[str | None]

    #: 最近一次排盘所用的出生信息（回显用户输入，并给后续工具调用复用）。
    birth_profile: NotRequired[dict[str, Any] | None]

    #: 已排出的命盘，按盘种索引：``{"bazi": {...}}``。
    charts: Annotated[dict[str, dict[str, Any]], merge_charts]

    #: Grounding 核验命中记录 —— 模型输出里出现了盘上没有的干支/神煞。
    grounding_flags: Annotated[list[dict[str, Any]], operator.add]

    #: 安全合规标记。M5 才实现判定逻辑，M2 只留通道。
    safety_flags: Annotated[list[str], operator.add]


__all__ = ["ThreadState", "merge_charts"]
