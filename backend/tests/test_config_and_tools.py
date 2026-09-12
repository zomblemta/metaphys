"""配置层、反射层、工具装配的测试。

这三层有一个共同点：**写错时不会立刻炸，而是在很远的地方以看不懂的形式炸**。

- ``use:`` 路径拼错 → 报的是 ``ModuleNotFoundError``，把人引向"依赖没装"
- ``$ENV`` 缺失 → 留空字符串，服务起得来，请求时才 401
- 工具名与配置不符 → 模型看到的名字和配置里写的不是一个，排查时两边对不上

所以这里的断言大多不是"功能对不对"，而是"**报错信息指不指得准**"。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from metaphys.config import (
    AppConfig,
    ModelConfig,
    ToolConfig,
    load_app_config,
)

# 从子模块取：这个常量是 find_config_path 的输入约定，包 __init__ 未对外暴露它，
# 而这条用例测的正是"$METAPHYS_CONFIG_PATH 指错文件时怎么报错"。
from metaphys.config.app_config import ENV_PATH_VAR
from metaphys.reflection import resolve_class, resolve_variable
from metaphys.tools import load_tools
from metaphys.tools.builtins.bazi_chart import bazi_chart_tool, build_chart_command
from metaphys.tools.builtins.clarification import ASK_CLARIFICATION_TOOL_NAME
from metaphys.tools.builtins.geo import lookup_birthplace_tool


# --------------------------------------------------------------------------- #
# 配置：$ENV 展开与 fail fast
# --------------------------------------------------------------------------- #
def _write_config(directory: Path, payload: dict[str, Any]) -> Path:
    path = directory / "config.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def _minimal_config(**model_extras: Any) -> dict[str, Any]:
    """一份能通过校验的最小配置。``model_extras`` 追加到 ``models[0]`` 上。"""
    return {
        "config_version": 1,
        "models": [{"name": "m", "use": "langchain_deepseek:ChatDeepSeek", **model_extras}],
        "default_model": "m",
        "tools": [],
    }


def test_env_reference_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("METAPHYS_TEST_KEY", "a-real-looking-value")
    path = _write_config(tmp_path, _minimal_config(api_key="$METAPHYS_TEST_KEY"))

    model = load_app_config(path).get_model_config()

    assert model.constructor_kwargs()["api_key"] == "a-real-looking-value"


def test_missing_env_var_fails_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """变量没设时必须**启动即报错**，而不是留空串。

    留空串的失败模式是：服务正常起、日志干干净净，直到第一个用户请求才 401 ——
    那时离病因已经很远了。
    """
    monkeypatch.delenv("METAPHYS_TEST_ABSENT", raising=False)
    path = _write_config(tmp_path, _minimal_config(api_key="$METAPHYS_TEST_ABSENT"))

    with pytest.raises(ValueError, match="METAPHYS_TEST_ABSENT"):
        load_app_config(path)


@pytest.mark.parametrize("blank", ["", "   ", "\t"], ids=["空串", "空格", "制表符"])
def test_a_blank_env_var_is_treated_as_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blank: str):
    """``.env`` 里写了键却没填值、或 CI 里设成 ``" "`` —— 必须与"没写"同样处理。

    纯空白曾能蒙混过关，于是一个用不了的密钥会被当成配好了：服务正常起，
    第一个请求才 401。而"密钥没配"与"密钥认证失败"看起来是两回事，排障成本高得多。
    """
    monkeypatch.setenv("METAPHYS_TEST_BLANK", blank)
    path = _write_config(tmp_path, _minimal_config(api_key="$METAPHYS_TEST_BLANK"))

    with pytest.raises(ValueError, match="METAPHYS_TEST_BLANK"):
        load_app_config(path)


def test_env_error_names_the_exact_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """报错要指出**哪一个键**引用了缺失变量。

    一份配置有几十个键，只说"缺环境变量"等于让人挨个找。这里钉住的是报错质量，
    不是行为 —— 而报错质量恰恰是这一层唯一的产品。
    """
    monkeypatch.delenv("METAPHYS_TEST_ABSENT", raising=False)
    path = _write_config(tmp_path, _minimal_config(api_key="$METAPHYS_TEST_ABSENT"))

    with pytest.raises(ValueError) as excinfo:
        load_app_config(path)

    assert "models[0].api_key" in str(excinfo.value), f"报错没指出位置：{excinfo.value}"


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("key: [unclosed", "YAML"),
        ("", "空文件"),
        ("- 只是一个列表\\n", "顶层必须是映射"),
    ],
    ids=["非法 YAML", "空文件", "顶层不是映射"],
)
def test_malformed_config_is_rejected_with_a_clear_reason(tmp_path: Path, raw: str, match: str):
    path = tmp_path / "config.yaml"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        load_app_config(path)


def test_env_path_override_must_point_at_a_real_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``$METAPHYS_CONFIG_PATH`` 指向不存在的文件时要说清楚，而不是回退到自动查找。

    静默回退会让人以为配置生效了，实际跑的是另一份 —— 改了半天没反应就是这么来的。
    """
    monkeypatch.setenv(ENV_PATH_VAR, str(tmp_path / "并没有这个文件.yaml"))

    with pytest.raises(FileNotFoundError, match=ENV_PATH_VAR):
        load_app_config()


