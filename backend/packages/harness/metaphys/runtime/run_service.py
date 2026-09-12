"""运行服务 —— 驱动 lead agent 的单一入口。

M2 用它跑端到端验证脚本；M4 的 FastAPI/SSE 层把 :meth:`RunService.astream_run`
的事件原样转发给前端，不再二次加工。

把"跑一轮对话"收在一处，是因为有三件事容易在各个调用点各写一遍、进而各错一遍：
线程 id 怎么定、递归上限怎么算、从终态里读哪些字段。尤其是**递归上限** ——
它没有默认值兜底，配错了表现为"聊到一半突然 GraphRecursionError"。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph.state import CompiledStateGraph

from metaphys.agents import make_lead_agent
from metaphys.agents.messages import message_text
from metaphys.config import AppConfig, get_app_config
from metaphys.tools.builtins.clarification import clarification_question

DEFAULT_THREAD_ID = "default"
"""默认线程 id。单用户脚本场景够用；多用户由网关按会话生成。"""

#: 一轮"模型 → 工具 → 模型"实测走几个 superstep。注册的 ``after_model`` 钩子各自
#: 是一个图节点，所以不是直觉上的 2。实测值见 tests/test_run_service.py。
_SUPERSTEPS_PER_ITERATION = 4

#: 收尾那一轮模型调用（不再调工具）的固定开销。
_FINAL_SUPERSTEPS = 3


def _recursion_limit(app_config: AppConfig) -> int:
    """把 ``agent.max_iterations`` 换算成 LangGraph 的 ``recursion_limit``。

    两个量纲不同：``max_iterations`` 数的是"模型调了几轮"，``recursion_limit``
    数的是"图走了几个 superstep"。换算系数按实测取值，**留有余量**：上限只是
    防死循环的保险丝，不是正常路径的约束，宁可宽一点也不要在正常对话里误伤。
    """
    return app_config.agent.max_iterations * _SUPERSTEPS_PER_ITERATION + _FINAL_SUPERSTEPS


@dataclass(frozen=True)
class RunResult:
    """一轮对话结束后的终态视图。

    只暴露调用方真正要用的字段，而不是把整个 state 字典递出去 —— 前端需要的
    是命盘与核验结果，不是 LangGraph 的内部结构。
    """

    messages: tuple[BaseMessage, ...] = ()
    charts: dict[str, dict[str, Any]] = field(default_factory=dict)
    birth_profile: dict[str, Any] | None = None
    grounding_flags: tuple[dict[str, Any], ...] = ()
    safety_flags: tuple[str, ...] = ()

    @classmethod
    def from_state(cls, state: dict[str, Any] | None) -> RunResult:
        """从图终态构造。``state`` 为 None（例如流里没收到 values）时返回空结果。"""
        state = state or {}
        return cls(
            messages=tuple(state.get("messages") or ()),
            charts=dict(state.get("charts") or {}),
            birth_profile=state.get("birth_profile"),
            grounding_flags=tuple(state.get("grounding_flags") or ()),
            safety_flags=tuple(state.get("safety_flags") or ()),
        )

    @property
    def reply(self) -> str:
        """本轮助手对用户说的话。

        与落地核验用的是同一个 :func:`~metaphys.agents.messages.message_text` ——
        取文本的方式若分成两份实现，核验就可能扫的不是用户看到的那段话。

        追问轮要回退到工具参数里的问句：那时助手确实说了话，只是话在
        ``ask_clarification`` 的参数里，``content`` 是空的。只取正文的话，
        "帮我排盘"却缺出生地的那一轮会返回空串 —— 用户看到一片沉默，而这正是
        最需要把话说明白的时刻。
        """
        for message in reversed(self.messages):
            if isinstance(message, AIMessage):
                return message_text(message) or clarification_question(message)
        return ""

    @property
    def bazi(self) -> dict[str, Any] | None:
        """八字命盘。没排过盘时为 None。"""
        return self.charts.get("bazi")

    @property
    def astro(self) -> dict[str, Any] | None:
        """西洋星盘。没排过盘时为 None。

        与 :attr:`bazi` 并列而非互斥 —— 同一轮里两张盘可以同时存在
        （``merge_charts`` 按 kind 合并，先八字的会话接着排星盘不会把命盘挤掉）。
        """
        return self.charts.get("astro")

    def as_dict(self) -> dict[str, Any]:
        """JSON 可序列化的摘要，供 SSE 推送。"""
        return {
            "reply": self.reply,
            "charts": self.charts,
            "birth_profile": self.birth_profile,
            "grounding_flags": list(self.grounding_flags),
            "safety_flags": list(self.safety_flags),
            "message_count": len(self.messages),
        }

    def summary(self) -> str:
        """一行摘要，给脚本与日志用。

        排过哪些盘就列哪些，**不用布尔**。原来这里是 ``has_chart``，只反映八字
        —— 接着排了星盘的那一轮，日志依然写着"没排盘"。这类错不会让任何东西崩溃，
        只会让排查的人照着一个假事实找原因。
        """
        return json.dumps(
            {
                "reply_preview": self.reply[:60],
                "charts": sorted(self.charts),
                "grounding_flags": [flag.get("claimed") for flag in self.grounding_flags],
            },
            ensure_ascii=False,
        )


class RunService:
    """驱动一个 lead agent 图。图是惰性构造的 —— 构造它要读配置、建模型。"""

    def __init__(
        self,
        graph: CompiledStateGraph | None = None,
        *,
        app_config: AppConfig | None = None,
    ) -> None:
        self._app_config = app_config
        self._graph = graph

    @property
    def app_config(self) -> AppConfig:
        if self._app_config is None:
            self._app_config = get_app_config()
        return self._app_config

    @property
    def graph(self) -> CompiledStateGraph:
        if self._graph is None:
            self._graph = make_lead_agent()
        return self._graph

    def _run_config(self, thread_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": _recursion_limit(self.app_config),
        }

    def run(self, message: str, *, thread_id: str = DEFAULT_THREAD_ID) -> RunResult:
        """跑一轮并返回终态。同一 ``thread_id`` 的后续调用自动带上历史。"""
        state = self.graph.invoke({"messages": [("human", message)]}, self._run_config(thread_id))
        return RunResult.from_state(state)

    async def astream_run(
        self,
        message: str,
        *,
        thread_id: str = DEFAULT_THREAD_ID,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式跑一轮，逐个 superstep 产出事件。

        事件两种：

        - ``{"event": "update", "node": <节点名>, "data": <该节点的状态增量>}``
        - ``{"event": "final", "data": <RunResult.as_dict()>}``

        直接用 LangGraph 的 ``updates`` 增量而不是自己解析消息 —— M4 要推给前端的
        正是"哪个节点产出了什么"，在这里抽成别的形状等于把信息丢掉再猜回来。
        """
        final_state: dict[str, Any] | None = None

        async for mode, payload in self.graph.astream(
            {"messages": [("human", message)]},
            self._run_config(thread_id),
            stream_mode=["updates", "values"],
        ):
            if mode == "updates":
                for node, update in (payload or {}).items():
                    yield {"event": "update", "node": node, "data": update or {}}
            elif mode == "values":
                final_state = payload

        yield {"event": "final", "data": RunResult.from_state(final_state).as_dict()}


@lru_cache(maxsize=1)
def get_run_service() -> RunService:
    """进程内单例。图（含 checkpointer）跨调用复用，否则每轮都丢历史。"""
    return RunService()


__all__ = ["DEFAULT_THREAD_ID", "RunResult", "RunService", "get_run_service"]
