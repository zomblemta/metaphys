"""工具异常兜底 —— 让未预期的失败变成一条可读消息，而不是整轮崩掉。

**这是兜底，不是主路径。** 工具自身已把可预期的失败（地名歧义、时辰未知、
农历闰月标错）转成"未排盘 + 建议提问"返回；能落到这里的都是没预料到的
缺陷或依赖故障。把它拦住的意义是：一次工具崩溃不该让用户丢掉整轮对话，
模型也还能向用户解释"这项查询现在用不了"。

必须放在中间件列表**第一位** —— ``wrap_tool_call`` 中第一个即最外层，
只有包在所有中间件之外才能兜住它们抛出的异常。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

logger = logging.getLogger(__name__)


class ToolErrorHandlingMiddleware(AgentMiddleware):
    """捕获工具执行中的未预期异常，转成 ``status="error"`` 的 ToolMessage。"""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        try:
            return handler(request)
        except GraphBubbleUp:
            # 控制流信号（interrupt / 跳转），不是错误 —— 吞掉会让中断机制失效。
            raise
        except Exception as err:  # noqa: BLE001 —— 兜底本就该抓全部
            return self._as_error_message(request, err)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as err:  # noqa: BLE001
            return self._as_error_message(request, err)

    @staticmethod
    def _as_error_message(request: ToolCallRequest, err: Exception) -> ToolMessage:
        tool_name = request.tool_call.get("name", "未知工具")
        logger.exception("工具 %s 执行失败", tool_name, exc_info=err)
        return ToolMessage(
            content=(
                f"工具 {tool_name} 执行失败：{type(err).__name__}: {err}。"
                f"这不是用户输入的问题。请换一种方式完成，或如实告诉用户该项查询暂时不可用 —— "
                f"不要据此推测任何命盘数据。"
            ),
            tool_call_id=request.tool_call.get("id", ""),
            status="error",
            artifact={"status": "error", "error_type": type(err).__name__},
        )


__all__ = ["ToolErrorHandlingMiddleware"]
