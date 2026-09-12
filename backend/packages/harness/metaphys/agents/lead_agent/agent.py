"""lead agent 的装配 —— 把模型、工具、中间件、状态缝成一个可运行的图。

装配本身很薄，真正需要读懂的是**中间件的顺序**（见 :func:`build_middlewares`）：
LangGraph 的两类钩子方向相反，顺序排错不会有任何报错，只会让某个中间件静默失效。
所以顺序在这里被断言钉死，而不是靠注释提醒。
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from metaphys.agents.lead_agent.prompt import apply_prompt_template
from metaphys.agents.thread_state import ThreadState
from metaphys.config import AppConfig, get_app_config
from metaphys.middlewares import (
    ClarificationMiddleware,
    GroundingMiddleware,
    ToolErrorHandlingMiddleware,
)
from metaphys.models import create_chat_model
from metaphys.tools import load_tools

AGENT_NAME = "lead_agent"
"""图名。与 ``langgraph.json`` 的 ``graphs`` 键保持一致，也用于追踪与调试。"""


def validate_middleware_order(middlewares: Sequence[AgentMiddleware]) -> None:
    """校验中间件顺序。顺序不合规时抛 ``AssertionError``。

    **顺序即语义**，两类钩子的方向恰好相反：

    ``wrap_model_call`` / ``wrap_tool_call`` —— **列表中第一个在最外层**。
    所以 ``ToolErrorHandlingMiddleware`` 排第一位：只有包在所有中间件之外，
    才能兜住它们抛出的异常。

    ``after_model`` —— **按注册逆序执行**，列表中最后一个最先看到模型输出。
    所以 ``ClarificationMiddleware`` 必须最后：它要抢在 ``GroundingMiddleware``
    之前丢弃与 ``ask_clarification`` 同轮的兄弟工具调用，否则模型可能一边追问、
    一边就把盘排了。

    两条检查对应的都是**静默失效**：位置挪了不会报错，只是行为悄悄变了。

    独立成函数而不是内联在 :func:`build_middlewares` 里，是为了让它能被单独调用 ——
    内联版本没法测：构造列表和校验用的是同一个名字，测试里换掉那个名字，
    校验也会跟着换，于是永远通不过"位置错了要报错"这条用例。
    """
    if not middlewares or not isinstance(middlewares[0], ToolErrorHandlingMiddleware):
        actual = type(middlewares[0]).__name__ if middlewares else "（空列表）"
        raise AssertionError(
            f"中间件列表第一位必须是 ToolErrorHandlingMiddleware（wrap_tool_call 的最外层），"
            f"实际是 {actual} —— 排在中间会漏掉其它中间件抛出的异常。"
        )
    if not isinstance(middlewares[-1], ClarificationMiddleware):
        raise AssertionError(
            f"中间件列表最后一位必须是 ClarificationMiddleware，实际是 {type(middlewares[-1]).__name__}。"
            f"它需要 (1) 作为最内层 wrap_tool_call 短路 ask_clarification，"
            f"(2) 作为最先执行的 after_model 丢弃同轮兄弟工具调用 —— 换位置两条都会静默失效。"
        )


def build_middlewares() -> list[AgentMiddleware]:
    """构造中间件列表并校验顺序。顺序的含义见 :func:`validate_middleware_order`。"""
    middlewares: list[AgentMiddleware] = [
        ToolErrorHandlingMiddleware(),
        GroundingMiddleware(),
        ClarificationMiddleware(),
    ]
    validate_middleware_order(middlewares)
    return middlewares


def make_lead_agent(config: RunnableConfig | None = None):
    """装配并返回已编译的 lead agent 图。

    ``config`` 由 LangGraph 传入（``langgraph.json`` 的 ``graphs`` 工厂约定），
    M2 未使用其中的信息 —— 参数保留是为了不改上游调用约定。

    **checkpointer 在编译后绑定**（``graph.checkpointer = InMemorySaver()``），
    而不是传给 ``create_agent``：M4 换成 Postgres saver 时只动这一处绑定，装配
    逻辑一行不用改。此写法已实测有效（第二轮的 ``invoke`` 能取回历史消息）。
    """
    app_config: AppConfig = get_app_config()

    graph = create_agent(
        model=create_chat_model(app_config=app_config),
        tools=load_tools(app_config),
        middleware=build_middlewares(),
        system_prompt=apply_prompt_template(),
        state_schema=ThreadState,
        name=AGENT_NAME,
    )

    # M4 换 AsyncPostgresSaver 只改这一行。M2 用内存版：单进程、无外部依赖。
    graph.checkpointer = InMemorySaver()
    return graph


__all__ = ["AGENT_NAME", "build_middlewares", "make_lead_agent", "validate_middleware_order"]
