"""干支基础表 —— 十神与十二长生。

lunar-python 能算这些，但只能相对于**它自己那一盘**的日主。当年柱/月柱与
日柱/时柱取自不同时刻时（见 ``chart.py`` 的说明），必须能独立重算，
故在此自建表。

纯查表，零依赖。
"""

from __future__ import annotations

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"

# 天干五行
STEM_ELEMENT: dict[str, str] = {
    "甲": "木",
    "乙": "木",
    "丙": "火",
    "丁": "火",
    "戊": "土",
    "己": "土",
    "庚": "金",
    "辛": "金",
    "壬": "水",
    "癸": "水",
}

# 天干阴阳：甲丙戊庚壬为阳
STEM_YANG: dict[str, bool] = {s: i % 2 == 0 for i, s in enumerate(STEMS)}

# 地支藏干（本气、中气、余气）
BRANCH_HIDDEN: dict[str, list[str]] = {
    "子": ["癸"],
    "丑": ["己", "癸", "辛"],
    "寅": ["甲", "丙", "戊"],
    "卯": ["乙"],
    "辰": ["戊", "乙", "癸"],
    "巳": ["丙", "庚", "戊"],
    "午": ["丁", "己"],
    "未": ["己", "丁", "乙"],
    "申": ["庚", "壬", "戊"],
    "酉": ["辛"],
    "戌": ["戊", "辛", "丁"],
    "亥": ["壬", "甲"],
}

# 五行相生：木→火→土→金→水→木
_GENERATES: dict[str, str] = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}

# 五行相克：木克土、土克水、水克火、火克金、金克木
_CONTROLS: dict[str, str] = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

# 十神名。键为 (生克关系, 阴阳是否相同)
_TEN_GODS: dict[tuple[str, bool], str] = {
    ("same", True): "比肩",
    ("same", False): "劫财",
    ("generate", True): "食神",
    ("generate", False): "伤官",
    ("control", True): "偏财",
    ("control", False): "正财",
    ("control_by", True): "七杀",
    ("control_by", False): "正官",
    ("generate_by", True): "偏印",
    ("generate_by", False): "正印",
}

# 十二长生，自长生起顺行
TWELVE_STAGES = ("长生", "沐浴", "冠带", "临官", "帝旺", "衰", "病", "死", "墓", "绝", "胎", "养")

# 各天干的长生地支
_CHANGSHENG: dict[str, str] = {
    "甲": "亥",
    "乙": "午",
    "丙": "寅",
    "丁": "酉",
    "戊": "寅",
    "己": "酉",
    "庚": "巳",
    "辛": "子",
    "壬": "申",
    "癸": "卯",
}


def ten_god(day_master: str, other: str) -> str:
    """以 ``day_master`` 为基准，求 ``other`` 天干的十神。"""
    dm_el, other_el = STEM_ELEMENT[day_master], STEM_ELEMENT[other]
    same_polarity = STEM_YANG[day_master] == STEM_YANG[other]

    if dm_el == other_el:
        relation = "same"
    elif _GENERATES[dm_el] == other_el:
        relation = "generate"
    elif _CONTROLS[dm_el] == other_el:
        relation = "control"
    elif _CONTROLS[other_el] == dm_el:
        relation = "control_by"
    else:
        relation = "generate_by"

    return _TEN_GODS[(relation, same_polarity)]


def hidden_stems(branch: str) -> list[str]:
    """地支藏干。"""
    return list(BRANCH_HIDDEN[branch])


def hidden_ten_gods(day_master: str, branch: str) -> list[str]:
    """地支藏干各自的十神，顺序与 :func:`hidden_stems` 一致。"""
    return [ten_god(day_master, s) for s in BRANCH_HIDDEN[branch]]


def twelve_stage(day_master: str, branch: str) -> str:
    """日主在 ``branch`` 上的十二长生（地势）。

    阳干顺行、阴干逆行 —— 这是本表与"阳顺阴逆"约定一致的关键。
    """
    start = BRANCHES.index(_CHANGSHENG[day_master])
    idx = BRANCHES.index(branch)
    offset = (idx - start) % 12 if STEM_YANG[day_master] else (start - idx) % 12
    return TWELVE_STAGES[offset]


def ten_god_for_stem(day_master: str, stem: str) -> str:
    """日柱天干对自身即"日主"。"""
    return "日主" if stem == day_master else ten_god(day_master, stem)


# --- 五行关系查询 ---------------------------------------------------------
# 旺衰评分需要按"生我/我生/克我/我克"把五行分党，此处提供方向明确的查询，
# 避免调用方到处推导 dict 的键值方向而搞反。


def element_generates(element: str) -> str:
    """我生者 —— 即食伤。"""
    return _GENERATES[element]


def element_generated_by(element: str) -> str:
    """生我者 —— 即印。"""
    return next(e for e, target in _GENERATES.items() if target == element)


def element_controls(element: str) -> str:
    """我克者 —— 即财。"""
    return _CONTROLS[element]


def element_controlled_by(element: str) -> str:
    """克我者 —— 即官杀。"""
    return next(e for e, target in _CONTROLS.items() if target == element)


__all__ = [
    "BRANCHES",
    "BRANCH_HIDDEN",
    "STEMS",
    "STEM_ELEMENT",
    "STEM_YANG",
    "TWELVE_STAGES",
    "element_controlled_by",
    "element_controls",
    "element_generated_by",
    "element_generates",
    "hidden_stems",
    "hidden_ten_gods",
    "ten_god",
    "ten_god_for_stem",
    "twelve_stage",
]
