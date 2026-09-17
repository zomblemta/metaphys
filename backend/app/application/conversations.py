"""会话的创建、查询与删除。

删除是本模块唯一有分支的方法，顺序不能调换：**所有权 → 活跃运行 → 清理**。
先清 checkpoint 再判活跃运行，删到一半发现删不了，用户的会话就处在既没删掉
也没保住的状态。所以这里逐条判完再动手，任何一条不过就整体拒绝。
"""

from __future__ import annotations

from app.application import messages
from app.application.runs import has_active_run
from app.application.views import conversation_page, decode_cursor, message_page
from app.domain.errors import Conflict
from app.domain.models import Conversation, Session


class ConversationService:
    """会话的增删查。"""

    def __init__(self, *, sessions, runs, events, executor) -> None:
        self._sessions = sessions
        self._runs = runs
        self._events = events
        self._executor = executor

    def create(self, session: Session) -> Conversation:
        return self._sessions.add_conversation(session)

    def get(self, session: Session, conversation_id: str) -> Conversation:
        return self._sessions.owned(session, conversation_id)

    def summary(self, session: Session) -> list[Conversation]:
        """旧接口的列表：最新创建的在最前。"""
        return list(reversed(list(session.conversations.values())))

    def page(self, session: Session, *, cursor: str | None, limit: int) -> tuple[list[dict], str | None]:
        """v1 的游标分页。游标非法抛 ``ValueError``（由路由转 400）。"""
        parsed = decode_cursor(cursor) if cursor else None
        return conversation_page(list(session.conversations.values()), cursor=parsed, limit=limit)

    def messages(self, session: Session, conversation_id: str, *, cursor: int, limit: int) -> dict:
        return message_page(self.get(session, conversation_id), cursor=cursor, limit=limit)

    async def delete(self, session: Session, conversation_id: str) -> None:
        """删除会话。

        正在解读时拒绝（409）而不是先取消再删：取消与完成的竞争归属属阶段 C，
        在这里补一个"顺手取消"会造出第二套取消语义 —— 而且用户看到的
        "已删除"会和后台仍在消耗的模型调用对不上。
        """
        conversation = self._sessions.owned(session, conversation_id)

        if has_active_run(conversation, self._runs):
            raise Conflict(messages.BUSY_DELETE)

        await self._executor.delete_execution(conversation.id)
        run_ids = self._runs.ids_for(conversation.id)
        self._runs.discard(conversation.id)
        self._events.discard(run_ids)
        self._sessions.remove_conversation(session, conversation_id)


__all__ = ["ConversationService"]
