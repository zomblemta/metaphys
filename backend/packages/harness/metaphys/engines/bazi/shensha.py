"""八字神煞规则表。

lunar-python **不提供**八字神煞 —— 它的 ``getDayJiShen()`` / ``getDayXiongSha()``
是**黄历择日**层面的吉凶神煞，与八字神煞同名异物，不可混用（M0 已确认）。
故此处照古籍自建规则表。

每条命中都记下 ``base``（以何为准）与 ``pillar``（命中哪一柱）。神煞查法流派
分歧不小，只给名字用户无法复核 —— 而"可溯源"正是本项目专业向定位的支点。

纯查表，零依赖。
"""

from __future__ import annotations

from metaphys.engines.bazi.ganzhi import BRANCHES, twelve_stage
from metaphys.schemas.chart import Pillar, ShenSha

# 天乙贵人：以**日干**查地支。口诀「甲戊庚牛羊，乙己鼠猴乡，丙丁猪鸡位，
# 壬癸兔蛇藏，六辛逢马虎」。
_TIAN_YI: dict[str, str] = {
    "甲": "丑未",
    "戊": "丑未",
    "庚": "丑未",
    "乙": "子申",
    "己": "子申",
    "丙": "亥酉",
    "丁": "亥酉",
    "壬": "卯巳",
    "癸": "卯巳",
    "辛": "午寅",
}

# 文昌贵人：以**日干**查地支。口诀「甲乙巳午报君知，丙戊申宫丁己鸡，
# 庚猪辛鼠壬逢虎，癸人见卯入云梯」。
_WEN_CHANG: dict[str, str] = {
    "甲": "巳",
    "乙": "午",
    "丙": "申",
    "丁": "酉",
    "戊": "申",
    "己": "酉",
    "庚": "亥",
    "辛": "子",
    "壬": "寅",
    "癸": "卯",
}

# 三合局归组：以**年支或日支**查
_SAN_HE_OF: dict[str, str] = {
    "寅": "寅午戌",
    "午": "寅午戌",
    "戌": "寅午戌",
    "申": "申子辰",
    "子": "申子辰",
    "辰": "申子辰",
    "巳": "巳酉丑",
    "酉": "巳酉丑",
    "丑": "巳酉丑",
    "亥": "亥卯未",
    "卯": "亥卯未",
    "未": "亥卯未",
}

# 三合局类神煞：各局对应的地支
_SAN_HE_SHEN_SHA: dict[str, dict[str, str]] = {
    "寅午戌": {"桃花": "卯", "驿马": "申", "华盖": "戌", "将星": "午", "劫煞": "亥", "亡神": "巳"},
    "申子辰": {"桃花": "酉", "驿马": "寅", "华盖": "辰", "将星": "子", "劫煞": "巳", "亡神": "亥"},
    "巳酉丑": {"桃花": "午", "驿马": "亥", "华盖": "丑", "将星": "酉", "劫煞": "寅", "亡神": "申"},
    "亥卯未": {"桃花": "子", "驿马": "巳", "华盖": "未", "将星": "卯", "劫煞": "申", "亡神": "寅"},
}


# 以日干为准的神煞名。**新增此类神煞时必须在此登记** —— GroundingMiddleware
# 用 SHEN_SHA_NAMES 识别"模型提到了盘上没有的神煞"，漏登记会让新神煞失去核验。
# 三合局类的名字从 _SAN_HE_SHEN_SHA 派生，不会漂移。
_DAY_MASTER_SHEN_SHA = ("天乙贵人", "文昌贵人", "羊刃")

#: 本引擎可能产出的全部神煞名。
SHEN_SHA_NAMES: frozenset[str] = frozenset(
    _DAY_MASTER_SHEN_SHA + tuple({name for group in _SAN_HE_SHEN_SHA.values() for name in group})
)


def _yang_ren_branches(day_master: str) -> list[str]:
    """羊刃所在地支。

    羊刃取日主的**帝旺**之位。如此定义可复用十二长生表，且阳干（甲卯、丙午、
    戊午、庚酉、壬子）与通行口诀完全吻合；阴干则依「阳顺阴逆」自动落在
    乙寅、丁己巳、辛申、癸亥。阴干是否论刃，流派有分歧 —— 解读层需注意。
    """
    return [b for b in BRANCHES if twelve_stage(day_master, b) == "帝旺"]


def find_shensha(day_master: str, pillars: list[tuple[str, Pillar]]) -> list[ShenSha]:
    """查找命中的神煞。

    Args:
        day_master: 日干
        pillars: ``[(柱名, Pillar), ...]``，柱名取「年」「月」「日」「时」

    Returns:
        命中记录，按柱序与神煞名稳定排列。
    """
    if not pillars:
        return []

    branches = {name: p.branch for name, p in pillars}
    year_branch = branches.get("年")
    day_branch = branches.get("日")

    hits: list[ShenSha] = []

    def record(name: str, base: str, pillar_name: str, branch: str) -> None:
        hits.append(ShenSha(name=name, base=base, pillar=pillar_name, branch=branch))

    # --- 以日干为准 -------------------------------------------------------
    tian_yi = set(_TIAN_YI.get(day_master, ""))
    wen_chang = _WEN_CHANG.get(day_master, "")
    yang_ren = set(_yang_ren_branches(day_master))

    for pillar_name, pillar in pillars:
        if pillar.branch in tian_yi:
            record("天乙贵人", "日干", pillar_name, pillar.branch)
        if pillar.branch == wen_chang:
            record("文昌贵人", "日干", pillar_name, pillar.branch)
        if pillar.branch in yang_ren:
            record("羊刃", "日干", pillar_name, pillar.branch)

    # --- 以年支、日支为准 -------------------------------------------------
    # 年支与日支是通行的两种查法，结论可能不同，故分别记录而非二选一。
    for base_label, base_branch in (("年支", year_branch), ("日支", day_branch)):
        if base_branch is None:
            continue
        group = _SAN_HE_OF.get(base_branch)
        if group is None:
            continue
        for name, target in _SAN_HE_SHEN_SHA[group].items():
            for pillar_name, pillar in pillars:
                if pillar.branch == target:
                    record(name, base_label, pillar_name, pillar.branch)

    # 去重：同柱同神煞但**基准不同**时保留两条（基准本身即信息）；
    # 完全相同的记录合并。
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[ShenSha] = []
    for hit in hits:
        key = (hit.name, hit.base, hit.pillar, hit.branch)
        if key not in seen:
            seen.add(key)
            unique.append(hit)

    # 稳定排序：先按柱序，再按神煞名 —— 保证回归测试与前端展示稳定
    pillar_order = {"年": 0, "月": 1, "日": 2, "时": 3}
    unique.sort(key=lambda h: (pillar_order.get(h.pillar, 9), h.name, h.base))
    return unique


def shen_sha_names(day_master: str, pillars: list[tuple[str, Pillar]]) -> list[str]:
    """仅取去重后的神煞名，供快速总览。"""
    seen: list[str] = []
    for hit in find_shensha(day_master, pillars):
        if hit.name not in seen:
            seen.append(hit.name)
    return seen


__all__ = ["SHEN_SHA_NAMES", "find_shensha", "shen_sha_names"]
