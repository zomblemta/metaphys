"""``astro_chart`` 工具。

与 :mod:`tests.test_config_and_tools` 里八字工具那几节对称，但星盘多出三处
**只对星盘成立**的约束，每一条都在这里钉住：

1. 时间非精确一律拒绝（含 ``hour_known``）—— 上升点每四分钟走一度。
2. 落盘路径不可由用户决定，且目录要自己建（kerykeion 不建）。
3. 引擎侧的 ``AstroError`` 不能让模型去问用户 —— 那是部署方的配置问题。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from metaphys.agents.thread_state import merge_charts
from metaphys.config import reload_app_config
from metaphys.engines.astro import AstroError, compute_astro
from metaphys.schemas.chart import BirthProfile, HouseSystem, TimeAccuracy
from metaphys.tools.builtins.astro_chart import (
    build_astro_command,
    svg_filename,
)
from metaphys.tools.builtins.bazi_chart import build_chart_command
from metaphys.tools.builtins.geo import resolve_place

_BIRTH = datetime(1990, 6, 15, 10, 30)
_BIRTH_ISO = _BIRTH.isoformat()
_PLACE = "北京市"

#: 临时 config.yaml 里的宫位制。**刻意不是引擎默认的 Placidus** —— 这样
#: "工具把配置透传下去了"才是一条真断言：忽略配置的实现会排出 Placidus，
#: 与期望值对不上。
_CONFIG_HOUSE_SYSTEM = "W"
_CONFIG_HOUSE_SYSTEM_ENUM = HouseSystem.WHOLE_SIGN


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture
def chart_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """换一份 config.yaml，返回其 ``astro.svg_dir`` 指向的目录（**尚不存在**）。

    走 ``METAPHYS_CONFIG_PATH`` 而不是给 ``get_app_config`` 打桩：``svg_dir``
    的相对路径要按 config.yaml 所在目录解析，这个分支只有真的换一份配置文件
    才测得到 —— 打桩会把被测的那段逻辑一起绕过去。
    """
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "config_version": 1,
                "models": [{"name": "m", "use": "langchain_deepseek:ChatDeepSeek", "api_key": "$DEEPSEEK_API_KEY"}],
                "default_model": "m",
                "tools": [],
                "astro": {
                    "house_system": _CONFIG_HOUSE_SYSTEM,
                    "zodiac_type": "tropical",
                    "svg_dir": "var/charts",
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("METAPHYS_CONFIG_PATH", str(config_path))
    reload_app_config()
    return tmp_path / "var" / "charts"


def _runtime_with(profile: dict[str, Any] | None) -> ToolRuntime[dict[str, Any], Any]:
    """只有一个 ``birth_profile`` 槽有内容的 runtime。

    用真的 ``ToolRuntime`` 而不是鸭子类型替身 —— 字段名写错这类问题要在测试里
    就暴露，而不是等到运行期才发现 ``state`` 取不到。
    """
    return ToolRuntime(
        state={"birth_profile": profile},
        context={},
        config={"configurable": {}},
        stream_writer=lambda _: None,
        tool_call_id=None,
        store=None,
    )


def _message_of(command: Any) -> ToolMessage:
    messages = command.update["messages"]
    assert len(messages) == 1 and isinstance(messages[0], ToolMessage)
    return messages[0]


def _expected_chart(name: str = "") -> dict[str, Any]:
    """工具**应当**产出的那张盘 —— 由引擎直接算一遍。

    坐标经地理库解析得到，不写死：写死过一个想当然的经纬度，与库里真值差了
    0.006°，于是"逐字段一致"恒假，而被测代码其实没有问题（八字那边踩过）。
    """
    point = resolve_place(_PLACE).point
    assert point is not None, f"地理库里查不到 {_PLACE}，夹具的前提不成立"
    profile = BirthProfile(
        name=name,
        birth_datetime=_BIRTH,
        place=point.display_name,
        latitude=point.latitude,
        longitude=point.longitude,
        time_accuracy=TimeAccuracy.EXACT,
    )
    return compute_astro(profile, house_system=_CONFIG_HOUSE_SYSTEM_ENUM).chart.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# 时间：非精确一律拒绝
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("accuracy", ["hour_known", "unknown"])
def test_a_non_exact_time_is_refused(accuracy: str, chart_dir: Path):
    """``hour_known`` 与 ``unknown`` 都被拒绝 —— 与八字工具**相反**。

    八字允许 ``hour_known``（时辰本就是两小时的格子，四柱照排）；星盘不允许，
    因为上升点约四分钟走一度，报"早上八点左右"会让上升点偏出 15°、宫位整体
    错开一宫。用户看不出这张盘是错的，所以必须拒绝。
    """
    command = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, time_accuracy=accuracy, tool_call_id="a1")

    assert "charts" not in command.update, f"{accuracy} 竟然排出了星盘"
    message = _message_of(command)
    assert message.artifact["status"] == "needs_clarification"
    assert "birth_datetime" in message.artifact["missing_fields"]
    assert "星座" in message.content, "必须说清拿到时间之前不许给什么"


def test_a_date_without_a_time_is_refused(chart_dir: Path):
    """只给日期 → 拒绝。

    不能拿 00:00 当出生时刻：那会凭空造出一个上升点，而它看起来和真的一样。
    """
    command = build_astro_command(birth_datetime="1990-06-15", place=_PLACE, tool_call_id="a2")

    assert "charts" not in command.update
    assert _message_of(command).artifact["status"] == "needs_clarification"


def test_the_tool_still_works_when_the_time_is_exact(chart_dir: Path):
    """反向样本 —— 否则上面两条"拒绝"可以靠永远拒绝来通过。"""
    command = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="a3")

    assert command.update["charts"]["astro"] == _expected_chart()


def test_a_placeholder_time_inherited_from_a_bazi_run_is_refused(chart_dir: Path):
    """上一次的**占位时刻**不许被当成精确时刻。

    真实的用户路径：先只知时辰排了八字（八字允许），隔一句说"那再给我看看星盘"。
    模型多半会把 state 里那份 ``birth_profile`` 的时间原样传回来 —— 只看本次
    入参（``time_accuracy`` 默认还是 ``exact``）就会放行，于是一个当初被标为
    "不确定"的时刻排出了一整张看起来正常的星盘。
    """
    placeholder = datetime(1990, 6, 15, 12, 0)
    prior = {
        "birth_datetime": placeholder.isoformat(),
        "place": "北京市",
        "latitude": 39.9,
        "longitude": 116.4,
        "time_accuracy": TimeAccuracy.HOUR_KNOWN.value,
    }

    command = build_astro_command(
        birth_datetime=placeholder.isoformat(),
        place=_PLACE,
        tool_call_id="a4",
        runtime=_runtime_with(prior),
    )

    assert "charts" not in command.update, "把「时辰未知」的占位值当成了精确时刻"
    assert _message_of(command).artifact["status"] == "needs_clarification"


def test_an_exact_time_that_merely_shares_a_prior_minute_is_not_blocked(chart_dir: Path):
    """上一版是 ``exact`` 时，同一个时刻照常放行 —— 否则上面那条会误伤复用。

    多轮对话里用户重问同一个人的星盘是常态，若只要"和上次相同"就拒绝，
    正常路径就被堵死了。判据必须是"上次的**精度**不足以出星盘"，不是"和上次一样"。
    """
    prior = {
        "birth_datetime": _BIRTH_ISO,
        "place": "北京市",
        "latitude": 39.9,
        "longitude": 116.4,
        "time_accuracy": TimeAccuracy.EXACT.value,
    }

    command = build_astro_command(
        birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="a5", runtime=_runtime_with(prior)
    )

    assert command.update["charts"]["astro"] == _expected_chart()


# --------------------------------------------------------------------------- #
# 落盘：文件名与目录
# --------------------------------------------------------------------------- #
def test_the_output_directory_is_created_by_the_tool(chart_dir: Path):
    """目录不存在时工具自己建 —— kerykeion 的 ``save_svg`` 不建。

    首次部署时该目录一定不存在，所以这条不是假想：照 kerykeion 的做法会直接
    ``FileNotFoundError``，而工具又承诺不抛异常。
    """
    assert not chart_dir.exists(), "夹具前提：目录本来就该不存在"

    command = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="a6")

    assert chart_dir.is_dir()
    svg_path = Path(command.update["messages"][0].artifact["svg_path"])
    assert svg_path.parent == chart_dir
    assert svg_path.is_file()
    assert svg_path.stat().st_size > 0


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../../tmp/evil",
        "..",
        "a/b/c",
        "....//....//evil",
        "evil\x00",
    ],
    ids=["多级上跳", "上跳", "路径分隔", "过滤绕过", "空字节"],
)
def test_the_name_cannot_steer_the_output_path(hostile: str, chart_dir: Path):
    """用户可控的 ``name`` 不得影响落盘位置。

    kerykeion 的默认文件名是 ``"{subject.name} - Natal Chart.svg"``，而
    ``save_svg`` 不过滤路径分隔符 —— 实测 ``filename="../../evil"`` 写出目录之外。
    本工具自己生成文件名，这里钉住那条防线。
    """
    command = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, name=hostile, tool_call_id="a7")

    svg_path = Path(command.update["messages"][0].artifact["svg_path"])
    assert svg_path.parent == chart_dir, f"{hostile!r} 把产物带出了输出目录"
    assert ".." not in svg_path.name and "/" not in svg_path.name
    assert svg_path.is_file()


def test_two_people_with_the_same_birth_data_do_not_share_a_file():
    """出生信息相同、称呼不同的两个人，文件名必须不同。

    文件名若只由出生信息决定，后者的图会覆盖前者的 —— 于是先前那个人拿到的
    链接显示出别人的名字（M4 网关按路径伺服这些文件）。称呼参与哈希即可避免，
    而哈希过的输入不构成路径穿越风险。
    """
    point = resolve_place(_PLACE).point
    assert point is not None
    chart = compute_astro(
        BirthProfile(
            birth_datetime=_BIRTH,
            place=point.display_name,
            latitude=point.latitude,
            longitude=point.longitude,
        ),
        house_system=_CONFIG_HOUSE_SYSTEM_ENUM,
    ).chart

    def name_of(label: str) -> str:
        return svg_filename(chart.profile.model_copy(update={"name": label}), chart)

    assert name_of("张三") != name_of("李四")


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
def test_the_chart_lands_in_state_field_by_field(chart_dir: Path):
    """``charts["astro"]`` 与引擎产出逐字段一致。

    比的是整份 ``model_dump``，所以任何一处被链路改写（少一个点、相位被截断、
    前提被换成默认值）都会红。
    """
    command = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="a8")

    assert command.update["charts"]["astro"] == _expected_chart()
    assert command.update["birth_profile"]["birth_datetime"] == _BIRTH.isoformat()


def test_the_config_decides_the_house_system(chart_dir: Path):
    """宫位制来自配置，不是引擎默认值。

    宫位制是全部宫位结论的前提：配置写 W（整宫）而实际按 Placidus 排，用户会
    得到一张宫位全错的盘，而图、JSON、解读三处**自洽**，看不出任何异常。
    """
    command = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="a9")

    chart = command.update["charts"]["astro"]
    assert chart["house_system"] == _CONFIG_HOUSE_SYSTEM_ENUM.value
    assert chart["house_system"] != HouseSystem.PLACIDUS.value, "夹具没起到对照作用"


def test_the_model_gets_the_chart_as_json_and_the_path(chart_dir: Path):
    """模型读到的是完整 JSON + 落盘路径。

    SVG 本体约 18 万字符，只回路径 —— 整份塞进上下文会把窗口占满，而且模型
    也读不出图形里画了什么。
    """
    command = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="a10")
    content = _message_of(command).content

    assert "```json" in content
    assert Path(command.update["messages"][0].artifact["svg_path"]).as_posix() in content
    assert content.index("宫位制") < content.index("```json"), "前提必须写在盘之前"


def test_the_same_input_twice_yields_the_same_chart(chart_dir: Path):
    """确定性 —— 同一输入两次必须逐字段相等。"""
    first = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="a11")
    second = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="a11")

    assert first.update["charts"] == second.update["charts"]


def test_a_failed_write_does_not_lose_the_chart(chart_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """落盘失败只降级提示，不丢盘。

    盘已经算出来了，JSON 也是完整的，缺的只是配图。为此把整次排盘作废，是拿
    一个可降级的问题去换一个不可降级的结果。
    """
    import metaphys.tools.builtins.astro_chart as astro_chart

    def boom(_: Any) -> Path:
        raise OSError("No space left on device")

    monkeypatch.setattr(astro_chart, "write_svg", boom)

    command = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="a12")

    assert command.update["charts"]["astro"]["points"], "排好的盘被一起丢掉了"
    assert any("星盘图生成失败" in w for w in command.update["charts"]["astro"]["warnings"])
    assert command.update["messages"][0].artifact["svg_path"] is None


# --------------------------------------------------------------------------- #
# 引擎异常 → 不让用户去猜
# --------------------------------------------------------------------------- #
def test_an_engine_error_does_not_send_the_user_on_a_wild_goose(chart_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """引擎侧失败 → 说明情况，且**不**让模型去追问用户。

    这类失败的成因在部署侧（宫位制写错、恒星黄道模式非法），用户看不懂也改不了。
    若也走 ``ask_clarification``，用户会被问一个自己无法回答的问题，然后大概率
    随便答一个 —— 一次配置故障就此变成一份错误数据。
    """
    import metaphys.tools.builtins.astro_chart as astro_chart

    def boom(*_: Any, **__: Any) -> Any:
        raise AstroError("星盘无法推算：Invalid houses system identifier")

    monkeypatch.setattr(astro_chart, "compute_astro", boom)

    command = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="a13")

    assert "charts" not in command.update
    message = _message_of(command)
    assert message.artifact["status"] == "engine_error"
    assert "Invalid houses system identifier" in message.content, "病因要原样带给运维"
    assert "ask_clarification" not in message.content, "配置问题不该由用户来回答"


def test_the_tool_never_raises_at_the_model(chart_dir: Path):
    """穷举一批坏输入，全部必须是 Command 而不是异常 —— 这是本工具的公开契约。"""
    bad_calls: list[dict[str, Any]] = [
        {"birth_datetime": ""},
        {"birth_datetime": "不是时间"},
        {"birth_datetime": _BIRTH_ISO, "place": "不存在的虚构地名"},
        {"birth_datetime": _BIRTH_ISO, "place": ""},
        {"birth_datetime": "1986-05-04T02:30:00", "place": _PLACE},  # 夏令时缺口
    ]

    for index, kwargs in enumerate(bad_calls):
        command = build_astro_command(tool_call_id=f"a1{index}", **kwargs)
        assert "charts" not in command.update, f"{kwargs} 竟然排出了盘"
        assert _message_of(command).artifact["status"] == "needs_clarification"


# --------------------------------------------------------------------------- #
# 两种盘并存
# --------------------------------------------------------------------------- #
def test_bazi_and_astro_coexist_in_one_thread(chart_dir: Path):
    """同一个会话里先八字后星盘，两张盘都要在。

    ``merge_charts`` 按 kind 覆盖合并 —— 若它改成整槽替换，用户问完星盘就再也
    看不到自己的八字盘了，而这一步不报错。
    """
    bazi = build_chart_command(gender="male", birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="b1")
    astro = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="a14")

    merged = merge_charts(bazi.update["charts"], astro.update["charts"])

    assert set(merged) == {"bazi", "astro"}, "先排的那张盘被后一张抹掉了"
    assert merged["bazi"] == bazi.update["charts"]["bazi"]
    assert merged["astro"] == _expected_chart()


def test_changing_the_subject_invalidates_the_previous_bazi_chart(chart_dir: Path):
    """称呼改变视为命主资料变更，不能保留另一命主的八字。"""
    bazi = build_chart_command(gender="male", birth_datetime=_BIRTH_ISO, place=_PLACE, tool_call_id="b2")
    first = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, name="旧", tool_call_id="a15")
    second = build_astro_command(birth_datetime=_BIRTH_ISO, place=_PLACE, name="新", tool_call_id="a16")

    merged = merge_charts(merge_charts(bazi.update["charts"], first.update["charts"]), second.update["charts"])

    assert set(merged) == {"astro"}
    assert merged["astro"]["profile"]["name"] == "新"


def test_svg_replace_failure_preserves_existing_file(tmp_path, monkeypatch, astro_chart_dict):
    from types import SimpleNamespace

    from metaphys.schemas.chart import AstroChart
    from metaphys.tools.builtins.astro_chart import svg_filename, write_svg

    chart = AstroChart.model_validate(astro_chart_dict)
    path = tmp_path / svg_filename(chart.profile, chart)
    path.write_text("original")
    monkeypatch.setattr("metaphys.tools.builtins.astro_chart.resolve_svg_dir", lambda: tmp_path)
    monkeypatch.setattr("metaphys.tools.builtins.astro_chart.render_svg", lambda result: "<svg/>")

    def fail_replace(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr("metaphys.tools.builtins.astro_chart.os.replace", fail_replace)
    with pytest.raises(OSError):
        write_svg(SimpleNamespace(chart=chart))
    assert path.read_text() == "original"
    assert list(tmp_path.iterdir()) == [path]
