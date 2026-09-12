"""澄清中间件 —— 保证"追问"时不会顺手把盘也排了。

模型完全可能在一轮里同时发出两个工具调用：``ask_clarification`` 问出生地，
外加一个 ``bazi_chart`` 用猜的地名先把盘排了。用户的体验会是"它一边问我，
一边已经给了一份命盘" —— 而那份命盘的时柱多半是错的。

本中间件用两个钩子堵住它，**两个钩子都依赖它排在中间件列表的最后一位**：

1. ``wrap_tool_call`` —— 列表中最后一个即最内层，紧贴真正的工具执行。在这里
   拦截 ``ask_clarification`` 并以 ``Command(goto=END)`` 短路，本轮就此结束。
2. ``after_model`` —— 注册逆序执行，列表中最后一个**最先**跑。所以它能在任何
   其他中间件之前看到模型的输出并丢弃兄弟工具调用。

若把它移到列表中间，两条都会失效，且**不会报错**。故
:mod:`metaphys.agents.lead_agent.agent` 里有断言把位置钉死，测试里另有断言
验证逆序语义本身。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import AIMessage
from langgraph.types import Command

from metaphys.tools.builtins.clarification import (
    ASK_CLARIFICATION_TOOL_NAME,
    build_clarification_command,
)

#: 模型没填 question 时的兜底问句。宁可问得笼统，也不要抛异常。
_FALLBACK_QUESTION = "为了继续，请补充必要的信息。"


def _drop_sibling_tool_calls(state: dict[str, Any]) -> dict[str, Any] | None:
    """若本轮含 ``ask_clarification``，丢弃与它同轮的兄弟工具调用。

    只看**最后一条消息** —— ``after_model`` 紧跟模型节点执行，最后一条就是本轮
    的模型输出。若改成向前回溯找"最近一条带工具调用的 AIMessage"，会捞到**上一轮**
    的调用并把它改写掉，等于篡改历史。

    返回 ``None`` 表示无需改动 —— 让调用方原样放行，不产生无谓的状态写入。
    """
    messages = state.get("messages") or []
    if not messages:
        return None

    message = messages[-1]
    if not (isinstance(message, AIMessage) and message.tool_calls):
        return None  # 本轮没有工具调用，无事可做

    kept = [call for call in message.tool_calls if call.get("name") == ASK_CLARIFICATION_TOOL_NAME]
    if not kept:
        # 本轮没调用 ask_clarification —— **原样放行**。
        # 这里曾写成"kept 与 tool_calls 长度不等就改写"，于是每次正常工具调用都会被
        # 清空成 []，模型再也调不动任何工具，而且不报任何错。
        return None
    if len(kept) == len(message.tool_calls):
        return None  # 只有澄清调用，没有兄弟可丢

    # 用 model_copy 保留 id：add_messages 按 id 替换，才能覆盖原消息而非追加。
    return {"messages": [message.model_copy(update={"tool_calls": kept})]}


class ClarificationMiddleware(AgentMiddleware):
    """拦截 ``ask_clarification``：结束本轮，且不让同轮的其它工具跑起来。"""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        if request.tool_call.get("name") == ASK_CLARIFICATION_TOOL_NAME:
            return self._clarify(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        if request.tool_call.get("name") == ASK_CLARIFICATION_TOOL_NAME:
            return self._clarify(request)
        return await handler(request)

    def after_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        return _drop_sibling_tool_calls(state)

    async def aafter_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        return _drop_sibling_tool_calls(state)

    @staticmethod
    def _clarify(request: ToolCallRequest) -> Command:
        """直接构造追问结果，不执行工具函数体。

        工具函数体里有一份等价实现，供脱离中间件单独使用时兜底；此处走
        ``build_clarification_command`` 以共用同一份构造逻辑。
        """
        args = request.tool_call.get("args") or {}
        question = str(args.get("question") or "").strip() or _FALLBACK_QUESTION
        missing_fields = args.get("missing_fields")
        return build_clarification_command(
            question,
            tool_call_id=request.tool_call.get("id", ""),
            missing_fields=missing_fields if isinstance(missing_fields, list) else None,
        )


__all__ = ["ClarificationMiddleware"]
