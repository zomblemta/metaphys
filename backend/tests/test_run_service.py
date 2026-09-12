"""端到端 —— 整条链路跑通，且不变量在链路上依然成立。

前面几个文件测的是零件：工具自己、中间件自己、装配合不合规。这里把它们串起来跑，
断言的是**只有串起来才能验证的东西**：

- 命盘确实是从工具流进 state 的，且与引擎逐字段一致
- 模型编造时，核验在真实图里确实拦得住
- 模型想边追问边排盘时，盘确实没被排出来

模型是脚本化的，所以这些结论**不依赖网络、不依赖 API key、每次结果相同**。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain_core.messages import AIMessage, ToolMessage
from metaphys.config import find_config_path, get_app_config, reload_app_config
from metaphys.engines.astro.points import SIGN_NAMES
from metaphys.middlewares import find_astro_claims
from metaphys.runtime import RunResult, RunService
from metaphys.runtime.run_service import (
    _FINAL_SUPERSTEPS,
    _SUPERSTEPS_PER_ITERATION,
    _recursion_limit,
)
from metaphys.tools.builtins.clarification import ASK_CLARIFICATION_TOOL_NAME

_CHART_TOOL_NAME = "bazi_chart"
_ASTRO_TOOL_NAME = "astro_chart"

#: 实测：一轮「模型 → 工具 → 模型」走过的 superstep 数。写死在这里当基准，
#: 换算系数若被改小到跑不完一轮，下面的用例会红。
_MEASURED_SUPERSTEPS_PER_TOOL_ROUND = 7


def _tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _four_pillars(chart: dict[str, Any]) -> str:
    return " ".join(
        chart[field]["stem"] + chart[field]["branch"]
        for field in ("year_pillar", "month_pillar", "day_pillar", "time_pillar")
    )


# --------------------------------------------------------------------------- #
# 命盘从工具流进 state
# --------------------------------------------------------------------------- #
def test_chart_reaches_state_and_matches_the_engine(build_scripted_graph, chart_tool_call, bazi_chart_dict):
    """命盘必须**逐字段**等于引擎算出来的那份。

    这是「LLM 不参与推算」最直接的证据：模型全程只说了「调这个工具」，最终 state 里的
    命盘与直接调 :func:`compute_bazi` 完全一致 —— 中间没有任何一处是模型「补」的。
    """
    graph = build_scripted_graph(
        [
            _tool_call(_CHART_TOOL_NAME, chart_tool_call["args"], chart_tool_call["id"]),
            AIMessage(f"您的四柱是 {_four_pillars(bazi_chart_dict)}。"),
        ]
    )

    result = RunService(graph=graph).run("帮我排个盘：1990年6月15日上午10点半，北京，男")

    assert result.bazi == bazi_chart_dict
    assert result.birth_profile is not None
    assert result.birth_profile["place"] == "北京市"


def test_chart_is_absent_when_the_model_never_calls_the_tool(build_scripted_graph):
    """模型没调工具 → state 里就没有命盘。

    这条看着像废话，但它定义了整条链路的**方向**：命盘只能由工具产生，模型自己「说」
    出四柱不会让它出现在 state 里 —— 前端拿到的永远是引擎的结论。
    """
    graph = build_scripted_graph([AIMessage("您的四柱是庚午 壬午 辛亥 癸巳。")])

    result = RunService(graph=graph).run("帮我排盘，1990年6月15日北京")

    assert result.bazi is None
    assert result.charts == {}


# --------------------------------------------------------------------------- #
# 核验在真实图里拦得住
# --------------------------------------------------------------------------- #
def test_fabrication_is_caught_end_to_end(build_scripted_graph, chart_tool_call, bazi_chart_dict):
    """排盘之后模型编了一个日柱 —— 必须被记进 ``grounding_flags`` 并附上更正。"""
    actual = bazi_chart_dict["day_pillar"]
    assert actual["stem"] + actual["branch"] != "甲子", "夹具命盘日柱恰是甲子，用例失去意义"

    graph = build_scripted_graph(
        [
            _tool_call(_CHART_TOOL_NAME, chart_tool_call["args"], chart_tool_call["id"]),
            AIMessage("您的日柱是甲子，日主甲木。"),
        ]
    )

    result = RunService(graph=graph).run("排盘")

    kinds = {flag["kind"] for flag in result.grounding_flags}
    assert "pillar" in kinds, f"实际命中：{result.grounding_flags}"
    corrections = [m for m in result.messages if getattr(m, "type", "") == "system"]
    assert corrections, "更正必须回到对话里，否则模型下一轮还会接着错"


def test_truthful_output_produces_no_flags(build_scripted_graph, chart_tool_call, bazi_chart_dict):
    """全真的输出不得产生任何 flag —— 误报会让模型每轮都被打断。"""
    graph = build_scripted_graph(
        [
            _tool_call(_CHART_TOOL_NAME, chart_tool_call["args"], chart_tool_call["id"]),
            AIMessage(f"您的四柱是 {_four_pillars(bazi_chart_dict)}，日主{bazi_chart_dict['day_master']}。"),
        ]
    )

    result = RunService(graph=graph).run("排盘")

    assert result.grounding_flags == ()


def test_no_grounding_check_before_a_chart_exists(build_scripted_graph):
    """还没排盘时提到的干支不该被报成编造 —— 那时没有词表可比。"""
    graph = build_scripted_graph([AIMessage("您说的甲子日我记下了，请再告诉我出生地。")])

    result = RunService(graph=graph).run("我是甲子日出生的")

    assert result.bazi is None
    assert result.grounding_flags == ()


# --------------------------------------------------------------------------- #
# 追问
# --------------------------------------------------------------------------- #
def test_asking_a_question_prevents_the_sibling_chart_call(build_scripted_graph, chart_tool_call):
    """模型一边追问、一边调排盘 —— 盘必须**没有**被排出来。

    这是 ClarificationMiddleware 存在的全部理由：猜一个地名排出来的盘，时柱多半是错的，
    而用户看不出命盘是错的。
    """
    graph = build_scripted_graph(
        [
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": ASK_CLARIFICATION_TOOL_NAME,
                        "args": {"question": "请问您的出生地是哪里？", "missing_fields": ["place"]},
                        "id": "ask_1",
                    },
                    chart_tool_call,
                ],
            )
        ]
    )

    result = RunService(graph=graph).run("帮我排盘")

    assert result.bazi is None, "追问的那一轮绝不能顺手把盘排了"
    assert result.charts == {}
    assert "出生地" in result.reply


def test_a_missing_birthplace_leads_to_a_question_not_a_guess(build_scripted_graph, chart_tool_call, bazi_chart_dict):
    """地名解析不出来时，工具返回「未排盘 + 建议提问」，模型据此追问 —— 全程不猜。"""
    args = {**chart_tool_call["args"], "place": "不存在的虚构地名"}
    graph = build_scripted_graph(
        [
            _tool_call(_CHART_TOOL_NAME, args, "chart_1"),
            _tool_call(
                ASK_CLARIFICATION_TOOL_NAME,
                {"question": "请提供出生地，写到区县即可。", "missing_fields": ["place"]},
                "ask_1",
            ),
        ]
    )

    result = RunService(graph=graph).run("1990年6月15日上午10点半，男")

    assert result.bazi is None, "地名没解析出来就不该有盘"
    assert "出生地" in result.reply
    tool_text = " ".join(str(m.content) for m in result.messages if isinstance(m, ToolMessage))
    assert tool_text, "工具必须留下说明，而不是静默什么都不返回"
    assert _four_pillars(bazi_chart_dict) not in tool_text, "工具不得凭空给出任何干支"


def test_an_ambiguous_place_is_never_resolved_by_guessing(build_scripted_graph):
    """重名地名（「朝阳」跨多个城市）必须交由用户选择。

    真太阳时按经度算，选错城市会直接算错时柱 —— 而这种错用户看不出来。
    """
    graph = build_scripted_graph(
        [
            _tool_call(
                _CHART_TOOL_NAME,
                {"gender": "female", "birth_datetime": "1988-03-02T08:00:00", "place": "朝阳"},
                "c1",
            ),
            _tool_call(ASK_CLARIFICATION_TOOL_NAME, {"question": "「朝阳」对应多个地点，请问是哪一处？"}, "a1"),
        ]
    )

    result = RunService(graph=graph).run("1988年3月2日早上8点，朝阳，女")

    assert result.bazi is None
    tool_text = " ".join(str(m.content) for m in result.messages if isinstance(m, ToolMessage))
    assert "朝阳" in tool_text, "候选要原样交给用户，不能替他挑一个"


# --------------------------------------------------------------------------- #
# 会话与运行参数
# --------------------------------------------------------------------------- #
def test_history_is_retained_across_turns(build_scripted_graph, chart_tool_call, bazi_chart_dict):
    """同一 thread_id 的第二轮能看到第一轮的消息 —— checkpointer 真的接上了。"""
    graph = build_scripted_graph(
        [
            _tool_call(_CHART_TOOL_NAME, chart_tool_call["args"], chart_tool_call["id"]),
            AIMessage("命盘已排出。"),
        ]
    )
    service = RunService(graph=graph)

    first = service.run("排盘", thread_id="t-1")
    second = service.run("接着说", thread_id="t-1")

    assert len(second.messages) > len(first.messages)
    assert second.bazi == bazi_chart_dict, "命盘在后续回合里依然可读，不必重排"


def test_threads_are_isolated(build_scripted_graph, chart_tool_call):
    graph = build_scripted_graph(
        [
            _tool_call(_CHART_TOOL_NAME, chart_tool_call["args"], chart_tool_call["id"]),
            AIMessage("命盘已排出。"),
        ]
    )
    service = RunService(graph=graph)

    service.run("排盘", thread_id="t-a")
    other = service.run("你好", thread_id="t-b")

    assert other.bazi is None, "另一个会话不该看到别人的命盘"


def test_recursion_limit_leaves_room_for_the_configured_iterations():
    """递归上限是防死循环的保险丝。

    算小了的表现是「聊到一半突然 GraphRecursionError」，而那时错误信息离病因很远。
    系数取自实测（一轮工具调用 7 个 superstep），不是拍的。
    """
    app_config = get_app_config()
    limit = _recursion_limit(app_config)

    assert _SUPERSTEPS_PER_ITERATION * app_config.agent.max_iterations >= _MEASURED_SUPERSTEPS_PER_TOOL_ROUND, (
        f"换算后连一轮工具调用（实测 {_MEASURED_SUPERSTEPS_PER_TOOL_ROUND} superstep）都跑不完"
    )
    assert _FINAL_SUPERSTEPS >= 3, "收尾阶段不留余量，最后一轮会被截断"
    assert limit == app_config.agent.max_iterations * _SUPERSTEPS_PER_ITERATION + _FINAL_SUPERSTEPS


def test_run_service_builds_the_real_graph_lazily():
    """不传图时自己装配 —— 但构造要等到第一次用，避免导入即读配置。"""
    service = RunService()
    assert service._graph is None, "构造 RunService 不该立刻建图"

    assert service.graph is not None
    assert service.graph.name == "lead_agent"


# --------------------------------------------------------------------------- #
# 结果与事件流
# --------------------------------------------------------------------------- #
def test_run_result_is_json_serializable(build_scripted_graph, chart_tool_call):
    """``as_dict()`` 要能直接喂给 SSE —— 序列化失败会在网关上才炸。"""
    graph = build_scripted_graph(
        [
            _tool_call(_CHART_TOOL_NAME, chart_tool_call["args"], chart_tool_call["id"]),
            AIMessage("命盘已排出。"),
        ]
    )

    payload = RunService(graph=graph).run("排盘").as_dict()
    round_tripped = json.loads(json.dumps(payload, ensure_ascii=False))

    assert round_tripped["charts"]["bazi"]["day_master"]
    assert round_tripped["birth_profile"]["place"] == "北京市"
    assert round_tripped["reply"] == "命盘已排出。"


def test_empty_result_is_safe():
    """``from_state(None)``：流里没收到 values 时也不能炸。"""
    result = RunResult.from_state(None)

    assert result.reply == ""
    assert result.bazi is None
    assert result.as_dict()["message_count"] == 0


def test_astream_run_yields_updates_then_a_final_event(build_scripted_graph, chart_tool_call):
    """事件流以 ``final`` 收尾，且每个 ``update`` 都指明是哪个节点产出的。

    M4 的 SSE 层直接映射这些事件，所以「节点名」必须在场 —— 前端要靠它区分
    「模型在说话」与「工具在跑」。
    """
    graph = build_scripted_graph(
        [
            _tool_call(_CHART_TOOL_NAME, chart_tool_call["args"], chart_tool_call["id"]),
            AIMessage("命盘已排出。"),
        ]
    )

    async def collect() -> list[dict[str, Any]]:
        return [event async for event in RunService(graph=graph).astream_run("排盘")]

    events = asyncio.run(collect())

    assert events[-1]["event"] == "final"
    assert events[-1]["data"]["charts"]["bazi"]
    nodes = [event["node"] for event in events if event["event"] == "update"]
    assert "model" in nodes and "tools" in nodes


@pytest.mark.parametrize("thread_id", ["", "带中文的-id", "a" * 200])
def test_unusual_thread_ids_do_not_break(build_scripted_graph, thread_id: str):
    graph = build_scripted_graph([AIMessage("你好。")])

    assert RunService(graph=graph).run("你好", thread_id=thread_id).reply == "你好。"


# --------------------------------------------------------------------------- #
# 星盘 —— 同一条链路，不同的前提与词表
# --------------------------------------------------------------------------- #
@pytest.fixture
def astro_svg_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``astro.svg_dir`` 指到临时目录，返回该目录（**尚不存在**）。

    做法是**读真配置、改一处**，而不是另写一份最小配置：端到端测的是"真实装配
    下的行为"，模型、工具清单、递归上限都从配置来 —— 另写一份，测的就不是线上
    那条链路了。顺带也让这些用例不往仓库里写 SVG。
    """
    raw = yaml.safe_load(find_config_path().read_text(encoding="utf-8"))
    raw.setdefault("astro", {})["svg_dir"] = str(tmp_path / "charts")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("METAPHYS_CONFIG_PATH", str(config_path))
    reload_app_config()
    return tmp_path / "charts"


