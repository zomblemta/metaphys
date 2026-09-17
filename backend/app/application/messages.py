"""对外文案 —— 一处定义，多处引用。

放在这里而不是散在各个 router 里，是因为同一条文案出现在三个地方
（SSE 的 error 帧、会话里的 error 消息、409 的响应体），任何一处改了
另一处没改，用户就会看到两种说法。

**这里只放通用文案。** 供应商异常文本、原始输入、服务端路径一律不进
这些字符串 —— 失败被包装成通用消息，细节留在服务端日志里。
"""

from __future__ import annotations

#: 一轮运行失败（异常、缺 final、超时）对用户说的话。
#: 与 M4 的文案逐字节相同：前端按这句判断"需要新建会话"，改了就是破坏契约。
GENERIC_RUN_ERROR = "本轮解读未完成。为避免使用不完整的对话状态，请新建会话后再试。"

#: 会话正在解读时拒绝新的运行。
BUSY_RUN = "当前会话正在解读，请稍候。"

#: 正在解读时拒绝删除。
BUSY_DELETE = "正在解读，请完成后再删除。"

#: 会话已过期。
SESSION_EXPIRED = "会话已过期，请刷新页面。"

#: CSRF 校验失败。
CSRF_FAILED = "会话校验失败，请刷新页面。"

#: 会话不存在或不属于当前身份。两种情况共用一句，不泄漏 id 是否存在。
CONVERSATION_MISSING = "会话不存在或已过期。"

#: 达到轮次上限（40 轮 = 80 条消息）。
ROUND_LIMIT = "本次会话已达到 40 轮，请新建会话。"

#: 输入长度与格式。Pydantic 的默认响应会回显 input，这里换成不回显的固定文案。
INVALID_MESSAGE = "消息格式不正确，请输入 1–4000 字的文本。"

__all__ = [
    "BUSY_DELETE",
    "BUSY_RUN",
    "CONVERSATION_MISSING",
    "CSRF_FAILED",
    "GENERIC_RUN_ERROR",
    "INVALID_MESSAGE",
    "ROUND_LIMIT",
    "SESSION_EXPIRED",
]