def test_unknown_keys_pass_through_to_the_model_constructor(tmp_path: Path):
    """``extra="allow"`` 是有意的：provider 的专有参数不该要求改代码。

    ``model`` / ``temperature`` 都不是 ``ModelConfig`` 的声明字段，它们必须活着走到
    模型构造函数里去。
    """
    path = _write_config(tmp_path, _minimal_config(model="deepseek-chat", temperature=0.7))

    kwargs = load_app_config(path).get_model_config().constructor_kwargs()

    assert kwargs["model"] == "deepseek-chat"
    assert kwargs["temperature"] == 0.7


@pytest.mark.parametrize("missing", ["name", "use"])
def test_model_requires_name_and_use(tmp_path: Path, missing: str):
    entry: dict[str, Any] = {"name": "m", "use": "langchain_deepseek:ChatDeepSeek"}
    del entry[missing]
    path = _write_config(tmp_path, {"models": [entry]})

    with pytest.raises(ValueError):
        load_app_config(path)


def test_duplicate_model_names_are_rejected(tmp_path: Path):
    """按名字取模型，重名就有歧义 —— 取到哪个取决于顺序，这种不确定性不该存在。"""
    payload = _minimal_config()
    payload["models"].append(dict(payload["models"][0]))
    path = _write_config(tmp_path, payload)

    with pytest.raises(ValueError, match="重复"):
        load_app_config(path)


def test_default_model_must_exist(tmp_path: Path):
    payload = {**_minimal_config(), "default_model": "并不存在的模型"}
    path = _write_config(tmp_path, payload)

    with pytest.raises(ValueError, match="default_model"):
        load_app_config(path)


def test_config_requires_at_least_one_model(tmp_path: Path):
    path = _write_config(tmp_path, {"models": []})

    with pytest.raises(ValueError, match="未配置任何模型"):
        load_app_config(path)


def test_get_model_config_falls_back_to_the_first(tmp_path: Path):
    """没配 ``default_model`` 时退到第一个 —— 单一 provider 的配置不必写这一行。"""
    payload = {"models": [{"name": "only", "use": "langchain_deepseek:ChatDeepSeek"}]}
    path = _write_config(tmp_path, payload)

    assert load_app_config(path).get_model_config().name == "only"


def test_get_model_config_rejects_an_unknown_name(tmp_path: Path):
    path = _write_config(tmp_path, _minimal_config())

    with pytest.raises(ValueError, match="未找到名为"):
        load_app_config(path).get_model_config("并不存在")


# --------------------------------------------------------------------------- #
# 反射：报错信息是这一层唯一的产品
# --------------------------------------------------------------------------- #
def test_resolves_a_real_dotted_path():
    tool = resolve_variable("metaphys.tools.builtins.geo:lookup_birthplace_tool")

    assert tool is lookup_birthplace_tool


def test_resolve_class_checks_subclass():
    model_class = resolve_class("langchain_deepseek:ChatDeepSeek", BaseChatModel)

    assert issubclass(model_class, BaseChatModel)


