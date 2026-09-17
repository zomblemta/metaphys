"""排盘工具 —— 命盘数据进入 state 的**唯一**入口。

架构不变量在这里落地：LLM 只负责把用户口述的出生信息整理成参数，全部推算
由 :func:`~metaphys.engines.bazi.compute_bazi` 完成。结果以 ToolMessage 回到
模型、同时以 ``charts`` 写入 state 供前端读取。模型既没有能力、也没有必要
自行推算任何干支。

**本工具不向模型抛异常。** 排不了盘时返回一条"未排盘"的说明并附上建议提问，
让模型用 ``ask_clarification`` 去问。理由是这类失败几乎全是用户输入不全
（时辰没说、地名重名、农历闰月标错），把它们变成异常只会让模型在"报错"与
"编一个"之间二选一，而后者对用户的伤害大得多。
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command
from pydantic import ValidationError

from metaphys.config import get_app_config
from metaphys.engines.bazi import (
    InvalidLunarDateError,
    MissingGenderError,
    NonexistentLocalTimeError,
    compute_bazi,
)
from metaphys.schemas.chart import BaziChart, BirthProfile, Calendar, Gender, TimeAccuracy
from metaphys.tools.builtins.birth import (
    needs_clarification,
    parse_birth_datetime,
    resolve_birth_place,
)
from metaphys.tools.types import Runtime

# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
#: 拿到完整出生信息之前，模型不得产出的内容。
_FORBIDDEN = "四柱、五行或神煞"


def _needs_clarification(
    reason: str,
    *,
    suggested_question: str,
    tool_call_id: str,
    missing_fields: list[str] | None = None,
) -> Command:
    """本工具对共享追问构造器的绑定 —— 只补上"八字不许编什么"。"""
    return needs_clarification(
        reason,
        suggested_question=suggested_question,
        tool_call_id=tool_call_id,
        missing_fields=missing_fields,
        must_not_produce=_FORBIDDEN,
    )


def _render_success(chart: BaziChart, note: str = "") -> str:
    """成功时的 ToolMessage 正文：一行速览 + 完整 JSON。"""
    header = (
        f"命盘已排出。四柱：{chart.four_pillars}"
        f"（引擎 {chart.engine_version}，流派 {chart.school}，已按出生地做真太阳时校正）"
    )
    parts = [header]
    if note:
        # 地名被放宽解析时必须说出来，否则用户不知道排的是哪个地方。
        parts.append(f"说明：{note}")
    payload = json.dumps(chart.model_dump(mode="json"), ensure_ascii=False, indent=2)
    parts += ["", "```json", payload, "```"]
    if chart.warnings:
        parts += ["", "质量提示：", *(f"- {w}" for w in chart.warnings)]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 主流程（与 langchain 解耦，便于直接测试）
# --------------------------------------------------------------------------- #
def build_chart_command(
    *,
    gender: str,
    birth_datetime: str,
    place: str = "",
    name: str = "",
    calendar: str = "solar",
    is_leap_month: bool = False,
    time_accuracy: str = "exact",
    tool_call_id: str = "",
    runtime: Runtime | None = None,
) -> Command:
    """校验输入 → 解析地名 → 排盘 → 写 state。

    任何一步不成立都返回"未排盘 + 建议提问"，绝不返回半个命盘。
    """
    # --- 1. 枚举值 ----------------------------------------------------------
    try:
        gender_value = Gender(gender)
        calendar_value = Calendar(calendar)
        accuracy = TimeAccuracy(time_accuracy)
    except ValueError as err:
        return _needs_clarification(
            f"参数取值不合法（{err}）。",
            suggested_question="请确认性别、历法与时间精度的取值是否正确。",
            tool_call_id=tool_call_id,
        )

    # --- 2. 出生时间 --------------------------------------------------------
    moment, has_time = parse_birth_datetime(birth_datetime)
    if moment is None:
        return _needs_clarification(
            f"出生时间 {birth_datetime!r} 无法解析为日期时间。",
            suggested_question="请提供确切的出生日期与时间，例如「1990年6月15日上午10点30分」。",
            tool_call_id=tool_call_id,
            missing_fields=["birth_datetime"],
        )

    downgraded = not has_time and accuracy is not TimeAccuracy.UNKNOWN
    if downgraded:
        # 只给了日期却声称时间精确 —— 以用户实际给的信息为准，不采信声明。
        accuracy = TimeAccuracy.UNKNOWN

    # --- 3. 出生地 ----------------------------------------------------------
    coords, display_name, candidates, note = resolve_birth_place(place, runtime)
    if coords is None:
        if candidates:
            question = f"您说的「{place}」对应多个地点，请问是哪一处？（经度不同会算出不同的时柱）"
        else:
            question = "请提供出生地（写到区县，例如「朝阳市」或「北京朝阳区」）。"
        return _needs_clarification(
            note,
            suggested_question=question,
            tool_call_id=tool_call_id,
            missing_fields=["place"],
        )

    # --- 4. 组装出生信息 ----------------------------------------------------
    try:
        profile = BirthProfile(
            name=name,
            gender=gender_value,
            birth_datetime=moment,
            place=display_name,
            latitude=coords[0],
            longitude=coords[1],
            time_accuracy=accuracy,
            calendar=calendar_value,
            is_leap_month=is_leap_month,
        )
    except ValidationError as err:
        first = err.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "参数"
        return _needs_clarification(
            f"出生信息不合法（{field}：{first['msg']}）。",
            suggested_question="请核对出生日期、时间与出生地。",
            tool_call_id=tool_call_id,
            missing_fields=[field],
        )

    # --- 5. 排盘 ------------------------------------------------------------
    try:
        chart = compute_bazi(profile, school=get_app_config().bazi.school)
    except NonexistentLocalTimeError:
        # 夏令时切换当天有一段钟表上不存在的时刻，只能请用户改时间。
        return _needs_clarification(
            f"{moment:%Y-%m-%d %H:%M} 在 {profile.place} 当地不存在（夏令时切换造成的时刻缺口）。",
            suggested_question="这个出生时间在夏令时调整当天并不存在，请核对是否为提前或推后一小时。",
            tool_call_id=tool_call_id,
            missing_fields=["birth_datetime"],
        )
    except InvalidLunarDateError as err:
        return _needs_clarification(
            f"农历日期不成立：{err}",
            suggested_question="请确认农历月份与是否闰月（闰月需要单独说明）。",
            tool_call_id=tool_call_id,
            missing_fields=["birth_datetime", "is_leap_month"],
        )
    except MissingGenderError:
        # 目前从工具进来不可能触发（gender 是必填参数），但引擎侧的守卫是
        # 公开契约的一部分，这里必须接住 —— 否则将来多一条调用路径时，
        # 一个"少问一句性别"就会变成面向用户的报错，而本工具承诺不抛异常。
        return _needs_clarification(
            "缺少性别，无法确定大运顺逆。",
            suggested_question="请问命主的性别是？（大运的顺排与逆排由性别决定）",
            tool_call_id=tool_call_id,
            missing_fields=["gender"],
        )

    if downgraded:
        # 与引擎自己的"时辰未知"告警并存但各有分工：那条说"时柱缺失"，
        # 这条说明**为什么**缺失（用户只报了日期），便于用户补齐输入。
        chart.warnings.append("提供的出生时间只有日期、没有时刻，已按「时辰未知」处理；补充具体时间后才能给出时柱。")

    # --- 6. 写 state --------------------------------------------------------
    return Command(
        update={
            "charts": {"bazi": chart.model_dump(mode="json")},
            "birth_profile": profile.model_dump(mode="json"),
            "messages": [ToolMessage(content=_render_success(chart, note), tool_call_id=tool_call_id)],
        }
    )


# --------------------------------------------------------------------------- #
# 工具入口
# --------------------------------------------------------------------------- #
@tool("bazi_chart", parse_docstring=True)
def bazi_chart_tool(
    runtime: Runtime,
    gender: Literal["male", "female"],
    birth_datetime: str,
    place: str = "",
    name: str = "",
    calendar: Literal["solar", "lunar"] = "solar",
    is_leap_month: bool = False,
    time_accuracy: Literal["exact", "hour_known", "unknown"] = "exact",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """根据出生信息排出八字命盘。

    用户提供了出生日期与时间后**必须**调用本工具取得命盘，不得自行推算四柱、
    五行、十神或神煞。排盘结果会写入会话状态，供后续回合直接引用。

    Args:
        gender: 性别，male 或 female。
        birth_datetime: 出生地当地钟表时间，不含 Z 或 UTC 偏移，ISO 8601 格式，如 1990-06-15T10:30:00。
            只知道日期时就只传日期（如 1990-06-15），此时时柱会被略去。
        place: 出生地，尽量给到区县，如「辽宁省朝阳市」。留空则沿用上一次排盘
            的出生地。
        name: 命主称呼，可留空。
        calendar: 历法，solar（公历，默认）或 lunar（农历）。
        is_leap_month: 农历闰月时必须置为 true，否则该月会被解析成错误的日期。
        time_accuracy: 时间精度，exact（精确到分）、hour_known（只知时辰）
            或 unknown（时辰未知）。

    Returns:
        更新会话状态的 Command；排盘成功时含完整命盘。
    """
    return build_chart_command(
        gender=gender,
        birth_datetime=birth_datetime,
        place=place,
        name=name,
        calendar=calendar,
        is_leap_month=is_leap_month,
        time_accuracy=time_accuracy,
        tool_call_id=tool_call_id,
        runtime=runtime,
    )


__all__ = ["bazi_chart_tool", "build_chart_command"]