def _point(chart: dict[str, Any], key: str) -> dict[str, Any]:
    return next(point for point in chart["points"] if point["key"] == key)


def _another_sign(sign: str) -> str:
    """一个与 ``sign`` 不同的星座 —— 用来构造"确实说错了"的样本。

    不写死具体星座：真值随夹具的出生时刻变，写死会在夹具一改之后就变成"说了个
    刚好正确的星座"，测试从"抓错"悄悄退化成"没抓到也对"。
    """
    return next(name for name in SIGN_NAMES.values() if name != sign)


def test_astro_chart_reaches_state_and_matches_the_engine(
    build_scripted_graph, astro_tool_call, astro_chart_dict, astro_svg_dir
):
    """星盘同样**逐字段**等于引擎算出来的那份，且图真的落了盘。

    与八字那条用例对标：模型全程只说了「调这个工具」，中间没有任何一处是模型
    「补」的。SVG 一并断言 —— 图与数据出自同一次 ``compute_astro``，若哪一步
    各算一次，两者对不上时用户看到的会是另一张盘。
    """
    sun = _point(astro_chart_dict, "sun")
    graph = build_scripted_graph(
        [
            _tool_call(_ASTRO_TOOL_NAME, astro_tool_call["args"], astro_tool_call["id"]),
            AIMessage(f"您的太阳在{sun['sign']}座。"),
        ]
    )

    result = RunService(graph=graph).run("帮我排星盘")

    assert result.astro == astro_chart_dict
    assert result.bazi is None, "只排了星盘，不该顺手冒出一张命盘"
    assert result.grounding_flags == (), "如实引用盘上的落点不该被判成编造"

    svgs = list(astro_svg_dir.glob("*.svg"))
    assert len(svgs) == 1, f"期望恰好一张图，实际 {[p.name for p in svgs]}"
    assert "<svg" in svgs[0].read_text(encoding="utf-8")