@pytest.mark.parametrize(
    ("path", "match"),
    [
        ("没有冒号的路径", "不是合法的 use: 路径"),
        (":只有属性名", "不完整"),
        ("只有模块名:", "不完整"),
    ],
    ids=["没有冒号", "缺模块", "缺属性"],
)
def test_malformed_paths_are_rejected(path: str, match: str):
    with pytest.raises(ImportError, match=match):
        resolve_variable(path)


def test_a_typo_in_the_module_name_points_at_the_config():
    """模块名拼错时要提示"检查 use: 拼写"，而不是让人以为是依赖没装。

    真实场景：``langchain_deepseak`` 报出的 ``ModuleNotFoundError`` 会把人的第一反应
    引向 ``pip install``，而依赖其实装得好好的。
    """
    with pytest.raises(ImportError, match="拼写"):
        resolve_variable("langchain_deepseak:ChatDeepSeek")


def test_a_missing_attribute_names_the_attribute():
    with pytest.raises(ImportError, match="并不存在的类"):
        resolve_variable("langchain_deepseek:并不存在的类")


def test_wrong_type_is_a_type_error_not_an_import_error():
    """类型不符与路径不存在是两类问题 —— 混成一个异常会让调用方没法分别处理。"""
    with pytest.raises(TypeError, match="期望"):
        resolve_variable("metaphys.tools.builtins.geo:lookup_birthplace_tool", str)


def test_resolve_class_rejects_a_non_class():
    with pytest.raises(TypeError, match="不是类"):
        resolve_class("metaphys.tools.builtins.geo:lookup_birthplace_tool")


def test_resolve_class_rejects_a_wrong_base():
    """取到的是类、但不是期望类型的子类 —— 与"取到的不是类"要分开报。

    两种错的处置方式不同：前者是选错了对象，后者是 use: 指向了一个实例。
    """
    with pytest.raises(TypeError, match="不是 .* 的子类"):
        resolve_class("pathlib:Path", BaseChatModel)


# --------------------------------------------------------------------------- #
# 工具装配
# --------------------------------------------------------------------------- #
def _tool_config_app(name: str, use: str) -> AppConfig:
    return AppConfig(
        models=[ModelConfig(name="m", use="langchain_deepseek:ChatDeepSeek")],
        tools=[ToolConfig(name=name, use=use)],
    )


def test_tool_name_mismatch_warns_but_uses_the_real_name(caplog: pytest.LogCaptureFixture):
    """配置写的名字与工具自身不符时**告警**，但以工具自身的名字为准。

    不报错是有意的：``name`` 只是配置侧的标识，真正生效的是 ``@tool("...")`` 里那个。
    但两者不一致通常意味着改了一处忘了另一处，值得在日志里留痕。
    """
    config = _tool_config_app("写错了的名字", "metaphys.tools.builtins.geo:lookup_birthplace_tool")

    with caplog.at_level(logging.WARNING):
        tools = load_tools(config)

    assert [tool.name for tool in tools] == ["lookup_birthplace", ASK_CLARIFICATION_TOOL_NAME]
    assert any("写错了的名字" in record.message for record in caplog.records), "名字不符必须留痕"


def test_duplicate_tool_names_are_rejected():
    """工具重名会让模型看到两个同名 tool，调用结果不可预期 —— 直接拒绝。"""
    config = AppConfig(
        models=[ModelConfig(name="m", use="langchain_deepseek:ChatDeepSeek")],
        tools=[
            ToolConfig(name="a", use="metaphys.tools.builtins.geo:lookup_birthplace_tool"),
            ToolConfig(name="b", use="metaphys.tools.builtins.geo:lookup_birthplace_tool"),
        ],
    )

    with pytest.raises(ValueError, match="工具重名"):
        load_tools(config)


