"""排星盘工具 —— 星盘数据进入 state 的**唯一**入口。

与 :mod:`metaphys.tools.builtins.bazi_chart` 结构对称：纯函数
:func:`build_astro_command` 承载全部逻辑（可直接测试，不必经过 langchain），
``@tool`` 只是一层薄包装。

**本工具不向模型抛异常**（与八字工具同一契约）：排不出盘时返回一条说明 +
建议提问，让模型去问用户。

与八字工具的三处差异，每一处都有具体理由：

1. **时间精度必须精确到分。** 八字只知时辰也能排出四柱（时辰本就是一个
   两小时的格子），星盘不行 —— 上升点约四分钟走一度，报"早上八点左右"
   会让上升点偏出 15°，宫位整体错开一宫。所以 ``hour_known`` 与 ``unknown``
   一律拒绝并追问。**还要查 state 里上一次的 birth_profile**：用户可能先用
   ``hour_known`` 排了八字（八字允许），接着说"那再给我看看星盘" —— 那时
   精度是从上一次继承来的，只看本次入参会漏掉。
2. **自己写 SVG 文件。** kerykeion 的 ``save_svg`` 把 ``filename`` 直接拼进
   路径，实测 ``filename="../../evil"`` 能写到输出目录之外；而它的默认文件名
   恰好含 ``subject.name`` —— 用户可控。所以文件名由出生信息**哈希**得到，
   并显式 mkdir（kerykeion 不建目录，目录不存在会直接 ``FileNotFoundError``）。
3. **多一类错误映射**：kerykeion 自己的异常经 ``AstroError`` 收窄后映射成
   "当前无法出盘"，而不是抛给模型。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command
from pydantic import ValidationError

from metaphys.config import find_config_path, get_app_config
from metaphys.engines.astro import (
    AstroError,
    AstroResult,
    compute_astro,
    render_svg,
)
from metaphys.engines.china_time import NonexistentLocalTimeError
from metaphys.schemas.chart import AstroChart, BirthProfile, TimeAccuracy
from metaphys.tools.builtins.birth import (
    needs_clarification,
    parse_birth_datetime,
    prior_birth_profile,
    resolve_birth_place,
)
from metaphys.tools.types import Runtime

#: 拿到完整出生信息之前，模型不得产出的内容。
_FORBIDDEN = "星座、宫位或相位"

#: 星盘对时间精度的要求，写在追问文案里让用户明白**为什么**要问这么细。
_TIME_REQUIREMENT = "上升点与宫位每四分钟就差一度，需要准确到分钟的出生时刻"


def _clarify(
    reason: str,
    *,
    suggested_question: str,
    tool_call_id: str,
    missing_fields: list[str] | None = None,
) -> Command:
    """本工具对共享追问构造器的绑定 —— 只补上"星盘不许编什么"。"""
    return needs_clarification(
        reason,
        suggested_question=suggested_question,
        tool_call_id=tool_call_id,
        missing_fields=missing_fields,
        must_not_produce=_FORBIDDEN,
    )


def _internal_failure(reason: str, *, tool_call_id: str) -> Command:
    """配置/依赖层面的失败 —— **不要**让模型去问用户。

    这类问题的成因在部署侧（宫位制写错、恒星黄道模式非法），用户既看不懂也
    改不了。若也走 ``ask_clarification``，用户会被问一个自己无法回答的问题，
    然后大概率随便答一个，把一次配置故障变成一份错误数据。
    """
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=(
                        f"星盘暂时无法排出：{reason}\n\n"
                        "这是服务端的配置问题，用户无法解决。请如实告知用户星盘当前不可用，"
                        f"**不要据此推算或描述任何{_FORBIDDEN}内容**，也不要反复重试。"
                    ),
                    tool_call_id=tool_call_id,
                    artifact={"status": "engine_error"},
                )
            ]
        }
    )


# --------------------------------------------------------------------------- #
# SVG 落盘
# --------------------------------------------------------------------------- #
def resolve_svg_dir() -> Path:
    """SVG 输出目录。

    相对路径按 **config.yaml 所在目录**解析，而不是进程的 cwd —— 否则从
    ``backend/`` 跑测试与从仓库根跑服务会把图写到两个地方，而两者都不报错。
    """
    raw = Path(get_app_config().astro.svg_dir).expanduser()
    if raw.is_absolute():
        return raw
    return find_config_path().parent / raw


def svg_filename(profile: BirthProfile, chart: AstroChart) -> str:
    """由出生信息与排盘前提算出文件名。

    **文件名里不含任何用户可控的原文。** kerykeion 默认用
    ``"{subject.name} - Natal Chart.svg"``，而 ``save_svg`` 不过滤路径分隔符
    —— 实测 ``filename="../../evil"`` 写在输出目录之外。这里换成哈希：
    路径穿越无从谈起，同一份出生信息重复排盘也命中同一个文件，不会越攒越多。

    ``name`` **参与哈希但不出现**。不参与的话，两个出生时刻与出生地完全相同、
    只有称呼不同的人会算出同一个文件名 —— 后者的图把前者的覆盖掉，于是先前
    那个人拿到的链接会显示出别人的名字（M4 网关要按路径伺服这些文件）。
    哈希过的输入不构成穿越风险，所以这里既安全、又不会串人。
    """
    key = "|".join(
        [
            profile.birth_datetime.isoformat(),
            f"{profile.latitude:.6f}",
            f"{profile.longitude:.6f}",
            profile.name,
            chart.house_system.value,
            chart.zodiac_type.value,
            chart.sidereal_mode or "",
            chart.engine_version,
        ]
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"astro-{digest}.svg"


def write_svg(result: AstroResult) -> Path:
    """把渲染结果写到 :func:`resolve_svg_dir`，返回落盘路径。

    **目录自己建**：kerykeion 的 ``save_svg`` 不创建目录，目录不存在时直接
    ``FileNotFoundError``（M0 记录的坑之一）。我们不走它的落盘路径，但同样
    不能假定目录已存在 —— 首次部署时它一定不存在。
    """
    directory = resolve_svg_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / svg_filename(result.chart.profile, result.chart)
    path.write_text(render_svg(result), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 消息渲染
# --------------------------------------------------------------------------- #
def _placement(chart: AstroChart, key: str) -> str:
    """把一个点的位置渲染成一行短语，如「双子 23.71°（第 11 宫）」。"""
    point = chart.point(key)
    if point is None:
        return "—"
    text = f"{point.sign} {point.position:.2f}°"
    if point.house is not None:
        text += f"（第 {point.house} 宫）"
    if point.retrograde:
        text += "（逆行）"
    return text


def _render_success(chart: AstroChart, svg_path: Path, note: str = "") -> str:
    """成功时的 ToolMessage 正文：要点速览 + 落盘路径 + 完整 JSON。

    刻意把黄道制与宫位制写在第一行：脱离这两个前提，"太阳在双子"没有意义。
    """
    parts = [
        f"星盘已排出（引擎 {chart.engine_version}；宫位制 {chart.house_system.name}；"
        f"黄道 {chart.zodiac_type.value}）。",
        "",
        f"- 太阳：{_placement(chart, 'sun')}",
        f"- 月亮：{_placement(chart, 'moon')}",
        f"- 上升：{_placement(chart, 'ascendant')}",
        f"- 天顶：{_placement(chart, 'medium_coeli')}",
        "",
        f"星盘图已保存至：{svg_path}",
    ]
    if note:
        parts.insert(1, f"说明：{note}")
    payload = json.dumps(chart.model_dump(mode="json"), ensure_ascii=False, indent=2)
    parts += ["", "```json", payload, "```"]
    if chart.warnings:
        parts += ["", "质量提示：", *(f"- {w}" for w in chart.warnings)]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 主流程（与 langchain 解耦，便于直接测试）
# --------------------------------------------------------------------------- #
def build_astro_command(
    *,
    birth_datetime: str,
    place: str = "",
    name: str = "",
    time_accuracy: str = "exact",
    tool_call_id: str = "",
    runtime: Runtime | None = None,
) -> Command:
    """校验输入 → 解析地名 → 排盘 → 渲染落盘 → 写 state。

    任何一步不成立都返回"未出盘 + 建议提问"，绝不返回半张星盘。
    """
    # --- 1. 出生时间 --------------------------------------------------------
    moment, has_time = parse_birth_datetime(birth_datetime)
    if moment is None:
        return _clarify(
            f"出生时间 {birth_datetime!r} 无法解析为日期时间。",
            suggested_question="请提供确切的出生日期与时间，例如「1990年6月15日上午10点30分」。",
            tool_call_id=tool_call_id,
            missing_fields=["birth_datetime"],
        )

    if not has_time:
        return _clarify(
            "只提供了出生日期，没有时刻。",
            suggested_question=f"星盘需要准确的出生时刻（{_TIME_REQUIREMENT}），请问是几点几分？",
            tool_call_id=tool_call_id,
            missing_fields=["birth_datetime"],
        )

    if time_accuracy != TimeAccuracy.EXACT.value:
        return _clarify(
            f"时间精度为 {time_accuracy}，不足以排出星盘（{_TIME_REQUIREMENT}）。",
            suggested_question="请问能确认到分钟的出生时间吗？哪怕只差半小时，上升点和宫位都会明显不同。",
            tool_call_id=tool_call_id,
            missing_fields=["birth_datetime"],
        )

    # --- 2. 不能把上一次的占位时刻当成精确时刻 ------------------------------
    prior = prior_birth_profile(runtime)
    if prior and prior.get("time_accuracy") not in (None, TimeAccuracy.EXACT.value):
        prior_moment, _ = parse_birth_datetime(str(prior.get("birth_datetime", "")))
        if prior_moment is not None and prior_moment == moment:
            # 上一次（多半是八字）用的是「时辰未知」的占位值，这次原样传了回来。
            # 不拦住的话，一个占位时刻会被当成精确时刻排出整张星盘。
            return _clarify(
                "这次提供的出生时刻与上一次排盘时使用的占位时刻完全相同，但它当时是作为「时间不确定」记录的。",
                suggested_question=f"上一次记录的时间并不精确。请问实际的出生时刻是几点几分？（{_TIME_REQUIREMENT}）",
                tool_call_id=tool_call_id,
                missing_fields=["birth_datetime"],
            )

    # --- 3. 出生地 ----------------------------------------------------------
    coords, display_name, candidates, note = resolve_birth_place(place, runtime)
    if coords is None:
        question = (
            f"您说的「{place}」对应多个地点，请问是哪一处？（经度不同会算出不同的上升点与宫位）"
            if candidates
            else "请提供出生地（写到区县，例如「朝阳市」或「北京朝阳区」）。"
        )
        return _clarify(note, suggested_question=question, tool_call_id=tool_call_id, missing_fields=["place"])

    # --- 4. 组装出生信息 ----------------------------------------------------
    try:
        profile = BirthProfile(
            name=name,
            birth_datetime=moment,
            place=display_name,
            latitude=coords[0],
            longitude=coords[1],
            # 前面已逐条拒绝过非精确输入，这里钉死 —— 不要采信调用方传进来的字符串。
            time_accuracy=TimeAccuracy.EXACT,
        )
    except ValidationError as err:
        first = err.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "参数"
        return _clarify(
            f"出生信息不合法（{field}：{first['msg']}）。",
            suggested_question="请核对出生日期、时间与出生地。",
            tool_call_id=tool_call_id,
            missing_fields=[field],
        )

    # --- 5. 排盘 ------------------------------------------------------------
    astro_config = get_app_config().astro
    try:
        result = compute_astro(
            profile,
            house_system=astro_config.house_system,
            zodiac_type=astro_config.zodiac_type,
            sidereal_mode=astro_config.sidereal_mode,
        )
    except NonexistentLocalTimeError:
        return _clarify(
            f"{moment:%Y-%m-%d %H:%M} 在 {profile.place} 当地不存在（夏令时切换造成的时刻缺口）。",
            suggested_question="这个出生时间在夏令时调整当天并不存在，请核对是否为提前或推后一小时。",
            tool_call_id=tool_call_id,
            missing_fields=["birth_datetime"],
        )
    except AstroError as err:
        return _internal_failure(str(err), tool_call_id=tool_call_id)

    # --- 6. 渲染落盘 --------------------------------------------------------
    # 落盘失败（磁盘满、权限不足）不该让整次排盘白做：盘已经算出来了，
    # 交给模型的 JSON 是完整的，只有配图缺了。故只降级提示，不中断。
    try:
        svg_path: Path | None = write_svg(result)
    except OSError as err:
        svg_path = None
        result.chart.warnings.append(f"星盘图生成失败（{err}），文字解读不受影响。")

    # --- 7. 写 state --------------------------------------------------------
    return Command(
        update={
            "charts": {"astro": result.chart.model_dump(mode="json")},
            "birth_profile": profile.model_dump(mode="json"),
            "messages": [
                ToolMessage(
                    content=_render_success(
                        result.chart,
                        svg_path or Path("（未生成）"),
                        note,
                    ),
                    tool_call_id=tool_call_id,
                    artifact={"status": "ok", "svg_path": str(svg_path) if svg_path else None},
                )
            ],
        }
    )


# --------------------------------------------------------------------------- #
# 工具入口
# --------------------------------------------------------------------------- #
@tool("astro_chart", parse_docstring=True)
def astro_chart_tool(
    runtime: Runtime,
    birth_datetime: str,
    place: str = "",
    name: str = "",
    time_accuracy: Literal["exact", "hour_known", "unknown"] = "exact",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """根据出生信息排出西洋星盘（本命盘）。

    用户想看星座、上升、宫位或行星相位时**必须**调用本工具取得星盘，不得自行
    推算任何星座、宫位或相位。排盘结果会写入会话状态，供后续回合直接引用。

    本工具对出生时间的要求比 bazi_chart **更严**：必须精确到分钟。只知道大致
    时辰时请如实传 hour_known 或 unknown，不要猜一个具体时间 —— 上升点每四分钟
    就走一度，猜出来的时间会得到一张完全错误的盘。

    Args:
        birth_datetime: 出生日期时间，ISO 8601 格式，**必须含时刻**，
            如 1990-06-15T10:30:00。只给日期会被拒绝。
        place: 出生地，尽量给到区县，如「辽宁省朝阳市」。留空则沿用上一次排盘
            的出生地。
        name: 命主称呼，可留空；会出现在星盘图的标题上。
        time_accuracy: 时间精度。exact（精确到分，默认）、hour_known（只知
            时辰）或 unknown（时辰未知）。后两者会被拒绝并请你向用户确认。

    Returns:
        更新会话状态的 Command；成功时含完整星盘与生成的 SVG 路径。
    """
    return build_astro_command(
        birth_datetime=birth_datetime,
        place=place,
        name=name,
        time_accuracy=time_accuracy,
        tool_call_id=tool_call_id,
        runtime=runtime,
    )


__all__ = [
    "astro_chart_tool",
    "build_astro_command",
    "resolve_svg_dir",
    "svg_filename",
    "write_svg",
]
