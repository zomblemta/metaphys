"""领域错误 —— 不依赖任何 Web 框架。

原来的仓储直接 ``raise HTTPException``，于是"存会话"这个动作反过来决定了
HTTP 状态码：换一个入口（CLI、后台任务、集成测试）就必须先伪造一个 Request
才能表达"没找到"。这里只描述**业务上发生了什么**，状态码到 HTTP 的映射
收在 ``app/gateway/errors.py`` 的一张表里。

``code`` 是稳定标识（给客户端判断分支），``message`` 是给用户看的文案。
两者都不含内部细节 —— 供应商异常、原始输入、服务端路径一律不出现在这里。
"""

from __future__ import annotations


class DomainError(Exception):
    """业务错误基类。"""

    code = "error"
    retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Unauthorized(DomainError):
    """没有有效身份。"""

    code = "unauthorized"


class Forbidden(DomainError):
    """有身份，但这次操作不被允许（例如 CSRF 校验失败）。"""

    code = "forbidden"


class NotFound(DomainError):
    """资源不存在，或不属于当前身份。

    两种情况合并成同一个 code，是故意的：区分它们会把"这个 id 是否存在"
    泄漏给非所有者。
    """

    code = "not_found"


class Conflict(DomainError):
    """与当前状态冲突（例如会话正在解读）。"""

    code = "conflict"


class ConversationLimit(Conflict):
    """会话数量达到上限。与普通 Conflict 分开，因为客户端该做的动作不同：要删旧的。"""

    code = "conversation_limit"


class RunCapacity(DomainError):
    """全局并发已满。retryable，客户端稍后重试即可。"""

    code = "run_capacity"
    retryable = True


class SessionCapacity(DomainError):
    """会话总数达到上限，无法再签发新会话。"""

    code = "session_capacity"
    retryable = True


class Unavailable(DomainError):
    """依赖缺失导致该功能暂时不可用（例如前端还没构建）。

    与 :class:`NotImplementedYet` 的区别是**谁的问题**：501 表示这个能力
    我们还没做，重试无用；503 表示能力是有的，只是这份部署还没准备好，
    补上缺失的东西就好了。
    """

    code = "unavailable"
    retryable = True


class InvalidInput(DomainError):
    """输入不合法。"""

    code = "invalid_input"


class NotImplementedYet(DomainError):
    """已登记但未实现的能力。

    比"静默忽略"好：客户端以为受保护（例如幂等键）而实际没有，是比明确拒绝
    更糟的结果。
    """

    code = "not_implemented"


class IllegalTransition(DomainError):
    """运行状态机的非法迁移。这是程序错误，不是用户输入错误。"""

    code = "illegal_transition"


__all__ = [
    "Conflict",
    "ConversationLimit",
    "DomainError",
    "Forbidden",
    "IllegalTransition",
    "InvalidInput",
    "NotFound",
    "NotImplementedYet",
    "RunCapacity",
    "SessionCapacity",
    "Unauthorized",
    "Unavailable",
]
