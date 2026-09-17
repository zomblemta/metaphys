"""领域层：业务实体、状态机与错误。

只依赖标准库。这里不出现 FastAPI、仓储、harness —— 领域规则要能在没有
Web 框架、没有模型配置的情况下被单独推理与测试。
"""

from app.domain.errors import (
    Conflict,
    ConversationLimit,
    DomainError,
    Forbidden,
    IllegalTransition,
    InvalidInput,
    NotFound,
    NotImplementedYet,
    RunCapacity,
    SessionCapacity,
    Unauthorized,
)
from app.domain.models import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    Conversation,
    Run,
    RunEvent,
    RunStatus,
    Session,
    parse_cursor,
    transition,
)

__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "Conflict",
    "Conversation",
    "ConversationLimit",
    "DomainError",
    "Forbidden",
    "IllegalTransition",
    "InvalidInput",
    "NotFound",
    "NotImplementedYet",
    "Run",
    "RunCapacity",
    "RunEvent",
    "RunStatus",
    "Session",
    "SessionCapacity",
    "Unauthorized",
    "parse_cursor",
    "transition",
]
