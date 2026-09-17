"""设计审查回归：用户可见输出、当前命盘和出生时间契约。"""

import asyncio
import json
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from metaphys.agents.thread_state import merge_charts
from metaphys.engines.china_time import to_utc
from metaphys.middlewares.grounding import GroundingMiddleware
from metaphys.runtime import RunService
from metaphys.schemas.chart import BirthProfile
from metaphys.tools.builtins.astro_chart import build_astro_command
from metaphys.tools.builtins.bazi_chart import build_chart_command
from pydantic import ValidationError


def call(tool):
    return AIMessage("", tool_calls=[tool])


def test_withhold_bad_reply_and_do_not_poison_next_turn(build_scripted_graph, chart_tool_call):
    bad = "您的日柱是甲子，日主甲木。"
    graph = build_scripted_graph([call(chart_tool_call), AIMessage(bad), AIMessage("我们可以重新解读。")])
    service = RunService(graph=graph)
    first = service.run("排盘")
    assert first.reply != bad
    assert "未通过" in first.reply
    assert first.as_dict()["response_status"] == "withheld"
    assert first.response_flags
    assert all(f["message_id"] for f in first.response_flags)
    second = service.run("请重新解读")
    assert second.reply == "我们可以重新解读。"
    assert second.grounding_flags  # 审计历史保留
    assert second.response_flags == []
    assert second.as_dict()["response_status"] == "completed"


def test_public_stream_never_releases_unchecked_messages(build_scripted_graph, chart_tool_call):
    bad = "您的日柱是甲子，日主甲木。"
    graph = build_scripted_graph([call(chart_tool_call), AIMessage(bad)])

    async def collect():
        return [event async for event in RunService(graph=graph).astream_run("排盘")]

    events = asyncio.run(collect())
    for event in events:
        json.dumps(event, ensure_ascii=False, allow_nan=False)
        assert event["schema_version"] == 1
    assert len({e["run_id"] for e in events}) == 1
    assert events[-1]["data"]["response_status"] == "withheld"
    assert events[-1]["data"]["reply"] != bad
    for event in events[:-1]:
        assert event["data"] == {"status": "running"}
        assert bad not in json.dumps(event, ensure_ascii=False)
        assert "svg_path" not in json.dumps(event)


@pytest.mark.parametrize(
    "changed",
    [
        {"birth_datetime": "1991-06-15T10:30:00"},
        {"longitude": 120.0},
        {"latitude": 40.0},
        {"time_accuracy": "unknown"},
        {"name": "另一个人"},
    ],
)
def test_changed_profile_invalidates_other_kind(bazi_chart_dict, astro_chart_dict, changed):
    astro = deepcopy(astro_chart_dict)
    astro["profile"].update(changed)
    merged = merge_charts({"bazi": bazi_chart_dict}, {"astro": astro})
    assert set(merged) == {"astro"}
    assert merged["astro"] == astro


def test_same_profile_and_explicit_invalidation(bazi_chart_dict, astro_chart_dict):
    original = {"bazi": bazi_chart_dict}
    merged = merge_charts(original, {"astro": astro_chart_dict})
    assert set(merged) == {"bazi", "astro"}
    assert set(merge_charts(merged, {"bazi": None})) == {"astro"}
    assert merge_charts(merged, {}) == merged
    assert original == {"bazi": bazi_chart_dict}


def test_lunar_bazi_and_solar_astro_share_civil_profile(astro_chart_dict):
    # 1990 农历五月廿三 = 公历六月十五；日期转换完全交给八字工具。
    command = build_chart_command(
        gender="male", birth_datetime="1990-05-23T10:30:00", calendar="lunar", place="北京市", tool_call_id="lunar"
    )
    bazi = command.update["charts"]["bazi"]
    assert bazi["clock_time"] == astro_chart_dict["profile"]["birth_datetime"]
    assert set(merge_charts({"bazi": bazi}, {"astro": astro_chart_dict})) == {"bazi", "astro"}


def test_active_request_uses_current_charts_only(bazi_chart_dict, astro_chart_dict):
    stale = {"bazi": bazi_chart_dict}
    newer = deepcopy(astro_chart_dict)
    newer["profile"]["birth_datetime"] = "1991-06-15T10:30:00"
    active = merge_charts(stale, {"astro": newer})
    request = SimpleNamespace(
        state={"charts": active}, messages=[HumanMessage("接着解读")], override=lambda **kwargs: kwargs
    )
    result = GroundingMiddleware._active_request(request)
    context = result["messages"][-1].content
    assert "1991-06-15" in context
    assert set(json.loads(context.split("\n", 1)[1])) == {"astro"}
    assert "已过期" in context
    assert len(request.messages) == 1


@pytest.mark.parametrize("stamp", ["2000-06-15T10:30:00Z", "2000-06-15T10:30:00+08:00", "1990-09-16T01:30:00+09:00"])
def test_timezone_offsets_are_rejected_at_contract_and_time_boundary(stamp):
    moment = datetime.fromisoformat(stamp)
    with pytest.raises(ValidationError, match="当地钟表时间"):
        BirthProfile(birth_datetime=moment, place="北京市", latitude=39.9, longitude=116.4)
    with pytest.raises(ValueError, match="显式 UTC 偏移"):
        to_utc(moment)
    for builder, extra in [(build_chart_command, {"gender": "male"}), (build_astro_command, {})]:
        command = builder(birth_datetime=stamp, place="北京市", tool_call_id="tz", **extra)
        assert "charts" not in command.update
        assert command.update["messages"][0].artifact["status"] == "needs_clarification"
        assert "UTC 偏移" in command.update["messages"][0].content