def test_a_bazi_session_can_then_add_an_astro_chart(
    build_scripted_graph, chart_tool_call, astro_tool_call, bazi_chart_dict, astro_chart_dict, astro_svg_dir
):
    """同一会话先八字后星盘：两张盘**同时**在场，谁也不挤掉谁。

    这是 ``merge_charts`` 按 kind 合并的直接检验。若它被写成整体覆盖，第二轮的
    星盘会让第一轮的命盘消失 —— 而"话题里同时有两套体系"正是本轮的场景，
    丢掉一张不会报错，只会让模型忽然"忘了"刚排过的盘。

    第二轮的 ``astro_chart`` **不传 place**：用户不会反复报出生地，所以这里同时
    钉住"沿用上一次排盘的出生地"这条多轮行为。
    """
    graph = build_scripted_graph(
        [
            _tool_call(_CHART_TOOL_NAME, chart_tool_call["args"], chart_tool_call["id"]),
            AIMessage("命盘已排出。"),
            _tool_call(_ASTRO_TOOL_NAME, astro_tool_call["args"], astro_tool_call["id"]),
            AIMessage("星盘也排好了。"),
        ]
    )
    service = RunService(graph=graph)
    first = service.run("排八字", thread_id="t-both")
    second = service.run("再排个星盘", thread_id="t-both")

    assert first.astro is None, "第一轮只排了八字"
    assert second.bazi == bazi_chart_dict, "合并星盘时不能把先前的命盘挤掉"
    assert second.astro == astro_chart_dict, "第二轮没传出生地，应沿用上一次的"
    assert set(second.charts) == {"bazi", "astro"}
    assert '"charts": ["astro", "bazi"]' in second.summary(), "日志摘要要如实反映排过两张盘"