def test_clarification_is_mounted_even_with_no_tools_configured():
    """``ask_clarification`` 不走配置。

    它与 ``ClarificationMiddleware`` 是一对，少任何一个"信息不足先追问"这条防线就整体
    失效 —— 而配置漏写不会报错，只会让模型重新开始猜。所以由代码无条件挂载。
    """
    config = AppConfig(models=[ModelConfig(name="m", use="langchain_deepseek:ChatDeepSeek")], tools=[])

    tools = load_tools(config)

    assert [tool.name for tool in tools] == [ASK_CLARIFICATION_TOOL_NAME]


# --------------------------------------------------------------------------- #
# 工具：异常 → 结构化追问（而不是抛给模型）
# --------------------------------------------------------------------------- #
_DST_GAP = "1986-05-04T02:30:00"
"""夏令时起始日的跳变区间 —— 该钟表时刻在现实中不存在。"""


def _message_of(command: Any) -> ToolMessage:
    messages = command.update["messages"]
    assert len(messages) == 1 and isinstance(messages[0], ToolMessage)
    return messages[0]


def test_a_nonexistent_clock_time_becomes_a_question_not_an_exception():
    """夏令时缺口 → 追问。

    这是 M1 留给 M2 的作业：引擎抛 ``NonexistentLocalTimeError`` 是对的（不能猜），
    但工具**不能**把它抛给模型 —— 那会让模型在"报错"与"编一个时间"之间二选一，
    而后者对用户伤害大得多。
    """
    command = build_chart_command(gender="male", birth_datetime=_DST_GAP, place="北京市", tool_call_id="c1")

    assert "charts" not in command.update, "排不了盘就不能返回半个命盘"
    message = _message_of(command)
    assert message.artifact["status"] == "needs_clarification"
    assert "birth_datetime" in message.artifact["missing_fields"]
    assert "夏令时" in message.content


def test_an_invalid_lunar_date_becomes_a_question_not_an_exception():
    """非法农历日期（闰月不存在）→ 追问，且要说清是闰月的问题。

    1990 年没有闰正月。
    """
    command = build_chart_command(
        gender="female",
        birth_datetime="1990-01-01T10:30:00",
        place="北京市",
        calendar="lunar",
        is_leap_month=True,
        tool_call_id="c2",
    )

    assert "charts" not in command.update
    message = _message_of(command)
    assert message.artifact["status"] == "needs_clarification"
    assert "is_leap_month" in message.artifact["missing_fields"]


@pytest.mark.parametrize(
    ("place", "why"),
    [("不存在的虚构地名", "地理库里没有"), ("", "压根没给")],
    ids=["查不到", "没给"],
)
def test_an_unresolvable_place_asks_instead_of_guessing(place: str, why: str):
    """地名解析不出来 → 追问，并且**一个干支都不许给**。"""
    command = build_chart_command(gender="male", birth_datetime="1990-06-15T10:30:00", place=place, tool_call_id="c3")

    assert "charts" not in command.update, f"地名{why}就排出了盘"
    message = _message_of(command)
    assert message.artifact["status"] == "needs_clarification"
    assert "place" in message.artifact["missing_fields"]


def test_an_ambiguous_place_hands_the_choices_to_the_user():
    """重名地名 → 列出候选交给用户选，绝不替他挑一个。

    「朝阳」在北京市、辽宁朝阳市等地都有 —— 经度不同会算出不同的时柱，而用户看不出
    命盘是错的。
    """
    command = build_chart_command(
        gender="female", birth_datetime="1988-03-02T08:00:00", place="朝阳", tool_call_id="c4"
    )

    assert "charts" not in command.update
    content = _message_of(command).content
    assert "朝阳市" in content and "朝阳区" in content, f"候选没列全：{content[:300]}"


def test_a_unique_place_produces_the_chart(bazi_chart_dict: dict[str, Any]):
    """能解析出唯一坐标时正常排盘 —— 上面几条都是它的例外分支。

    与 :func:`compute_bazi` 逐字段一致：这是"命盘只能来自引擎"在工具层的证据。
    """
    command = build_chart_command(
        gender="male", birth_datetime="1990-06-15T10:30:00", place="北京市", tool_call_id="c5"
    )

    assert command.update["charts"]["bazi"] == bazi_chart_dict
    assert command.update["birth_profile"] == bazi_chart_dict["profile"]


