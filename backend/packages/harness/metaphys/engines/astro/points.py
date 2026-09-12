"""星盘词表 —— 简体中文名的**唯一真源**，且**不依赖 kerykeion**。

这个模块被三处消费，它们必须永远一致：

1. :mod:`metaphys.engines.astro.chart` —— 写进 ``AstroChart`` 的名字；
2. :mod:`metaphys.engines.astro.svg` —— SVG 的语言包覆盖；
3. :mod:`metaphys.middlewares.grounding` —— 落地核验认的词表。

第 3 条是**本模块刻意不 import kerykeion** 的原因：中间件按架构不变量不得
具备排盘能力（见 ``tests/test_harness_boundary.py``）。若词表跟着引擎模块走，
核验侧就会顺带把瑞士星历表拉进依赖图 —— 护栏看不出这一点（它只看单文件的
顶层 import），但那条不变量已经被绕过了。名字只是字符串，不需要星历表。

**为什么必须覆盖语言包。** kerykeion 的 ``CN`` 语言包里繁体与简体**混排**：
``出生图``/``宫位划分``（简体宫）与 ``宮``/``太陽``/``上升點``（繁体）出现在
同一份文档里。实测渲染出的中文串共 39 个，其中含繁体字的有 11 个。逐键覆盖
之后才能得到一份全简体的图 —— 而完成的标准是一条**测试**（渲染结果里不得
出现繁体字），不是人工核对，理由见 :data:`TRADITIONAL_ONLY`。
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# 行星与轴点
# --------------------------------------------------------------------------- #
#: kerykeion 的英文点名 → 简体中文。**键必须与 kerykeion 的 ``active_points``
#: 取值完全一致**（首字母大写、蛇形），否则那个点会静默地不被翻译。
PLANET_NAMES: dict[str, str] = {
    "Sun": "太阳",
    "Moon": "月亮",
    "Mercury": "水星",
    "Venus": "金星",
    "Mars": "火星",
    "Jupiter": "木星",
    "Saturn": "土星",
    "Uranus": "天王星",
    "Neptune": "海王星",
    "Pluto": "冥王星",
    "Ascendant": "上升",
    "Medium_Coeli": "天顶",
}

#: 参与排盘的点。**刻意收窄**：不含小行星、凯龙、莉莉丝、恒星与南北交点。
#:
#: 每多一个点，就多一个"模型可以张冠李戴"的对象，也多一面核验要覆盖的网。
#: 而多出来的那些点对解读的边际价值远低于它们的误报成本 —— 一个把「凯龙」
#: 说错的模型不会因此更懂命主，但用户会因此不再相信整张盘。
ACTIVE_POINTS: tuple[str, ...] = tuple(PLANET_NAMES)

# --------------------------------------------------------------------------- #
# 星座
# --------------------------------------------------------------------------- #
#: kerykeion 的三字母缩写（顺序即黄道顺序，从白羊起）→ 简体中文。
SIGN_NAMES: dict[str, str] = {
    "Ari": "白羊",
    "Tau": "金牛",
    "Gem": "双子",
    "Can": "巨蟹",
    "Leo": "狮子",
    "Vir": "处女",
    "Lib": "天秤",
    "Sco": "天蝎",
    "Sag": "射手",
    "Cap": "摩羯",
    "Aqu": "水瓶",
    "Pis": "双鱼",
}

#: 黄道十二宫顺序 —— ``sign_num`` 即此元组的索引。
SIGN_ORDER: tuple[str, ...] = tuple(SIGN_NAMES)

#: 星座名 → 序号。核验时把 ``sign_num`` 与星盘上的值对照。
SIGN_INDEX: dict[str, int] = {abbr: index for index, abbr in enumerate(SIGN_ORDER)}


# --------------------------------------------------------------------------- #
# 相位
# --------------------------------------------------------------------------- #
#: kerykeion 的相位键（小写英文）→ 简体中文。
ASPECT_NAMES: dict[str, str] = {
    "conjunction": "合",
    "opposition": "冲",
    "square": "刑",
    "trine": "拱",
    "sextile": "六合",
}

#: 只保留五大主相位。
#:
#: kerykeion 默认还会给 quintile / biquintile / semi-sextile / quincunx /
#: sesquiquadrate —— 这些属于进阶技法，各家用法分歧大，且**不是**本产品要
#: 讲的层次。留下它们只会让模型有更多机会说错。
MAJOR_ASPECTS: frozenset[str] = frozenset(ASPECT_NAMES)

#: 相位配置 —— **必须同时**喂给算相位的那一路（``AspectsFactory``）与画图的
#: 那一路（``ChartDataFactory``）。
#:
#: 这两处的默认值并不相同：算相位的那路默认含 quintile，画图的那路默认也含
#: quintile，但两者的容许度表各写各的。若只在一处收窄，模型读到的相位表与图
#: 上画出来的线就会**不一致** —— 图上多几条解释不了的线，或者图上没有而解读
#: 里提了。用户按图索骥时无从判断谁对。
#:
#: 容许度取 kerykeion 自己的主相位默认值，不自创：换一套容许度就是换一套
#: 判据，那属于流派选择，不该由一个渲染模块顺手决定。
ACTIVE_ASPECTS: tuple[dict[str, Any], ...] = (
    {"name": "conjunction", "orb": 10},
    {"name": "opposition", "orb": 10},
    {"name": "trine", "orb": 8},
    {"name": "sextile", "orb": 6},
    {"name": "square", "orb": 5},
)


# --------------------------------------------------------------------------- #
# 宫位
# --------------------------------------------------------------------------- #
#: kerykeion 的宫位名 → 序号（1–12）。
HOUSE_NAMES: dict[str, int] = {
    "First_House": 1,
    "Second_House": 2,
    "Third_House": 3,
    "Fourth_House": 4,
    "Fifth_House": 5,
    "Sixth_House": 6,
    "Seventh_House": 7,
    "Eighth_House": 8,
    "Ninth_House": 9,
    "Tenth_House": 10,
    "Eleventh_House": 11,
    "Twelfth_House": 12,
}

#: 宫位的中文说法。核验时要把「第七宫」「第 7 宫」「7 宫」都归一化再比对。
HOUSE_ORDINALS: tuple[str, ...] = (
    "一",
    "二",
    "三",
    "四",
    "五",
    "六",
    "七",
    "八",
    "九",
    "十",
    "十一",
    "十二",
)


# --------------------------------------------------------------------------- #
# SVG 语言包覆盖
# --------------------------------------------------------------------------- #
#: 简体化覆盖表。**顶层键**（非 ``celestial_points`` 之下的部分）。
#:
#: 每一项都对应实测渲染出的繁体串，来源键列在注释里 —— 这些键名是从
#: ``kerykeion.settings.translation_strings.LANGUAGE_SETTINGS["CN"]`` 里
#: 反查出来的，不是猜的。也包含当前排盘不渲染、但换图种就会出现的键
#: （``solar_return``/``lunar_return``），一并改掉以免日后漏网。
_UI_OVERRIDES: dict[str, Any] = {
    "cusp": "宫",  # 宫头标签「宮」
    "east": "东",  # 经度后缀「東」
    "east_letter": "东",
    "longitude": "经度",
    "latitude": "纬度",
    "apparent_geocentric": "视地心",  # 「视角」的取值
    "perspective_type": "视角",
    "zodiac": "黄道",
    "tropical": "热带",
    "air": "风",
    "fire": "火",
    "earth": "土",
    "water": "水",
    "and_word": "与",
    "houses": "宫位系统",
    "planets_and_house": "行星与宫位",
    "midpoints": "中点",
    "solar_return": "太阳回归",
    "lunar_return": "月亮回归",
    # 宫位制名称：ChartDrawer 会把它印在图上
    "houses_system_P": "普拉西达斯",
    "houses_system_A": "等宫",
    "houses_system_D": "等宫 (MC)",
    "houses_system_N": "等宫/1=白羊",
    "houses_system_V": "等宫/韦洛",
    "houses_system_W": "等宫/整体星座",
    "houses_system_X": "轴向旋转系统/子午线宫",
    "houses_system_Y": "APC宫位",
}


def language_pack() -> dict[str, Any]:
    """构造传给 ``ChartDrawer(language_pack=...)`` 的覆盖表。

    **注意传参形态。** 源码是 ``overrides = {self.chart_language: dict(language_pack)}``
    （``chart_drawer.py``），所以这里要传的是**该语言那一层**的 dict，
    **不是** ``{"CN": {...}}``。多包一层不会报错、也不会生效 —— 一个比正确
    写法更安静的错法，因此有正反两个用例钉住它。

    ``celestial_points`` 由 :data:`PLANET_NAMES` **全量生成**（只覆盖参与排盘的
    12 个点）：这样 SVG 上的行星名与 ``AstroChart`` 里的名字出自同一个表，
    改一处即两处同时改，不可能对不上。
    """
    return {
        **_UI_OVERRIDES,
        "celestial_points": dict(PLANET_NAMES),
    }


#: 只出现在繁体、简体写法不同的字。
#:
#: 用来做"渲染结果必须是全简体"的判据。**必须按字列，不能按词列** —— 按词列
#: 会漏掉日后新出现的键（比如换一种图就多印一个繁体标签），而按字列能兜住。
#: 集合里的字都取自 kerykeion 的 CN 包实测值，不是凭印象写的。
TRADITIONAL_ONLY: frozenset[str] = frozenset(
    "宮東視黃熱緯經點風與為這個們來時實現計劃語詞體業處線麵臺萬億產專書寫讀聽話達觀週運動"
)


__all__ = [
    "ACTIVE_ASPECTS",
    "ACTIVE_POINTS",
    "ASPECT_NAMES",
    "HOUSE_NAMES",
    "HOUSE_ORDINALS",
    "MAJOR_ASPECTS",
    "PLANET_NAMES",
    "SIGN_INDEX",
    "SIGN_NAMES",
    "SIGN_ORDER",
    "TRADITIONAL_ONLY",
    "language_pack",
]
