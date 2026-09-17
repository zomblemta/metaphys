"""业务实体与运行状态机 —— 不依赖 Web 框架、仓储或 harness。

这个模块只描述"业务上有什么"以及"运行可以从哪个状态走到哪个状态"。
怎么存（内存 / PostgreSQL）、怎么暴露（旧 /api 还是 /api/v1）都不在这里。

## 阶段 A 的两处投影

``Conversation.busy`` / ``.failed`` 是**阶段 A 兼容投影**：权威状态是
:class:`Run` 的 ``status``，这两个布尔量只是为了不改动前端契约与既有测试
（``tests/test_gateway.py`` 直接对它们赋值）。二者只由
``app/application/runs.py`` 的三个函数同时写入，不会各写各的。阶段 B 改为
由 ``active_run_id`` + ``Run.status`` 派生后即可删除。

``Conversation.result`` 与 ``Run.result`` 指向同一次提交，同理。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from app.domain.errors import IllegalTransition

# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #


@dataclass
class Conversation:
    """一次解读会话。

    ``messages`` 是用户可见历史，``result`` 是最近一轮的公开结果摘要。
    ``created_at`` / ``updated_at`` 是 UTC epoch 秒 —— 应用时间戳存 UTC，
    与出生钟表时间的"本地时间语义"是两件事，不能混。
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    title: str = "新的解读"
    messages: list[dict[str, str]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    # 阶段 A 兼容投影，见模块 docstring。
    busy: bool = False
    failed: bool = False
    active_run_id: str | None = None
    latest_run_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()


@dataclass
class Session:
    """浏览器会话。``token`` 只在 Cookie 里出现，不进 URL、不进日志。"""

    token: str
    csrf: str
    touched: float = field(default_factory=time.monotonic)
    conversations: dict[str, Conversation] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 运行
# --------------------------------------------------------------------------- #


class RunStatus(StrEnum):
    """一轮运行的状态。

    成功运行可以返回 completed / withheld / safety_redirect —— 那是
    ``response_status``，属于**结果**，与运行状态是两套词汇，不要混用。
    追问同样是一轮成功结束，不是持续占用 worker 的"等待用户"。
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


#: 仍在占用 worker 的状态。准入时用它判断"同会话是否已有活跃 Run"。
ACTIVE_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.RUNNING})

#: 终态。到达其中之一后不再迁移。
TERMINAL_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELLED})

#: 合法迁移表。写成数据而不是散落的 if，是为了让"取消与完成竞争由谁赢"
#: 这类问题只在一处回答。
_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELLED}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.TIMED_OUT: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


@dataclass
class Run:
    """一轮独立运行。

    结果固定在**该轮**的 ``result`` 上，不从会话最新 checkpoint 冒充历史结果。
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    conversation_id: str = ""
    status: RunStatus = RunStatus.QUEUED
    input: str = ""
    result: dict[str, Any] | None = None
    error_code: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def transition(run: Run, target: RunStatus) -> None:
    """把 ``run`` 迁移到 ``target``，非法迁移抛 :class:`IllegalTransition`。

    非法的情形只有两种：从未知状态出发，或从终态再迁移。后者是真正的
    风险 —— "取消成功后不得再发布成功 final"就靠它拦住。
    """
    allowed = _ALLOWED_TRANSITIONS.get(run.status, frozenset())
    if target not in allowed:
        raise IllegalTransition(f"运行状态不能从 {run.status} 变为 {target}")
    run.status = target
    if target is RunStatus.RUNNING and run.started_at is None:
        run.started_at = time.time()
    if target in TERMINAL_STATUSES:
        run.finished_at = time.time()


# --------------------------------------------------------------------------- #
# 事件
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunEvent:
    """允许公开的运行事件。

    只存"允许公开"的事件：模型正文在核验通过前不进这里（见 ``run.final``
    的提交顺序）。``(run_id, seq)`` 唯一，``seq`` 从 1 开始连续 —— SSE 的
    ``Last-Event-ID`` 游标就是它，重放因此是"按下标续读"而不是"重新跑一遍"。
    """

    run_id: str
    seq: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        """线上形状。字段顺序固定，便于人读日志。"""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "seq": self.seq,
            "type": self.type,
            "data": self.data,
        }

    def cursor(self) -> str:
        """SSE ``id:`` 的编码：``<run_id>:<seq>``。

        带上 run_id 才能在重放时校验"这个游标属于这一轮" —— 否则客户端把
        另一个 Run 的序号带过来，就会静默跳过或重放错的事件。
        """
        return f"{self.run_id}:{self.seq}"


def parse_cursor(cursor: str) -> tuple[str, int]:
    """解析 SSE 游标。非法格式抛 :class:`ValueError`（由调用方转 400）。"""
    run_id, _, raw = cursor.partition(":")
    if not run_id or not raw.isdecimal():
        raise ValueError(f"非法事件游标：{cursor!r}")
    return run_id, int(raw)


__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "Conversation",
    "Run",
    "RunEvent",
    "RunStatus",
    "Session",
    "parse_cursor",
    "transition",
]
