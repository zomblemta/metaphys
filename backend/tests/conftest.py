"""测试共用夹具。

核心是 :class:`ScriptedChatModel` —— 一个按脚本吐消息的假模型。有了它，整条
agent 链路（图装配 → 中间件 → 工具 → state）都能在**无网络、无 API key**的条件下
确定性地跑通，断言的是"排盘一定来自工具""编造一定被抓"这类不变量本身。

真实模型的对抗性测试另有其人（见 :mod:`tests.test_adversarial`），它需要 key，
缺 key 时跳过 —— 但那种测试只能证明"这一次没编"，覆盖不到边角，所以主力是这里。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from metaphys.config import get_app_config, reload_app_config
from metaphys.engines.astro import compute_astro
from metaphys.engines.bazi import compute_bazi
from metaphys.schemas.chart import BirthProfile, Calendar, Gender, TimeAccuracy
from metaphys.tools.builtins.geo import resolve_place

#: 一个具体到分钟的出生信息，用于固定命盘。改它会牵动多处断言。
_BIRTH = datetime(1990, 6, 15, 10, 30)
_PLACE = "北京市"


class ScriptedChatModel(BaseChatModel):
    """按脚本逐条返回消息的假模型。

    比 ``GenericFakeChatModel`` 多两样必需的能力：

    1. **``bind_tools``** —— agent 装配时一定会 bind，基类默认抛
       ``NotImplementedError``，图根本建不起来。
    2. **消息原样返回** —— ``GenericFakeChatModel`` 走流式路径时会丢掉
       ``tool_calls``（实测：返回的 AIMessage 里 ``tool_calls`` 变成 ``[]``），
       而工具调用恰恰是本项目最需要被测的路径。

    脚本用完后**重复最后一条**而不是抛 StopIteration：测试里真正关心的是"第 N 步
    发生了什么"，让它在多余调用上抛异常会把断言失败伪装成脚本越界，反而难查。
    """

    responses: list[AIMessage]
    counter: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(
        self,
        messages: Sequence[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        index = min(self.counter, len(self.responses) - 1)
        self.counter += 1
        return ChatResult(generations=[ChatGeneration(message=self.responses[index])])

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        return self


@pytest.fixture(autouse=True)
def _app_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """让配置层始终能被加载。

    ``config.yaml`` 的 ``api_key: $DEEPSEEK_API_KEY`` 在变量缺失时会 fail fast ——
    这是刻意设计（宁可启动即报错，也不要请求期才炸）。但绝大多数测试并不真的发
    请求，所以给一个占位值即可。**测试从不打印这个值**，它也不是任何真实密钥。

    同时清掉配置单例缓存：本夹具在用例前后各刷一次，避免"上一个用例改了配置、
    下一个用例读到缓存"这类跨用例污染。
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-placeholder-not-a-real-key")
    reload_app_config()
    yield
    # 收尾**只清缓存、不重读**。`reload_app_config()` 会立刻把缓存填回去，而此刻
    # monkeypatch 尚未回滚 —— 用例若动过配置环境（比如指向一个不存在的 config.yaml），
    # 这一读就会抛异常，把用例结果从"通过"污染成 teardown ERROR。
    # 下一个用例的 setenv 之后本来就会重读，这里重读没有任何收益。
    get_app_config.cache_clear()


