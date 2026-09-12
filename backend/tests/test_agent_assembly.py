"""Agent 装配的测试 —— 中间件顺序、图可编译、提示词到位。

这里守的是一条**不会自己报错的**约束：中间件排错顺序、图工厂写成惰性再导出、
提示词漏掉一条铁律 —— 三者都不会让程序崩溃，只会让防线静默消失。所以每条都
写成断言。
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import metaphys.agents as agents_package
import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from metaphys.agents.lead_agent import SYSTEM_PROMPT, apply_prompt_template
from metaphys.agents.lead_agent import agent as agent_module
from metaphys.agents.lead_agent.agent import AGENT_NAME, build_middlewares
from metaphys.middlewares import (
    ClarificationMiddleware,
    GroundingMiddleware,
    ToolErrorHandlingMiddleware,
)
from metaphys.tools import load_tools
from metaphys.tools.builtins.clarification import ASK_CLARIFICATION_TOOL_NAME

from tests.conftest import ScriptedChatModel

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# 中间件顺序
# --------------------------------------------------------------------------- #
def test_middleware_order_is_exactly_as_designed():
    """顺序即语义，两类钩子方向相反：

    - ``wrap_tool_call`` 第一个在最外层 → 兜底异常的要排第一
    - ``after_model`` 逆序执行 → 丢弃兄弟工具调用的要排最后
    """
    assert [type(m) for m in build_middlewares()] == [
        ToolErrorHandlingMiddleware,
        GroundingMiddleware,
        ClarificationMiddleware,
    ]


def test_refuses_a_list_that_does_not_end_with_clarification():
    """把 ClarificationMiddleware 从末位挪走，必须**拒绝**而不是默默继续。

    真实场景是有人调整列表顺序时顺手挪了它 —— 那会让"追问时不会顺手排盘"这条
    防线整体失效，且没有任何报错。
    """
    wrong = [ToolErrorHandlingMiddleware(), GroundingMiddleware(), GroundingMiddleware()]

    with pytest.raises(AssertionError, match="最后一位必须是 ClarificationMiddleware"):
        agent_module.validate_middleware_order(wrong)


def test_refuses_a_list_that_does_not_start_with_error_handling():
    """兜底中间件不在最外层，就兜不住其它中间件抛出的异常。"""
    wrong = [GroundingMiddleware(), ToolErrorHandlingMiddleware(), ClarificationMiddleware()]

    with pytest.raises(AssertionError, match="第一位必须是 ToolErrorHandlingMiddleware"):
        agent_module.validate_middleware_order(wrong)


@pytest.mark.parametrize(
    "middlewares",
    [[], [ClarificationMiddleware()]],
    ids=["空列表", "只有澄清中间件"],
)
def test_refuses_a_list_that_cannot_work(middlewares: list[AgentMiddleware]):
    with pytest.raises(AssertionError):
        agent_module.validate_middleware_order(middlewares)


def test_a_correctly_ordered_list_passes():
    agent_module.validate_middleware_order(
        [ToolErrorHandlingMiddleware(), GroundingMiddleware(), ClarificationMiddleware()]
    )


def test_after_model_hooks_run_in_reverse_order():
    """实测钉住框架语义，而不是照着文档猜。

    ``after_model`` 按注册**逆序**执行 —— 整个澄清设计建立在这条之上。框架升级若改了
    这个方向，这里会先红，而不是等到"模型又一边追问一边排盘"才被发现。
    """
    order: list[str] = []

    def recorder(label: str, base: type[AgentMiddleware]) -> AgentMiddleware:
        def after_model(self, state, runtime):  # noqa: ANN001, ANN202
            order.append(label)
            return None

        # 类名必须各不相同：create_agent 按 name 去重，同名会被判成"重复中间件"而拒收。
        return type(f"Recording{label.title()}", (base,), {"after_model": after_model})()

    middlewares = [
        recorder("error", ToolErrorHandlingMiddleware),
        recorder("grounding", GroundingMiddleware),
        recorder("clarification", ClarificationMiddleware),
    ]

    graph = create_agent(
        model=ScriptedChatModel(responses=[AIMessage("好")]),
        tools=[],
        middleware=middlewares,
        system_prompt="x",
    )
    graph.invoke({"messages": [("human", "你好")]})

    assert order == ["clarification", "grounding", "error"], (
        f"after_model 应当是逆序执行（最后注册的最先跑），实际顺序：{order}"
    )


# --------------------------------------------------------------------------- #
# 图工厂
# --------------------------------------------------------------------------- #
def test_factory_is_a_real_module_attribute():
    """LangGraph 从模块 ``__dict__`` 取工厂，``__getattr__`` 式的惰性再导出取不到。

    这是照搬 deer-flow 写法时最容易踩的坑：本地 import 一切正常，只有 LangGraph
    解析时找不到 —— 报的是"找不到 graph"，离病因很远。
    """
    assert "make_lead_agent" in vars(agents_package), "工厂必须出现在模块 __dict__ 里"
    assert callable(vars(agents_package)["make_lead_agent"])


def test_langgraph_json_points_at_a_resolvable_factory():
    """直接按 langgraph.json 的写法解析一遍，等价于 LangGraph 自己做的事。"""
    config = json.loads((BACKEND_ROOT / "langgraph.json").read_text(encoding="utf-8"))
    module_path, _, attribute = config["graphs"]["lead_agent"].partition(":")

    module = importlib.import_module(module_path)
    assert attribute in vars(module), f"{module_path} 的 __dict__ 里没有 {attribute}"
    assert callable(vars(module)[attribute])


def test_graph_compiles_with_the_expected_tools():
    graph = agent_module.make_lead_agent()

    assert graph.name == AGENT_NAME
    assert isinstance(graph.checkpointer, InMemorySaver), "checkpointer 在编译后绑定 —— 少了它多轮对话会丢历史"

    tool_names = {tool.name for tool in load_tools()}
    assert {"bazi_chart", "lookup_birthplace", ASK_CLARIFICATION_TOOL_NAME} <= tool_names


def test_clarification_is_mounted_unconditionally():
    """``ask_clarification`` 与 ``ClarificationMiddleware`` 是一对。

    它不走 config.yaml —— 配置漏写不会报错，只会让模型重新开始猜。所以由代码
    无条件挂载。
    """
    assert ASK_CLARIFICATION_TOOL_NAME in {tool.name for tool in load_tools()}


def test_injected_parameters_are_hidden_from_the_model():
    """``runtime`` 与 ``tool_call_id`` 是注入参数，不能出现在模型看到的契约里。

    暴露出去会让模型试图自己编一个 runtime，或者干脆漏传 tool_call_id 而让
    ToolMessage 对不上调用。
    """
    model_visible = {tool.name: set(tool.tool_call_schema.model_json_schema()["properties"]) for tool in load_tools()}

    assert "runtime" not in model_visible["bazi_chart"]
    assert "tool_call_id" not in model_visible["bazi_chart"]
    assert "tool_call_id" not in model_visible[ASK_CLARIFICATION_TOOL_NAME]
    assert model_visible["bazi_chart"] >= {"gender", "birth_datetime", "place"}


# --------------------------------------------------------------------------- #
# 提示词
# --------------------------------------------------------------------------- #
def test_prompt_is_stable_and_matches_the_module_constant():
    assert apply_prompt_template() == SYSTEM_PROMPT


def test_prompt_carries_every_hard_rule():
    """四条铁律是"模型不去做"的那一半保障；缺一条就等于少一道防线。"""
    required = {
        "没有计算能力": "铁律一：不得自行推算",
        "bazi_chart": "铁律一：八字唯一合法来源是工具",
        "astro_chart": "铁律一：星盘唯一合法来源是工具",
        "上升": "铁律一：星盘不得自行推算的具体项（上升/宫位/相位）",
        ASK_CLARIFICATION_TOOL_NAME: "铁律二：信息不足就问",
        "time_accuracy": "铁律三：时辰精度决定话题范围",
        "子女宫": "铁律三：unknown 时不得谈的具体项",
        "strength.school": "铁律四：八字结论必须带流派前提",
        "strength.notes": "铁律四：必须给出取法依据",
        "house_system": "铁律四：星盘结论必须带宫位制前提",
        "zodiac_type": "铁律四：星盘结论必须带黄道制前提",
        "用什么体系": "体系选择：没指明就先问，不替用户选",
    }
    missing = [why for needle, why in required.items() if needle not in SYSTEM_PROMPT]

    assert not missing, "提示词缺少：" + "；".join(missing)


def test_prompt_contains_no_runtime_context():
    """静态提示词才能吃到 prefix cache。

    动态内容（当前日期、用户称呼）一旦混进来，每轮前缀都变，长对话的成本与首字
    延迟都会明显变差。这里钉住"模板不接收任何参数"这件事。
    """
    assert list(inspect.signature(apply_prompt_template).parameters) == []
