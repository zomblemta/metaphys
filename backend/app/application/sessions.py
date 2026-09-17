"""会话引导与过期回收。

过期回收必须**先**做，再 find-or-create —— 顺序反了，一个刚被判定过期的
会话会因为 find() 顺手刷新 ``touched`` 而复活，容量就永远回收不回来。

回收时不判 ``busy``：清扫的入选条件已经排除了有活跃运行的会话（见
``SessionStore.find`` 的注释）。在清理路径上再加一道 busy 判断，会让"会话
过期但一直有运行"的泄漏无法回收。
"""

from __future__ import annotations

import time

from app.domain.models import Session


class SessionService:
    """会话生命周期。"""

    def __init__(self, *, sessions, runs, events, executor) -> None:
        self._sessions = sessions
        self._runs = runs
        self._events = events
        self._executor = executor

    async def bootstrap(self, token: str | None) -> Session:
        """取当前会话；没有就签发一个新的。"""
        await self.sweep()
        return self._sessions.find(token) or self._sessions.create()

    async def sweep(self) -> int:
        """回收过期会话，返回回收数量。"""
        expired = [
            session
            for session in list(self._sessions.sessions.values())
            if time.monotonic() - session.touched >= self._sessions.ttl
            and not any(c.busy for c in session.conversations.values())
        ]
        for session in expired:
            await self.purge(session)
        return len(expired)

    async def purge(self, session: Session) -> None:
        """丢弃一个会话及其全部运行痕迹。

        删除会话时也要清掉 checkpoint 与事件 —— 只从字典里 pop 掉，会让
        被删会话的运行仍可按 run_id 查到，"删除"就成了假动作。
        """
        for conversation in list(session.conversations.values()):
            await self._executor.delete_execution(conversation.id)
            run_ids = self._runs.ids_for(conversation.id)
            self._runs.discard(conversation.id)
            self._events.discard(run_ids)
        self._sessions.discard(session)


__all__ = ["SessionService"]