def test_a_date_without_a_time_downgrades_accuracy():
    """只给日期却声称时间精确 —— 以用户实际给的信息为准，不采信声明。

    否则会拿 00:00 当真实出生时刻，凭空造出一个时柱。
    """
    command = build_chart_command(gender="male", birth_datetime="1990-06-15", place="北京市", tool_call_id="c6")

    chart = command.update["charts"]["bazi"]
    assert chart["profile"]["time_accuracy"] == "unknown"
    assert chart["time_pillar"] is None, "时辰未知就不能有时柱"
    assert any("只有日期" in w for w in chart["warnings"]), "降级必须说出来，否则用户不知道时柱为何缺失"


@pytest.mark.parametrize(
    ("field", "value"),
    [("gender", "其他"), ("calendar", "阴阳历"), ("time_accuracy", "大概")],
    ids=["性别", "历法", "时间精度"],
)
def test_invalid_enum_values_become_questions(field: str, value: str):
    command = build_chart_command(
        gender=value if field == "gender" else "male",
        birth_datetime="1990-06-15T10:30:00",
        place="北京市",
        calendar=value if field == "calendar" else "solar",
        time_accuracy=value if field == "time_accuracy" else "exact",
        tool_call_id="c7",
    )

    assert "charts" not in command.update
    assert _message_of(command).artifact["status"] == "needs_clarification"


def test_the_tool_never_raises_on_bad_input():
    """把"任何输入都不抛异常"本身写成断言。

    工具的契约是"排不了就说明为什么"，异常兜底中间件只是保险丝而非主路径 ——
    一旦主路径开始抛异常，模型就会重新在"报错"与"编一个"之间做选择。
    """
    bad_inputs: list[dict[str, Any]] = [
        {"gender": "", "birth_datetime": ""},
        {"gender": "male", "birth_datetime": "不是时间"},
        {"gender": "male", "birth_datetime": "1990-06-15T10:30:00", "place": "北京", "calendar": "lunar"},
        {"gender": "male", "birth_datetime": "1990-06-15T10:30:00", "place": "北京市", "is_leap_month": True},
    ]

    for index, kwargs in enumerate(bad_inputs):
        command = build_chart_command(tool_call_id=f"bad{index}", **kwargs)
        assert command.update["messages"], f"输入 {kwargs} 既没排盘也没说明"


def test_the_registered_tool_exposes_the_same_contract():
    """``build_chart_command`` 与 ``@tool`` 包装后的入口必须是同一套语义。

    测试直接调前者（便于传 ``runtime=None``），但模型调的是后者 —— 两者若分叉，
    被测的就不是线上跑的那条路径了。
    """
    visible = set(bazi_chart_tool.tool_call_schema.model_json_schema()["properties"])

    assert visible >= {"gender", "birth_datetime", "place", "calendar", "is_leap_month", "time_accuracy"}
    assert "tool_call_id" not in visible, "注入参数不该出现在模型看到的契约里"
    assert "runtime" not in visible


def test_the_chart_carries_the_configured_school():
    """``config.yaml`` 的 ``bazi.school`` 要一路走到命盘里。

    它不只是个内部参数：解读层必须能读到流派前提，否则等于把一家之言说成定论 ——
    正是专业用户一眼看穿的地方（M1-FINDINGS §六.3）。
    """
    from metaphys.config import get_app_config

    command = build_chart_command(
        gender="male", birth_datetime="1990-06-15T10:30:00", place="北京市", tool_call_id="c8"
    )

    assert command.update["charts"]["bazi"]["school"] == get_app_config().bazi.school.value


def test_a_clock_time_with_seconds_does_not_leak_into_the_chart():
    """输入的 ISO 字符串带秒甚至微秒时，命盘里不该出现内部占位值。

    M1-FINDINGS §五 记过这个坑：``clock_time`` 曾泄漏内部占位值。
    """
    command = build_chart_command(
        gender="male", birth_datetime="1990-06-15T10:30:47", place="北京市", tool_call_id="c9"
    )

    chart = command.update["charts"]["bazi"]
    assert datetime.fromisoformat(chart["clock_time"]) == datetime(1990, 6, 15, 10, 30, 47)
