"""应用层与基础设施之间的边界（端口）。

上行：``application/`` 只依赖这里定义的 Protocol，不认识具体实现。
下行：``infrastructure/`` 实现它们。``app/bootstrap.py`` 负责把两边接起来。

## 为什么是 Protocol 而不是 ABC

这些协议是**结构性的**：集成测试会注入鸭子类型的替身
（``SimpleNamespace(_graph=None)``、只实现一个方法的裸类），它们没有继承
任何东西也照样满足协议。因此：

- **不要** ``isinstance(x, SomePort)`` 做校验；
- **不要**给协议加 ``@runtime_checkable`` 然后拿它当门禁。

Protocol 在这里的用途是让读者与类型检查器看清边界在哪，不是运行时守卫。

## 阶段 A 的落地情况

每个端口只有一个实现（全在 ``app/infrastructure/``，全部内存），所以文档
§3 目录里的 ``database/``、``events/``、``identity/`` 三个子包推迟到阶段 B：
届时用 SQLAlchemy 仓储替换 ``memory.py``，**端口本身不动**。``MessageRepository``
与 ``UnitOfWork`` 同理 —— 没有第二个实现、没有真实事务时先不引入。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Protocol

from app.domain.models import Conversation, Run, RunEvent, Session

# --------------------------------------------------------------------------- #
# 身份与会话
# --------------------------------------------------------------------------- #


class SessionRepository(Protocol):
    """会话聚合的仓储：会话自己拥有它的 conversation 集合。

    阶段 A 里没有独立的 ``principals`` 表 —— 会话就是所有者，所有权校验
    因此收在 ``owned()`` 一处，而不是散在每个路由里。
    """

    def find(self, token: str | None) -> Session | None:
        """按 Cookie token 取会话；不存在或已过期返回 ``None``。"""
        ...

    def create(self) -> Session:
        """签发新会话。超出容量抛 ``SessionCapacity``。"""
        ...

    def owned(self, session: Session, conversation_id: str) -> Conversation:
        """取会话拥有的 conversation；不存在或不属于它抛 ``NotFound``。"""
        ...

    def add_conversation(self, session: Session) -> Conversation:
        """在会话下新建 conversation。超出上限抛 ``ConversationLimit``。"""
        ...

    def remove_conversation(self, session: Session, conversation_id: str) -> None:
        """从会话移除 conversation（同会话内的其它 conversation 不受影响）。"""
        ...

    def discard(self, session: Session) -> None:
        """整段丢弃一个会话（过期回收用）。"""
        ...

    @property
    def sessions(self) -> dict[str, Session]:
        """底层映射。仅用于过期清扫与容量断言。"""
        ...


# --------------------------------------------------------------------------- #
# 运行与事件
# --------------------------------------------------------------------------- #


class RunRepository(Protocol):
    """运行记录。"""

    def add(self, run: Run) -> Run: ...

    def get(self, run_id: str) -> Run | None: ...

    def active_for(self, conversation_id: str) -> Run | None:
        """该会话当前活跃（queued/running）的 Run，没有则 ``None``。"""
        ...

    def discard(self, conversation_id: str) -> None:
        """逐出该会话的全部运行记录（删除会话时用）。"""
        ...


class RunEventLog(Protocol):
    """公开事件的追加日志 + 订阅。

    ``(run_id, seq)`` 唯一且 ``seq`` 从 1 连续。**数据库才是重放的依据**
    （阶段 B）；这里的实现只在进程内有效，但接口一致，替换时应用层不动。
    事件先入库再通知：进程内通知只是低延迟优化。
    """

    def append(self, run_id: str, type: str, data: dict[str, Any]) -> RunEvent:
        """追加一条事件并唤醒订阅者。``seq`` 由日志分配。"""
        ...

    def after(self, run_id: str, seq: int = 0) -> list[RunEvent]:
        """重放：返回序号大于 ``seq`` 的全部事件（已保留的部分）。"""
        ...

    async def next_event(self, run_id: str, seq: int) -> RunEvent | None:
        """等待序号大于 ``seq`` 的下一条事件；日志已终结且读完时返回 ``None``。

        返回 ``None`` 是订阅循环的**唯一**退出条件 —— 因此终结时必须
        ``close()``，否则订阅者会一直挂到进程结束。
        """
        ...

    def close(self, run_id: str) -> None:
        """标记该 Run 不再有新事件，唤醒所有等待者。"""
        ...

    def discard(self, conversation_id: str, run_ids: list[str]) -> None:
        """逐出这些 Run 的事件（删除会话时用）。"""
        ...


# --------------------------------------------------------------------------- #
# 执行与制品
# --------------------------------------------------------------------------- #


class AgentExecutor(Protocol):
    """驱动 agent 图。

    这是应用层与 harness 之间**唯一**的接触面。路由不再读 ``runner._graph``、
    不再直接操作 checkpointer —— 那些细节收在 ``infrastructure/execution.py``。
    """

    def execute(self, *, run_id: str, thread_id: str, message: str) -> AsyncIterator[dict[str, Any]]:
        """跑一轮，逐个产出 harness 事件。

        ``run_id`` 由应用生成并贯穿 harness、日志、审计与事件，不能两层各生成一个。
        """
        ...

    async def delete_execution(self, thread_id: str) -> None:
        """删除该线程的 checkpoint。"""
        ...


class ArtifactStore(Protocol):
    """受控文件存储（阶段 A 只有星盘 SVG）。

    应用以 opaque 路径定位，绝不由用户拼接路径。目录按**每次调用**解析：
    配置可能被重载，而且测试需要在构造之后替换它。
    """

    def svg_directory(self) -> Path: ...

    def locate(self, chart: dict[str, Any]) -> Path | None:
        """按命盘内容定位文件；不存在或越界返回 ``None``。"""
        ...


#: 目录提供者。抽成类型别名只是为了在 ``bootstrap`` 里读起来短一点。
DirectoryProvider = Callable[[], Path]


__all__ = [
    "AgentExecutor",
    "ArtifactStore",
    "DirectoryProvider",
    "RunEventLog",
    "RunRepository",
    "SessionRepository",
]
