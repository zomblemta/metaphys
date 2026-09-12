"""日主五行旺衰强弱评分。

这是八字里**最依赖流派**的一环 —— 同一张盘，子平、盲派、新派可能给出不同的
强弱判断，进而推出完全相反的喜忌。因此本模块不假装有唯一正解，而是：

1. 把评分拆成可复核的分量（各五行得分、同党异党、通根、透干）全部输出；
2. 权重按流派取不同预设，并在结果中标注所用流派。

这样解读层可以声明"依子平法判为身弱"，用户与专业读者都能复核前提。

评分方案（量纲为"分"，只用于内部比较）：

* **天干**各按柱位加权，日干本身即日主，不计入。
* **地支**按柱位加权后，再由藏干按本气/中气/余气分配。
* **月令权重最高** —— 月令司权，是旺衰的第一决定因素，各流派对此无争议，
  分歧只在"最高到何种程度"。
* **通根**单独标记：日主在地支藏干中出现即为有根，这是身强身弱的关键分野，
  不能只靠总分掩盖。

纯函数，零依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from metaphys.engines.bazi.ganzhi import (
    BRANCH_HIDDEN,
    STEM_ELEMENT,
    element_controlled_by,
    element_controls,
    element_generated_by,
    element_generates,
)
from metaphys.schemas.chart import ElementScore, Pillar, School, StrengthAnalysis

ELEMENTS = ("木", "火", "土", "金", "水")

# 藏干分配比例，按藏干个数取前 n 项：本气 / 中气 / 余气
_HIDDEN_SHARES: dict[int, tuple[float, ...]] = {
    1: (1.0,),
    2: (0.7, 0.3),
    3: (0.6, 0.3, 0.1),
}


@dataclass(frozen=True)
class StrengthWeights:
    """流派权重预设。"""

    branch: dict[str, float] = field(default_factory=dict)
    stem: dict[str, float] = field(default_factory=dict)
    rationale: str = ""


# 子平：以月令为纲，月令权重压倒性
_ZIPING = StrengthWeights(
    branch={"年": 1.0, "月": 5.0, "日": 1.5, "时": 1.0},
    stem={"年": 0.8, "月": 1.0, "时": 1.0},
    rationale="子平法以月令为纲，月令司权最重，故月支权重远高于他柱",
)

# 盲派：月令与日支并重（日支为日主坐下，盲派重"坐支"与做功）
_MANGPAI = StrengthWeights(
    branch={"年": 1.0, "月": 3.5, "日": 3.0, "时": 1.0},
    stem={"年": 1.0, "月": 1.0, "时": 1.0},
    rationale="盲派重日主坐支与做功，月令与日支并重；但盲派本身不重量化打分，此分仅供参考",
)

# 新派：量化均衡，各柱差距较小
_XINPAI = StrengthWeights(
    branch={"年": 1.0, "月": 3.0, "日": 2.0, "时": 1.5},
    stem={"年": 1.0, "月": 1.5, "时": 1.5},
    rationale="新派量化打分，各柱权重较为均衡，时干贴近日主故略高于年干",
)

_WEIGHTS: dict[School, StrengthWeights] = {
    School.ZIPING: _ZIPING,
    School.MANGPAI: _MANGPAI,
    School.XINPAI: _XINPAI,
}


def _verdict(ratio: float) -> str:
    """同党占比 → 强弱判语。"""
    if ratio >= 0.60:
        return "身强"
    if ratio >= 0.52:
        return "偏强"
    if ratio >= 0.48:
        return "中和"
    if ratio >= 0.40:
        return "偏弱"
    return "身弱"


def analyze_strength(
    day_master: str,
    pillars: list[tuple[str, Pillar]],
    school: School = School.ZIPING,
) -> StrengthAnalysis:
    """评估日主旺衰。

    Args:
        day_master: 日干
        pillars: ``[(柱名, Pillar), ...]``，柱名取「年」「月」「日」「时」
        school: 流派，决定权重预设

    Returns:
        含完整分量的 ``StrengthAnalysis``。
    """
    weights = _WEIGHTS[school]
    dm_element = STEM_ELEMENT[day_master]

    totals: dict[str, float] = dict.fromkeys(ELEMENTS, 0.0)

    # --- 天干 -------------------------------------------------------------
    for name, pillar in pillars:
        weight = weights.stem.get(name)
        if weight is None:  # 日干即日主，不计入
            continue
        totals[STEM_ELEMENT[pillar.stem]] += weight

    # --- 地支（经藏干分配） -----------------------------------------------
    for name, pillar in pillars:
        weight = weights.branch.get(name, 0.0)
        if weight <= 0:
            continue
        hidden = BRANCH_HIDDEN[pillar.branch]
        shares = _HIDDEN_SHARES[len(hidden)]
        for stem, share in zip(hidden, shares, strict=True):
            totals[STEM_ELEMENT[stem]] += weight * share

    grand_total = sum(totals.values())
    scores = [
        ElementScore(
            element=el,
            score=round(totals[el], 3),
            percent=round(totals[el] / grand_total * 100, 2) if grand_total else 0.0,
        )
        for el in ELEMENTS
    ]

    # --- 分党 -------------------------------------------------------------
    # 同党：比劫（同我）+ 印（生我）；异党：食伤（我生）+ 财（我克）+ 官杀（克我）
    support_elements = {dm_element, element_generated_by(dm_element)}
    oppose_elements = {
        element_generates(dm_element),
        element_controls(dm_element),
        element_controlled_by(dm_element),
    }
    support = sum(totals[e] for e in support_elements)
    oppose = sum(totals[e] for e in oppose_elements)
    ratio = support / (support + oppose) if (support + oppose) else 0.0

    # --- 通根 -------------------------------------------------------------
    rooted_in = [
        name for name, pillar in pillars if any(STEM_ELEMENT[s] == dm_element for s in BRANCH_HIDDEN[pillar.branch])
    ]

    # --- 透干 -------------------------------------------------------------
    # 日主以外的天干中出现同我五行者
    transparent = [
        f"{name}干{pillar.stem}" for name, pillar in pillars if name != "日" and STEM_ELEMENT[pillar.stem] == dm_element
    ]

    notes = [weights.rationale]
    if not rooted_in:
        notes.append("日主地支无根，纵使同党总分不低，亦难任财官 —— 论命须格外谨慎")
    if len(rooted_in) >= 3:
        notes.append("日主三处以上通根，根重")

    return StrengthAnalysis(
        school=school,
        day_master_element=dm_element,
        scores=scores,
        support_score=round(support, 3),
        oppose_score=round(oppose, 3),
        support_ratio=round(ratio, 4),
        verdict=_verdict(ratio),
        is_rooted=bool(rooted_in),
        rooted_in=rooted_in,
        transparent_stems=transparent,
        notes=notes,
    )


__all__ = ["ELEMENTS", "StrengthWeights", "analyze_strength"]
