"""内存实现：会话、运行记录、公开事件日志。

阶段 A 只有内存一种实现，所以文档 §3 目录里的 ``database/`` / ``events/`` /
``identity/`` 三个子包先合并到这里。它们的**端口**（``application/ports.py``）
已经固定，阶段 B 换成 SQLAlchemy 仓储时，``application/`` 一行都不用改。

三处必须一起理解的约束：

1. **会话自己拥有 conversation**。阶段 A 没有 ``principals`` 表，所有权校验
   收在 ``owned()`` 一处，而不是散在每个路由里 —— "跨 owner 全资源拒绝"
   因此是结构性的，不依赖谁记得加判断。
2. **事件日志是订阅的唯一依据**。执行与订阅解耦：浏览器断开只停止订阅，
   任务照常跑完并落库。``close()`` 必须在终态调用，否则订阅者会一直挂着。
3. **``Run`` 是权威状态，``Conversation.busy/failed`` 是投影**。准入判断
   两边都读，见 ``application/runs.py``。
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

from app.domain.errors import ConversationLimit, NotFound, SessionCapacity
from app.domain.models import Conversation, Run, RunEvent, Session


class SessionStore:
    """进程内会话存储。

    保留 ``sessions`` / ``ttl`` / ``max_*`` 这些公开属性，是因为容量与过期
    这类行为只能在真实存储上验证，测试需要够得着它们。
    """

    def __init__(self, *, ttl: float = 86400, max_sessions: int = 128, max_conversations: int = 20):
        self.ttl = ttl
        self.max_sessions = max_sessions
        self.max_conversations = max_conversations
        self.sessions: dict[str, Session] = {}

    def find(self, token: str | None) -> Session | None:
        """按 token 取会话。

        有一个**故意**的例外：只要会话里还有正在解读的 conversation，就不算
        过期。否则用户排盘排到一半去泡杯茶，回来连恢复结果的资格都没了 ——
        而那一轮的服务端成本已经花掉了。
        """
        session = self.sessions.get(token or "")
        if session and (
            time.monotonic() - session.touched < self.ttl or any(c.busy for c in session.conversations.values())
        ):
            session.touched = time.monotonic()
            return session
        return None

    def create(self) -> Session:
        if len(self.sessions) >= self.max_sessions:
            raise SessionCapacity("当前会话较多，请稍后再试。")
        session = Session(token=secrets.token_urlsafe(32), csrf=secrets.token_urlsafe(32))
        self.sessions[session.token] = session
        return session

    def owned(self, session: Session, conversation_id: str) -> Conversation:
        """取该会话拥有的 conversation。

        不存在与不属于它返回**同一个** NotFound：区分二者会把"这个 id 是否存在"
        泄漏给非所有者。
        """
        conversation = session.conversations.get(conversation_id)
        if conversation is None:
            raise NotFound("会话不存在或已过期。")
        return conversation

    def add_conversation(self, session: Session) -> Conversation:
        if len(session.conversations) >= self.max_conversations:
            raise ConversationLimit("最多保留 20 个会话，请先删除不需要的会话。")
        conversation = Conversation()
        session.conversations[conversation.id] = conversation
        return conversation

    def remove_conversation(self, session: Session, conversation_id: str) -> None:
        session.conversations.pop(conversation_id, None)

    def discard(self, session: Session) -> None:
        self.sessions.pop(session.token, None)


class RunRepository:
    """进程内运行记录。"""

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def add(self, run: Run) -> Run:
        self._runs[run.id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def active_for(self, conversation_id: str) -> Run | None:
        """该会话当前活跃的 Run。

        阶段 A 用线性扫描：同会话最多一个活跃 Run，且运行记录随会话删除而
        逐出，规模有上界。阶段 C 换成数据库上的部分唯一索引。
        """
        for run in self._runs.values():
            if run.conversation_id == conversation_id and run.is_active:
                return run
        return None

    def ids_for(self, conversation_id: str) -> list[str]:
        return [run.id for run in self._runs.values() if run.conversation_id == conversation_id]

    def active(self) -> list[Run]:
        """全部活跃运行。关停时用它兜底结算。"""
        return [run for run in self._runs.values() if run.is_active]

    def discard(self, conversation_id: str) -> None:
        for run_id in self.ids_for(conversation_id):
            del self._runs[run_id]


@dataclass
class _Log:
    """单个 Run 的事件日志。"""

    events: list[RunEvent] = field(default_factory=list)
    closed: bool = False
    #: 每个订阅者一个 Event。共用一个是错的：先醒来的那个 ``clear()`` 会把
    #: 还在等待的其它订阅者一起弄丢。
    waiters: set[asyncio.Event] = field(default_factory=set)

    def wake(self) -> None:
        for waiter in self.waiters:
            waiter.set()


class RunEventLog:
    """进程内事件日志 + 订阅。

    ``seq`` 从 1 开始连续分配，``(run_id, seq)`` 唯一。事件先入库再通知：
    通知只是低延迟优化，**重放的依据永远是日志本身**。
    """

    def __init__(self) -> None:
        self._logs: dict[str, _Log] = {}

    def append(self, run_id: str, type: str, data: dict) -> RunEvent:
        log = self._logs.setdefault(run_id, _Log())
        event = RunEvent(run_id=run_id, seq=len(log.events) + 1, type=type, data=data)
        log.events.append(event)
        log.wake()
        return event

    def after(self, run_id: str, seq: int = 0) -> list[RunEvent]:
        """重放：序号大于 ``seq`` 的全部事件。"""
        log = self._logs.get(run_id)
        if log is None:
            return []
        return [event for event in log.events if event.seq > seq]

    async def next_event(self, run_id: str, seq: int) -> RunEvent | None:
        """等待下一条事件；已终结且读完时返回 ``None``。

        注册等待者**之后**必须再查一次日志：注册与首次查询之间发生的 append
        不会唤醒任何人，漏掉这一次复查就是一个永不返回的订阅。
        """
        log = self._logs.get(run_id)
        if log is None:
            return None

        while True:
            for event in log.events:
                if event.seq > seq:
                    return event
            if log.closed:
                return None

            waiter = asyncio.Event()
            log.waiters.add(waiter)
            try:
                if log.closed or any(event.seq > seq for event in log.events):
                    continue
                await waiter.wait()
            finally:
                log.waiters.discard(waiter)

    def close(self, run_id: str) -> None:
        """标记不再有新事件并唤醒所有等待者。终态必须调用。"""
        log = self._logs.get(run_id)
        if log is not None:
            log.closed = True
            log.wake()

    def discard(self, run_ids: list[str]) -> None:
        """逐出事件。先 ``close()`` 再删，避免正在等待的订阅者永远挂着。"""
        for run_id in run_ids:
            self.close(run_id)
            self._logs.pop(run_id, None)

    def has(self, run_id: str) -> bool:
        return run_id in self._logs


__all__ = ["RunEventLog", "RunRepository", "SessionStore"]