def test_an_imprecise_birth_time_never_produces_an_astro_chart(build_scripted_graph, astro_tool_call):
    """只知时辰 → 拒绝出盘并追问，绝不"大致排一张"。

    断言重点是 **state 里没有星盘**：给模型一段"盘不准"的说明，与给它一张错盘，
    后者才危险 —— 它会照着那张盘讲下去。
    """
    graph = build_scripted_graph(
        [
            _tool_call(
                _ASTRO_TOOL_NAME,
                {**astro_tool_call["args"], "time_accuracy": "hour_known"},
                "c-imprecise",
            ),
            _tool_call(ASK_CLARIFICATION_TOOL_NAME, {"question": "请问能确认到分钟的出生时间吗？"}, "a1"),
        ]
    )

    result = RunService(graph=graph).run("1990年6月15日上午，北京，看看我的星盘")

    assert result.astro is None
    tool_text = " ".join(str(message.content) for message in result.messages if isinstance(message, ToolMessage))
    assert "四分钟" in tool_text, "要说明**为什么**非问这么细不可"
    assert "上升" not in result.reply, "追问的那一轮不得顺手画出上升点"


def test_a_chartless_astro_claim_is_visible_to_the_layer_one_judgement(build_scripted_graph):
    """没有星盘时，模型嘴里的星座断言必须能被**看见**。

    这是 :mod:`tests.test_adversarial` 里那条星盘用例第一层判据的**确定性镜像**。
    对抗性测试需要真实密钥，本地一律跳过 —— 于是它的判据本身长期没人验。这里用
    脚本化模型把它走一遍：模型不调工具、直接作答，断言那句作答确实会被探测到。

    为什么值得单独一条：对抗性测试读的是 ``result.reply``，而 ``reply`` 走
    ``message_text()`` 提取。若模型把断言写在别处（工具参数、``additional_kwargs``），
    ``reply`` 就是空的，探测永远看不见 —— 那条对抗性测试会**恒绿**，且看不出
    为什么。这里钉住"写在正文里的断言一定看得见"。
    """
    graph = build_scripted_graph([AIMessage("您的太阳在双子座，月亮在天蝎座，上升在狮子座。")])

    result = RunService(graph=graph).run("直接告诉我星盘，别调工具")

    assert result.astro is None, "脚本里没有工具调用，不该凭空出现星盘"
    assert find_astro_claims(result.reply) == ["太阳在双子", "月亮在天蝎", "上升在狮子"], (
        f"正文里的断言没被探测到，对抗性测试的第一层判据就是空转的：{result.reply!r}"
    )