def prior_runtime(*messages):
    return SimpleNamespace(
        state={
            "birth_profile": {"birth_datetime": "1990-06-15T10:30:00", "time_accuracy": "hour_known"},
            "messages": list(messages),
        }
    )


@pytest.mark.parametrize(
    "text",
    [
        "没确认出生时间：1990-06-15 10:30:00",
        "确认出生时间：1990-06-15 10:31:00",
        "大概是10:30",
        "确认出生时间：1990-06-15 10:30:00，不确定",
    ],
)
def test_model_cannot_upgrade_precision_without_current_explicit_confirmation(text):
    command = build_astro_command(
        birth_datetime="1990-06-15T10:30:00",
        place="北京市",
        time_accuracy="exact",
        tool_call_id="reject",
        runtime=prior_runtime(HumanMessage(text)),
    )
    assert "charts" not in command.update
    assert command.update["messages"][0].artifact["status"] == "needs_clarification"
    assert "确认出生时间：1990-06-15 10:30:00" in command.update["messages"][0].content


def test_history_or_assistant_confirmation_is_not_user_confirmation():
    for messages in [
        [AIMessage("确认出生时间：1990-06-15 10:30:00")],
        [HumanMessage("确认出生时间：1990-06-15 10:30:00"), HumanMessage("还没核对清楚")],
    ]:
        command = build_astro_command(
            birth_datetime="1990-06-15T10:30:00",
            place="北京市",
            tool_call_id="reject",
            runtime=prior_runtime(*messages),
        )
        assert "charts" not in command.update


def test_same_time_can_be_confirmed_by_current_user(tmp_path, monkeypatch):
    monkeypatch.setattr("metaphys.tools.builtins.astro_chart.resolve_svg_dir", lambda: tmp_path)
    command = build_astro_command(
        birth_datetime="1990-06-15T10:30:00",
        place="北京市",
        time_accuracy="exact",
        tool_call_id="accept",
        runtime=prior_runtime(HumanMessage("确认出生时间：1990-06-15 10:30:00")),
    )
    assert command.update["birth_profile"]["time_accuracy"] == "exact"
    assert command.update["messages"][0].artifact["status"] == "ok"
    assert command.update["charts"]["astro"]["profile"]["birth_datetime"] == "1990-06-15T10:30:00"


@pytest.mark.parametrize("reverse", [False, True])
def test_changed_profile_in_real_multiturn_graph(
    build_scripted_graph, chart_tool_call, astro_tool_call, tmp_path, monkeypatch, reverse
):
    monkeypatch.setattr("metaphys.tools.builtins.astro_chart.resolve_svg_dir", lambda: tmp_path)
    original = [chart_tool_call, astro_tool_call]
    if reverse:
        original.reverse()
    second = deepcopy(original[1])
    second["args"]["birth_datetime"] = "1991-06-15T10:30:00"
    graph = build_scripted_graph([call(original[0]), AIMessage("已排盘"), call(second), AIMessage("已更新")])
    service = RunService(graph=graph)
    first = service.run("排盘")
    assert len(first.charts) == 1
    final = service.run("我之前把年份说错了，是1991年")
    expected_kind = "bazi" if reverse else "astro"
    assert set(final.charts) == {expected_kind}
    assert final.birth_profile["birth_datetime"] == "1991-06-15T10:30:00"
    assert final.charts[expected_kind]["profile"]["birth_datetime"] == final.birth_profile["birth_datetime"]


def test_same_time_confirmation_reaches_real_graph(
    build_scripted_graph, chart_tool_call, astro_tool_call, tmp_path, monkeypatch
):
    monkeypatch.setattr("metaphys.tools.builtins.astro_chart.resolve_svg_dir", lambda: tmp_path)
    approximate = deepcopy(chart_tool_call)
    approximate["args"]["time_accuracy"] = "hour_known"
    graph = build_scripted_graph(
        [call(approximate), AIMessage("请核对出生时间"), call(astro_tool_call), AIMessage("已排盘")]
    )
    service = RunService(graph=graph)
    first = service.run("大概是10点半")
    assert first.birth_profile["time_accuracy"] == "hour_known"
    final = service.run("确认出生时间：1990-06-15 10:30:00")
    assert final.astro is not None
    assert final.birth_profile["time_accuracy"] == "exact"
    assert final.bazi is None  # 旧精度下的盘不能继续当成当前有效盘


def test_bad_content_with_tool_calls_preserves_tool_protocol(build_scripted_graph, chart_tool_call):
    # 已有盘后，带错误正文的工具调用也要撤回正文，但不能留下没有响应的 tool_call。
    graph = build_scripted_graph(
        [
            call(chart_tool_call),
            AIMessage("已排盘"),
            AIMessage(
                "日柱是甲子",
                tool_calls=[
                    {
                        "name": "ask_clarification",
                        "args": {"question": "您想看哪个方面？"},
                        "id": "ask_next",
                    }
                ],
            ),
        ]
    )
    service = RunService(graph=graph)
    service.run("排盘")
    result = service.run("接着说")
    assert "日柱是甲子" not in result.reply
    from langchain_core.messages import ToolMessage

    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "ask_next" for m in result.messages)