@pytest.fixture(scope="session")
def bazi_chart_dict() -> dict[str, Any]:
    """一份真实的命盘（1990-06-15 10:30 北京，男）。

    session 级：排盘是确定性的，重复算没有意义。返回 ``model_dump(mode="json")``
    而不是 ``BaziChart`` 对象 —— 中间件与 state 里流通的就是这个形状，
    测试里不该出现"引擎对象"与"state 字典"两套用法。

    坐标**经地理库解析**得到，不写死。写死过一个想当然的经纬度（116.4074），
    与地理库里的真值（116.413384）差了 0.006° —— 真太阳时因此差 1.4 秒，
    于是"命盘与引擎逐字段一致"这条断言恒假，而被测代码其实一点问题没有。
    夹具必须走与工具完全相同的那条解析路径，否则它比的不是"链路有没有改动命盘"，
    而是"我猜的坐标对不对"。
    """
    point = resolve_place(_PLACE).point
    assert point is not None, f"地理库里查不到 {_PLACE}，夹具的前提不成立"

    profile = BirthProfile(
        gender=Gender.MALE,
        birth_datetime=_BIRTH,
        place=point.display_name,
        latitude=point.latitude,
        longitude=point.longitude,
        calendar=Calendar.SOLAR,
        time_accuracy=TimeAccuracy.EXACT,
    )
    return compute_bazi(profile).model_dump(mode="json")


@pytest.fixture(scope="session")
def astro_chart_dict() -> dict[str, Any]:
    """一份真实的星盘（与 :func:`bazi_chart_dict` 同一瞬间、同一地点）。

    同一瞬间是刻意的：两条链路必须从同一个出生时刻走到同一个 UTC 瞬间，
    任何一边的时区处理漂了，两张盘就不再描述同一个人。

    ``gender`` 留空 —— 星盘不需要性别，而这个夹具同时证明了
    ``BirthProfile.gender`` 改可选之后星盘链路照常工作。
    """
    point = resolve_place(_PLACE).point
    assert point is not None, f"地理库里查不到 {_PLACE}，夹具的前提不成立"

    profile = BirthProfile(
        birth_datetime=_BIRTH,
        place=point.display_name,
        latitude=point.latitude,
        longitude=point.longitude,
        time_accuracy=TimeAccuracy.EXACT,
    )
    return compute_astro(profile).chart.model_dump(mode="json")


@pytest.fixture
def build_scripted_graph():
    """返回一个 ``(responses) -> 已编译图`` 的构造器。

    用的是**真的**工具、**真的**中间件、**真的**状态定义，只把模型换成脚本化的 ——
    于是被测的是整条链路，而不是某个中间件的孤立行为。脚本里写什么，模型就"说"什么。
    """
    from langchain.agents import create_agent
    from langgraph.checkpoint.memory import InMemorySaver
    from metaphys.agents.lead_agent.agent import AGENT_NAME, build_middlewares
    from metaphys.agents.lead_agent.prompt import apply_prompt_template
    from metaphys.agents.thread_state import ThreadState
    from metaphys.tools import load_tools

    def build(responses: list[AIMessage]):
        graph = create_agent(
            model=ScriptedChatModel(responses=responses),
            tools=load_tools(),
            middleware=build_middlewares(),
            system_prompt=apply_prompt_template(),
            state_schema=ThreadState,
            name=AGENT_NAME,
        )
        # 与 make_lead_agent 一致：编译后绑定 checkpointer，否则多轮对话会丢历史。
        graph.checkpointer = InMemorySaver()
        return graph

    return build


@pytest.fixture
def chart_tool_call() -> dict[str, Any]:
    """一次 ``bazi_chart`` 工具调用的参数，与 :func:`bazi_chart_dict` 同一个人。"""
    return {
        "name": "bazi_chart",
        "args": {
            "gender": "male",
            "birth_datetime": _BIRTH.isoformat(),
            "place": _PLACE,
        },
        "id": "call_chart_1",
    }


@pytest.fixture
def astro_tool_call() -> dict[str, Any]:
    """一次 ``astro_chart`` 工具调用的参数，**同一个人、同一瞬间**。

    没有 ``gender`` —— 星盘不需要它，这条与 :func:`chart_tool_call` 的差别是
    刻意的：两个夹具并列摆着，"哪些输入属于哪套体系"一眼可见。
    """
    return {
        "name": "astro_chart",
        "args": {
            "birth_datetime": _BIRTH.isoformat(),
            "place": _PLACE,
        },
        "id": "call_astro_1",
    }
