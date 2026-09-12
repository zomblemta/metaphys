"""Agent 层。

``make_lead_agent`` 是 ``langgraph.json`` 里 ``graphs.lead_agent`` 指向的入口。
"""

from langchain_core.runnables import RunnableConfig

from metaphys.agents.thread_state import ThreadState


def make_lead_agent(config: RunnableConfig | None = None):
    """图工厂 —— LangGraph 从本模块的 ``__dict__`` 取它，再调用。

    **必须是模块级的具体函数，不能写成 ``__getattr__`` 式的惰性再导出。**
    LangGraph 取的是 ``__dict__`` 里的实际值，只经 ``__getattr__`` 暴露的名字
    取不到；失败信息是"找不到 graph"，而不是"你的 ``__getattr__`` 没生效"，
    排查起来相当费时。

    ``import`` 放在函数体内：让 ``import metaphys.agents`` 不必立刻拉起完整的
    装配链（构造模型、解析工具都要读 ``config.yaml`` 并展开 ``$ENV``），
    也让缺少 ``$DEEPSEEK_API_KEY`` 的环境能安全地导入本模块做静态检查。
    """
    from metaphys.agents.lead_agent.agent import make_lead_agent as factory

    return factory(config)


__all__ = ["ThreadState", "make_lead_agent"]
