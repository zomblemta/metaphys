"""出生地解析 —— 排盘前的消歧策略。

真太阳时校正依赖经度，所以地名必须先落到唯一坐标上。难点在于**什么才算歧义**：

- 「北京」匹配到 17 条（各区县），但它们同属一个城市，经度跨度 1.14°（约 4.5 分钟
  真太阳时）。一个时辰有 120 分钟，这个量级翻不动时柱；而排盘引擎在出生时刻距
  时辰交界 30 分钟内时本来就会告警。**按市级中心取值即可，不该去问用户。**
- 「朝阳」匹配到北京朝阳区、长春朝阳区、辽宁朝阳市 —— **三个不同城市**，经度相差
  数度，选错就是另一个时辰的命盘。**必须让用户选。**

所以判据是**不同城市的个数**，不是候选条数。用条数会把"北京的 17 个区"误判成
歧义，把用户拖进一轮毫无意义的追问；用城市数则恰好把该问的问出来。

本模块只读查询、不写 state；解析规则由 ``bazi_chart`` 与 ``lookup_birthplace``
共用，两处不会对"是否有歧义"给出不同答案。
"""

from __future__ import annotations

from typing import NamedTuple

from langchain_core.tools import tool

from metaphys.engines.geo import GeoPoint, lookup_candidates

#: 候选过多时截断展示。到这个数量说明用户给的名字太宽泛，列全了只会淹没模型。
_MAX_CANDIDATES = 10

#: 市级中心条目的 ``area``：直辖市为空串，普通地级市为"市辖区"。
_CENTER_AREAS = ("", "市辖区")


def _prefer_center(candidates: list[GeoPoint]) -> GeoPoint:
    """同一城市内取市级中心条目 —— 量级论证见模块开头。"""
    for point in candidates:
        if point.area in _CENTER_AREAS:
            return point
    return candidates[0]


def representatives(candidates: list[GeoPoint]) -> list[GeoPoint]:
    """每个城市取一个代表，保持数据顺序。

    用户要选的是"哪个城市"，不是"朝阳市还是朝阳市双塔区"——后者是同一处地方，
    列出来只会让问题变长而不变清楚。
    """
    groups: dict[tuple[str, str], list[GeoPoint]] = {}
    for point in candidates:
        groups.setdefault((point.province, point.city), []).append(point)
    return [_prefer_center(group) for group in groups.values()]


def lookup_relaxed(place: str) -> tuple[list[GeoPoint], str]:
    """先精确查，查不到再逐字去掉前缀重查。

    地名库的索引键是市/区县名（「朝阳市」「朝阳区」），用户却常连上级区划一起写
    （「辽宁省朝阳市」）。直接查会一无所获，而这是很自然的写法。放宽时**从最长的
    后缀开始试**，优先匹配最具体的那个名字。

    Returns:
        ``(候选, 实际命中的名字)``。第二个值是空串表示精确命中 —— 调用方据此
        判断要不要向用户交代"我按另一个名字解析的"。
    """
    text = place.strip()
    if not text:
        return [], ""

    hits = lookup_candidates(text)
    if hits:
        return hits, text

    for start in range(1, len(text) - 1):
        hits = lookup_candidates(text[start:])
        if hits:
            return hits, text[start:]
    return [], ""


class PlaceResolution(NamedTuple):
    """地名解析结果。

    ``point`` 与 ``candidates`` 互斥：要么得到唯一坐标，要么带着候选去问用户。
    """

    point: GeoPoint | None
    #: 待用户选择的候选，每个城市一条。仅在需要消歧时非空。
    candidates: list[GeoPoint]
    #: 给模型的说明：未收录的理由、消歧提示，或放宽匹配的交代。
    note: str


def render_ambiguity(place: str, choices: list[GeoPoint]) -> str:
    """把跨城市候选渲染成给模型看的文本。``choices`` 应为每城一条。"""
    lines = [f"{place!r} 匹配到 {len(choices)} 个不同城市的地点，必须让用户选择其一（经度不同会算出不同的时柱）："]
    for index, point in enumerate(choices[:_MAX_CANDIDATES], start=1):
        lines.append(
            f"{index}. {point.display_name}"
            f"（{point.province}/{point.city}/{point.area}）"
            f"经度 {point.longitude}，纬度 {point.latitude}"
        )
    if len(choices) > _MAX_CANDIDATES:
        lines.append(f"（仅列出前 {_MAX_CANDIDATES} 个）")
    lines.append("请调用 ask_clarification 让用户确认是哪一处，不要自行选定。")
    return "\n".join(lines)


def resolve_place(place: str) -> PlaceResolution:
    """把地名解析成唯一坐标，或判定为需要用户消歧。"""
    text = place.strip()
    if not text:
        return PlaceResolution(None, [], "缺少出生地。")

    candidates, matched = lookup_relaxed(text)
    if not candidates:
        return PlaceResolution(
            None,
            [],
            f"地名库中未收录 {text!r}。请向用户确认更完整的行政区划名称"
            "（例如「朝阳市」「北京朝阳区」），或请用户直接提供经纬度。",
        )

    choices = representatives(candidates)
    if len(choices) == 1:
        # 放宽匹配要透明：不能悄悄把「辽宁省朝阳市」当成别的地方。
        note = f"已将 {text!r} 按 {matched!r} 解析为 {choices[0].display_name}。" if matched != text else ""
        return PlaceResolution(choices[0], [], note)

    return PlaceResolution(None, choices, render_ambiguity(text, choices))


@tool("lookup_birthplace", parse_docstring=True)
def lookup_birthplace_tool(place: str) -> str:
    """查询出生地对应的经纬度，用于排盘前的消歧与确认。

    当用户给出的地名可能对应多个城市（如「朝阳」既是北京朝阳区也是辽宁朝阳市）时，
    本工具会返回全部候选。**必须**先让用户确认，再调用 bazi_chart —— 经度不同会
    算出不同的真太阳时，进而得到不同的时柱。

    Args:
        place: 用户给出的出生地，如「北京」、「朝阳市」、「朝阳区」。

    Returns:
        唯一匹配时返回其经纬度；多个城市匹配时返回候选列表；无匹配时说明未收录。
    """
    text = place.strip()
    resolution = resolve_place(text)

    if resolution.point is not None:
        point = resolution.point
        prefix = f"{resolution.note}\n" if resolution.note else ""
        return (
            f"{prefix}唯一匹配：{point.display_name}，经度 {point.longitude}，纬度 {point.latitude}。可直接用于排盘。"
        )

    return resolution.note


__all__ = [
    "PlaceResolution",
    "lookup_birthplace_tool",
    "lookup_relaxed",
    "render_ambiguity",
    "representatives",
    "resolve_place",
]