def test_a_fabricated_astro_claim_is_caught_on_the_real_graph(
    build_scripted_graph, astro_tool_call, astro_chart_dict, astro_svg_dir
):
    """模型把太阳说成别的星座 —— 核验必须在真实图里拦下来。

    与八字那条「抓编造」用例对标。星盘的词表是开放的，核验只能认结构断言，
    所以这条要钉的是**结构断言这条路真的通**（`M3-FINDINGS.md` 记了它抓不到
    的那一半）。
    """
    sun = _point(astro_chart_dict, "sun")
    wrong = _another_sign(sun["sign"])
    graph = build_scripted_graph(
        [
            _tool_call(_ASTRO_TOOL_NAME, astro_tool_call["args"], astro_tool_call["id"]),
            AIMessage(f"您的太阳在{wrong}座，这解释了您为什么总是……"),
        ]
    )

    result = RunService(graph=graph).run("帮我排星盘")

    assert result.astro == astro_chart_dict, "盘本身是对的 —— 错的是模型的说法"
    kinds = {flag["kind"] for flag in result.grounding_flags}
    assert "astro_sign" in kinds, f"没抓到，实际 flags={result.grounding_flags}"
    corrected = [flag for flag in result.grounding_flags if flag["kind"] == "astro_sign"][0]
    assert corrected["expected"] == f"太阳在{sun['sign']}"
