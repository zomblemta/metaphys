"""澄清追问工具 —— 把"信息不足"变成一次正常的交互，而不是一次编造。

排盘缺出生地、缺时辰、地名有歧义时，模型最容易做的事是**挑一个最像的**然后
照常输出。那会产出一份看着完整、实则时柱错误的命盘。这个工具给模型一条
体面的退路：停下来问。

**为什么函数体只是构造 Command：** 真正保证"追问时不会顺手把盘也排了"的是
:class:`~metaphys.middlewares.clarification.ClarificationMiddleware` ——
它在 ``wrap_tool_call`` 里短路本工具（不执行函数体），并在 ``after_model``
里丢弃同轮的兄弟工具调用。此处保留可用实现，是为了让工具单独可用、可测，
不至于脱离中间件就变成一颗哑弹。
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.graph import END
from langgraph.types import Command

ASK_CLARIFICATION_TOOL_NAME = "ask_clarification"
"""工具名。中间件按名字识别拦截目标，故两侧共用同一个常量，不各写一遍字面量。"""


def clarification_question(message: BaseMessage) -> str:
    """取出这条 AI 消息里 ``ask_clarification`` 的问句；不是追问则返回空串。

    追问与普通回复的形态不同：那句问话是**工具调用的参数**，不在消息正文里。
    于是追问轮的 ``AIMessage.content`` 是空串 —— 调用方若只取最后一条 AI 消息的
    正文，用户看到的就是沉默。此处把问句取出来，是"助手到底说了什么"的一部分。

    抽成函数而不是让调用方自己读 ``tool_calls[0]["args"]["question"]``：参数名与
    工具名都属于本模块的契约，散落到调用点就会在改名时漏掉一处、且不报错。
    """
    if not isinstance(message, AIMessage):
        return ""
    for call in message.tool_calls:
        if call.get("name") != ASK_CLARIFICATION_TOOL_NAME:
            continue
        question = (call.get("args") or {}).get("question")
        if isinstance(question, str) and question.strip():
            return question.strip()
    return ""


def build_clarification_command(
    question: str,
    *,
    tool_call_id: str,
    missing_fields: list[str] | None = None,
) -> Command:
    """构造"追问并结束本轮"的 Command。

    工具函数体与中间件共用这一处，避免两边对 ToolMessage 的构造方式产生分歧。

    ``missing_fields`` 放进 ``artifact`` 而非消息正文：它是给前端的结构化提示
    （可用来高亮表单字段），塞进正文只会成为模型与用户都要读的噪音。
    """
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=question,
                    tool_call_id=tool_call_id,
                    artifact={"missing_fields": list(missing_fields or [])},
                )
            ],
        },
        goto=END,
    )


@tool(ASK_CLARIFICATION_TOOL_NAME, parse_docstring=True, return_direct=True)
def ask_clarification_tool(
    question: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    missing_fields: list[str] | None = None,
) -> Command:
    """当必要信息缺失或存在歧义时，向用户提出一个澄清问题并结束本轮回复。

    用户没给出生地、只说了"下午"而没说具体时辰、或地名有多个同名候选时，
    用它提问。**不要**在信息不足时猜测后继续排盘 —— 猜错经度会让时柱出错，
    而用户看不出命盘是错的。

    Args:
        question: 要问用户的话。直接写完整的一句问句，不要写"需要澄清"这类
            元描述 —— 这句话会原样展示给用户。
        missing_fields: 缺失的字段名列表，如 ``["birth_datetime"]``、
            ``["place"]``。供前端高亮对应输入项，用户看不到。

    Returns:
        结束本轮并携带该问题的 Command。
    """
    return build_clarification_command(question, tool_call_id=tool_call_id, missing_fields=missing_fields)


__all__ = [
    "ASK_CLARIFICATION_TOOL_NAME",
    "ask_clarification_tool",
    "build_clarification_command",
    "clarification_question",
]
