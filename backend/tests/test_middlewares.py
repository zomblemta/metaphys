"""三个中间件的行为测试。

这些不是"代码能跑"的测试，而是**架构不变量的运行时防线**：

- 模型编造命盘数据 → 必须被抓到
- 模型正常输出 → 必须不被误报
- 模型一边追问一边排盘 → 必须只留下追问
- 工具抛异常 → 必须变成可读消息，且控制流信号不被吞掉
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from metaphys.engines.bazi import SHEN_SHA_NAMES, shen_sha_names
from metaphys.middlewares import (
    ALL_GANZHI,
    ClarificationMiddleware,
    GroundingMiddleware,
    ToolErrorHandlingMiddleware,
    chart_vocabulary,
    find_astro_claims,
    find_astro_fabrications,
    find_fabrications,
)
from metaphys.middlewares.clarification import _drop_sibling_tool_calls
from metaphys.schemas.chart import Pillar
from metaphys.tools.builtins.clarification import (
    ASK_CLARIFICATION_TOOL_NAME,
    clarification_question,
)

_PILLAR_LABELS = ("年", "月", "日", "时")
_STEMS = "甲乙丙丁戊己庚辛壬癸"
_BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
_DAY_PILLAR_SLOT = 2


def _request(tool_call: dict[str, Any]) -> ToolCallRequest:
    """手工构造一个工具调用请求。

    ``tool`` / ``runtime`` 传 None：被测的中间件只读 ``tool_call``，不碰这两个。
    """
    return ToolCallRequest(tool_call=tool_call, tool=None, state={}, runtime=None)


def _four_pillars(chart: dict[str, Any]) -> str:
    return " ".join(
        chart[field]["stem"] + chart[field]["branch"]
        for field in ("year_pillar", "month_pillar", "day_pillar", "time_pillar")
    )


# --------------------------------------------------------------------------- #
# Grounding：词表与六十甲子
# --------------------------------------------------------------------------- #
def test_all_ganzhi_is_exactly_the_sixty_cycle():
    """六十甲子必须是 60 个互不相同的两字组合 —— 核验的判据全建立在它之上。

    写错了不会报错，只会让某些编造漏检或让正常文本误报，所以把三条性质都钉住。
    """
    assert len(ALL_GANZHI) == 60
    assert len(set(ALL_GANZHI)) == 60
    assert all(gz[0] in _STEMS and gz[1] in _BRANCHES for gz in ALL_GANZHI)
    # 阳干配阳支、阴干配阴支 —— 这条排除了"甲丑"这类不存在的组合
    assert all(_STEMS.index(gz[0]) % 2 == _BRANCHES.index(gz[1]) % 2 for gz in ALL_GANZHI)


def test_shen_sha_names_covers_everything_the_engine_can_emit():
    """防漂移：``SHEN_SHA_NAMES`` 必须覆盖引擎可能吐出的全部神煞名。

    漏登记不会让任何东西报错，只会让核验**静默漏检**该神煞 —— 这比报错危险。
    所以用穷举换确定性：对每个日干、每个日支，轮流让四个柱位取遍六十甲子。
    神煞规则只依赖（日干，日支，某一柱的干支），这个枚举覆盖了全部可达组合；
    而全量 60⁴ 太慢，没必要。
    """
    reachable: set[str] = set()

    for day_master in _STEMS:
        for day_branch in _BRANCHES:
            for slot in range(len(_PILLAR_LABELS)):
                for gan_zhi in ALL_GANZHI:
                    pillars = []
                    for index, label in enumerate(_PILLAR_LABELS):
                        if index == _DAY_PILLAR_SLOT:
                            stem, branch = day_master, day_branch
                        elif index == slot:
                            stem, branch = gan_zhi[0], gan_zhi[1]
                        else:
                            stem, branch = "甲", "子"
                        pillars.append((label, Pillar(stem=stem, branch=branch)))
                    reachable.update(shen_sha_names(day_master, pillars))

    assert reachable, "穷举没跑出任何神煞 —— 枚举本身写错了，这条测试就没在保护什么"

    missing = reachable - set(SHEN_SHA_NAMES)
    assert not missing, (
        f"引擎能算出 {sorted(missing)}，但它们不在 SHEN_SHA_NAMES 里。"
        f"往 engines/bazi/shensha.py 加神煞时必须在 SHEN_SHA_NAMES 登记，否则核验会漏掉它 —— "
        f"模型编造这个神煞时不会被抓到。"
    )

    # 反向也要成立：登记表里出现引擎算不出的名字，通常意味着名字写错了（错别字、
    # 少一个字）。写错的名字永远匹配不上任何真实神煞，只会让提到它的情况无差别误报。
    unknown = set(SHEN_SHA_NAMES) - reachable
    assert not unknown, f"SHEN_SHA_NAMES 里的 {sorted(unknown)} 引擎从来算不出来，名字可能写错了"


# --------------------------------------------------------------------------- #
# Grounding：抓编造
# --------------------------------------------------------------------------- #
def test_flags_fabricated_day_pillar(bazi_chart_dict: dict[str, Any]):
    real = bazi_chart_dict["day_pillar"]
    actual = real["stem"] + real["branch"]
    assert actual != "甲子", "夹具命盘的日柱恰好是甲子，这条用例就失去了意义"

    flags = find_fabrications("您的日柱是甲子。", bazi_chart_dict)

    kinds = {flag["kind"] for flag in flags}
    assert "pillar" in kinds, "「日柱是甲子」与盘上不符，必须报 pillar"
    assert "ganzhi" in kinds, "甲子不在盘上，必须报 ganzhi"
    pillar_flag = next(flag for flag in flags if flag["kind"] == "pillar")
    assert pillar_flag["expected"] == f"日柱{actual}"
    assert pillar_flag["excerpt"], "必须带上下文片段，否则前端无法定位"


def test_flags_ganzhi_asserted_on_the_wrong_pillar(bazi_chart_dict: dict[str, Any]):
    """干支在盘上、但安到了错误的柱位 —— 这是最容易蒙混过去的一类错。

    它不触发 ``ganzhi`` 检查（该干支确实合法），只能靠柱位检查抓。
    """
    day = bazi_chart_dict["day_pillar"]
    day_gan_zhi = day["stem"] + day["branch"]
    year = bazi_chart_dict["year_pillar"]

    flags = find_fabrications(f"您的年柱是{day_gan_zhi}。", bazi_chart_dict)

    assert len(flags) == 1, f"应当只报柱位错，实际：{[(f['kind'], f['claimed']) for f in flags]}"
    assert flags[0]["kind"] == "pillar"
    assert flags[0]["expected"] == f"年柱{year['stem']}{year['branch']}"


def test_flags_fabricated_day_master(bazi_chart_dict: dict[str, Any]):
    actual = bazi_chart_dict["day_master"]
    wrong = next(stem for stem in _STEMS if stem != actual)

    flags = find_fabrications(f"您的日主是{wrong}。", bazi_chart_dict)

    assert any(flag["kind"] == "day_master" and flag["expected"] == actual for flag in flags)


def test_flags_fabricated_shensha(bazi_chart_dict: dict[str, Any]):
    on_chart = {item["name"] for item in bazi_chart_dict["shen_sha"]}
    absent = sorted(set(SHEN_SHA_NAMES) - on_chart)
    assert absent, "这个命盘带全了所有神煞，换一个固定命盘才能测出漏检"

    flags = find_fabrications(f"命带{absent[0]}，主一生顺遂。", bazi_chart_dict)

    assert any(flag["kind"] == "shensha" and flag["claimed"] == absent[0] for flag in flags)


def test_flags_bare_ganzhi_with_no_claim_around_it(bazi_chart_dict: dict[str, Any]):
    """没有任何「X柱」「日主」引导词的裸干支，也要抓。

    模型编造时常写成「流年遇丁卯，主变动」这种形式 —— 没有句式可依赖，
    只能靠六十甲子子串扫描。
    """
    on_chart = {gz for gz in ALL_GANZHI if gz in chart_vocabulary(bazi_chart_dict)}
    invented = next(gz for gz in ALL_GANZHI if gz not in on_chart)

    flags = find_fabrications(f"流年遇{invented}，主变动。", bazi_chart_dict)

    assert any(flag["kind"] == "ganzhi" and flag["claimed"] == invented for flag in flags)


# --------------------------------------------------------------------------- #
# Grounding：不误报（与抓编造同等重要）
# --------------------------------------------------------------------------- #
def test_accepts_a_fully_truthful_answer(bazi_chart_dict: dict[str, Any]):
    """全真的输出必须一处都不报 —— 误报会让模型每轮都被打断，比漏报更快毁掉体验。"""
    truth = (
        f"您的四柱是 {_four_pillars(bazi_chart_dict)}，日主{bazi_chart_dict['day_master']}。"
        f"四柱：年柱{bazi_chart_dict['year_pillar']['stem']}{bazi_chart_dict['year_pillar']['branch']}，"
        f"日柱{bazi_chart_dict['day_pillar']['stem']}{bazi_chart_dict['day_pillar']['branch']}。"
    )
    assert find_fabrications(truth, bazi_chart_dict) == []


def test_accepts_the_shensha_actually_on_the_chart(bazi_chart_dict: dict[str, Any]):
    on_chart = [item["name"] for item in bazi_chart_dict["shen_sha"]]
    assert on_chart, "夹具命盘没有任何神煞，这条用例测不到东西"
    text = "、".join(f"命带{name}" for name in on_chart)
    assert find_fabrications(text, bazi_chart_dict) == []


@pytest.mark.parametrize(
    "text",
    [
        "今天天气不错，我们先聊聊你的出生信息。",
        "请问您的出生地是哪里？",
        "",
        "   ",
    ],
    ids=["普通散文", "问句", "空串", "全空白"],
)
def test_plain_text_never_triggers(bazi_chart_dict: dict[str, Any], text: str):
    assert find_fabrications(text, bazi_chart_dict) == []


def test_middleware_is_silent_until_a_chart_exists():
    """没排盘时不核验 —— 没有词表，任何扫描都只会把正常干支提及全报成编造。"""
    mw = GroundingMiddleware()
    ai = AIMessage("您的日柱是甲子。")

    assert mw.after_model({"messages": [ai], "charts": {}}, None) is None
    assert mw.after_model({"messages": [ai], "charts": None}, None) is None
    assert mw.after_model({"messages": [ai]}, None) is None


def test_middleware_writes_flags_and_appends_a_correction(bazi_chart_dict: dict[str, Any]):
    mw = GroundingMiddleware()
    state = {"messages": [HumanMessage("看盘"), AIMessage("您的日柱是甲子。")], "charts": {"bazi": bazi_chart_dict}}

    update = mw.after_model(state, None)

    assert update is not None
    assert update["grounding_flags"], "命中必须落进 grounding_flags，供前端审计"
    correction = update["messages"][0]
    assert "落地核验未通过" in correction.content
    assert "甲子" in correction.content


def test_middleware_stays_silent_on_a_truthful_answer(bazi_chart_dict: dict[str, Any]):
    mw = GroundingMiddleware()
    state = {
        "messages": [AIMessage(f"您的四柱是 {_four_pillars(bazi_chart_dict)}。")],
        "charts": {"bazi": bazi_chart_dict},
    }
    assert mw.after_model(state, None) is None


def test_async_hook_matches_the_sync_one(bazi_chart_dict: dict[str, Any]):
    """``aafter_model`` 与 ``after_model`` 必须同源 —— 两条路径结论不一致的话，
    走异步的网关与走同步的脚本会得到不同的核验结果。"""
    mw = GroundingMiddleware()
    state = {"messages": [AIMessage("您的日柱是甲子。")], "charts": {"bazi": bazi_chart_dict}}

    sync_update = mw.after_model(state, None)
    async_update = asyncio.run(mw.aafter_model(state, None))

    assert sync_update is not None and async_update is not None
    assert sync_update["grounding_flags"] == async_update["grounding_flags"]


# --------------------------------------------------------------------------- #
# Grounding：星盘 —— 只认结构断言
# --------------------------------------------------------------------------- #
_HOUSE_NUMERALS = "一二三四五六七八九十"


def _point(chart: dict[str, Any], name: str) -> dict[str, Any]:
    return next(point for point in chart["points"] if point["name"] == name)


def _other_sign(actual: str) -> str:
    """一个**不是** ``actual`` 的星座名 —— 拿来构造必定为假的断言。"""
    from metaphys.engines.astro.points import SIGN_NAMES

    return next(sign for sign in SIGN_NAMES.values() if sign != actual)


def _other_house(actual: int) -> int:
    return 1 if actual != 1 else 2


def _ordinal(number: int) -> str:
    """1 → 一，11 → 十一。只覆盖 1–12。"""
    if number <= 10:
        return _HOUSE_NUMERALS[number - 1]
    return "十" + _HOUSE_NUMERALS[number - 11]


def _astro_truth(chart: dict[str, Any]) -> str:
    """由盘本身生成的**全真**表述 —— 误报测试的前提。

    落座与落宫分成两小句写，不是啰嗦：判据要求点名紧挨着关系词，写成
    「月亮在双鱼座，第七宫」时那句落宫根本不会被扫到，于是这条用例会变成
    "因为压根没匹配上所以没报"的空转。
    """
    return "".join(
        f"{point['name']}在{point['sign']}座。{point['name']}位于第{_ordinal(point['house'])}宫。"
        for point in chart["points"]
    )


def test_flags_a_sun_in_the_wrong_sign(astro_chart_dict: dict[str, Any]):
    sun = _point(astro_chart_dict, "太阳")

    flags = find_astro_fabrications(f"您的太阳在{_other_sign(sun['sign'])}座，这让你很顾家。", astro_chart_dict)

    assert [flag["kind"] for flag in flags] == ["astro_sign"]
    assert flags[0]["expected"] == f"太阳在{sun['sign']}"


def test_flags_an_ascendant_sign_assertion_with_a_copula(astro_chart_dict: dict[str, Any]):
    """「上升星座是X」是极常见的说法 —— 不覆盖它等于没核验上升点。"""
    ascendant = _point(astro_chart_dict, "上升")

    flags = find_astro_fabrications(f"您的上升星座是{_other_sign(ascendant['sign'])}座。", astro_chart_dict)

    assert [flag["kind"] for flag in flags] == ["astro_sign"]


def test_a_copula_without_the_word_planet_sign_is_not_a_placement_claim(astro_chart_dict: dict[str, Any]):
    """「火星是天蝎座的守护星」是入庙陈述，不是落座断言 —— 必须不报。

    火星确实主天蝎，这句话讲的是**星座的守护关系**，与命主的火星在哪毫无关系。
    而它与「上升星座是狮子座」形式上几乎一样，区别只在中间有没有「星座」二字。

    刻意用**火星**而不是金星：金星在本夹具盘上恰好落在金牛，用它做反面样本时
    即便把「是」误当成关系词也照样不会报，用例就成了空转。
    """
    assert _point(astro_chart_dict, "火星")["sign"] != "天蝎", "夹具前提：火星不在天蝎，否则这条测不到东西"

    assert find_astro_fabrications("火星是天蝎座的守护星，代表行动力。", astro_chart_dict) == []


def test_flags_a_planet_in_the_wrong_house(astro_chart_dict: dict[str, Any]):
    moon = _point(astro_chart_dict, "月亮")

    flags = find_astro_fabrications(f"月亮在{_other_house(moon['house'])}宫，说明你重视关系。", astro_chart_dict)

    assert [flag["kind"] for flag in flags] == ["astro_house"]
    assert flags[0]["expected"] == f"月亮在第{moon['house']}宫"


@pytest.mark.parametrize("template", ["{n}在{h}宫。", "{n}在第{h}宫。", "{n}在 第{h} 宫。", "{n}落于第{h}宫。"])
def test_house_numbers_are_normalized_before_comparing(template: str, astro_chart_dict: dict[str, Any]):
    """同一件事有多种写法，它们必须被当成同一件事。

    不归一化的话，每一句**正确**的表述都会被判成编造 —— 一种把正确内容报成
    错误的失效方式，比漏报更难发现，因为用户的直观反应是"它怎么又说错了"。
    """
    moon = _point(astro_chart_dict, "月亮")
    text = template.format(n="月亮", h=_ordinal(moon["house"]))

    assert find_astro_fabrications(text, astro_chart_dict) == []


def test_a_wrong_house_written_in_chinese_numerals_is_flagged(astro_chart_dict: dict[str, Any]):
    """**反方向**的用例：中文数字写错时必须报出来。

    只有"中文数字写对了不报"那一条是不够的 —— 把归一化整个删掉（认不出中文数字
    就跳过该断言）也能让它绿。要证明归一化真的在比对，就得让一个中文数字的
    错误断言必须命中。
    """
    moon = _point(astro_chart_dict, "月亮")
    wrong = _other_house(moon["house"])

    flags = find_astro_fabrications(f"月亮在{_ordinal(wrong)}宫。", astro_chart_dict)

    assert [flag["kind"] for flag in flags] == ["astro_house"]
    assert flags[0]["claimed"] == f"月亮在第{wrong}宫"
    assert flags[0]["expected"] == f"月亮在第{moon['house']}宫"


def test_an_arabic_house_number_is_read_the_same_way(astro_chart_dict: dict[str, Any]):
    moon = _point(astro_chart_dict, "月亮")

    assert find_astro_fabrications(f"月亮在第{moon['house']}宫。", astro_chart_dict) == []
    assert find_astro_fabrications(f"月亮在{_other_house(moon['house'])}宫。", astro_chart_dict) != []


def test_house_numerals_beyond_twelve_are_not_claims(astro_chart_dict: dict[str, Any]):
    """「月亮在第十三宫」不存在这种说法 —— 不报，而不是报成"宫位错了"。"""
    assert find_astro_fabrications("月亮在第十三宫。", astro_chart_dict) == []


def test_flags_an_aspect_that_is_not_on_the_chart(astro_chart_dict: dict[str, Any]):
    on_chart = {frozenset((aspect["p1"], aspect["p2"])) for aspect in astro_chart_dict["aspects"]}
    names = ("太阳", "月亮", "水星", "金星")
    first, second = next((a, b) for a in names for b in names if a != b and frozenset((a, b)) not in on_chart)

    flags = find_astro_fabrications(f"{first}刑{second}，让你内心常有拉扯。", astro_chart_dict)

    assert [flag["kind"] for flag in flags] == ["astro_aspect"]
    assert flags[0]["claimed"] == f"{first}刑{second}"


def test_an_aspect_on_the_chart_is_accepted_in_either_order(astro_chart_dict: dict[str, Any]):
    """盘上的 ``p1``/``p2`` 顺序不固定，核验必须按**无序对**比对。

    否则「月亮刑水星」会被判成不存在（盘上写的是「水星刑月亮」），于是一条
    完全正确的解读被报成编造。
    """
    aspect = astro_chart_dict["aspects"][0]

    forward = find_astro_fabrications(f"{aspect['p1']}{aspect['aspect']}{aspect['p2']}。", astro_chart_dict)
    backward = find_astro_fabrications(f"{aspect['p2']}{aspect['aspect']}{aspect['p1']}。", astro_chart_dict)

    assert forward == [] and backward == []


def test_accepts_a_fully_truthful_astro_answer(astro_chart_dict: dict[str, Any]):
    """全真的星盘输出必须一处都不报 —— 与八字同等重要。"""
    assert find_astro_fabrications(_astro_truth(astro_chart_dict), astro_chart_dict) == []


@pytest.mark.parametrize(
    "text",
    [
        "我朋友是处女座，典型的完美主义。",
        "你是典型的双子座，好奇心重。",
        "月亮代表你的情绪需求。",
        "上升趋势很明显，事业在走上坡路。",
        "太阳系有八大行星。",
        "今天我们聊聊你的星盘。",
        "水星逆行期间不宜签约。",
        "   ",
        "",
    ],
    ids=["别人的星座", "裸星座名", "月亮非断言", "上升非断言", "太阳系", "普通问句", "水逆", "全空白", "空串"],
)
def test_astro_plain_text_never_triggers(text: str, astro_chart_dict: dict[str, Any]):
    """散文里提到盘外的星座**不报** —— 这是我们主动放弃的能力。

    星盘的词表不是封闭的：「双子座」「天蝎座」是日常词汇，用户自己就会说
    "我朋友是处女座"。照搬八字那套裸词扫描必然误报。这条用例把那个取舍**钉住**，
    免得日后有人"顺手把裸星座扫描补上"。
    """
    assert find_astro_fabrications(text, astro_chart_dict) == []


def test_a_generic_sentence_shaped_like_a_claim_is_flagged(astro_chart_dict: dict[str, Any]):
    """已知的**误报**，刻意保留 —— 把取舍写进测试而不是留在注释里。

    「太阳在狮子座的人喜欢被关注」是通用句式，不是对命主盘的断言，但它在形式上
    与「你的太阳在狮子座」完全一样。不做语义判断就分不开这两者。

    倒向严格一侧是权衡的结果：漏报的代价是用户被告知一个错误的星座，
    误报的代价只是模型多收到一条更正。
    """
    sun = _point(astro_chart_dict, "太阳")

    flags = find_astro_fabrications(f"太阳在{_other_sign(sun['sign'])}座的人喜欢被关注。", astro_chart_dict)

    assert [flag["kind"] for flag in flags] == ["astro_sign"]


def test_a_contradiction_later_in_the_text_is_still_caught(astro_chart_dict: dict[str, Any]):
    """先说了真话、后面又说了假话 —— 假的那句仍要报。

    去重若按点名做，前一句真话会把后一句假话挡掉，正好漏掉最该抓的自相矛盾。
    """
    sun = _point(astro_chart_dict, "太阳")
    wrong = _other_sign(sun["sign"])

    flags = find_astro_fabrications(f"您的太阳在{sun['sign']}座。另外，太阳星座是{wrong}座。", astro_chart_dict)

    assert [flag["claimed"] for flag in flags] == [f"太阳在{wrong}"]


# --------------------------------------------------------------------------- #
# Grounding：没有星盘时的探测（find_astro_claims）
# --------------------------------------------------------------------------- #
# 这是 ALL_GANZHI 在星盘上的对应物 —— 对抗性测试靠它回答"模型有没有作出本该由
# 工具回答的断言"。它与核验共用同一处句式遍历，所以下面每一条都在同时钉住两边。
def test_claims_detects_a_placement_assertion():
    assert find_astro_claims("您的太阳在双子座。") == ["太阳在双子"]


def test_claims_detects_the_copula_form():
    """「上升星座是狮子座」是极常见的说法，必须与关系词那一路同等看待。"""
    assert find_astro_claims("您的上升星座是狮子座。") == ["上升在狮子"]


def test_claims_detects_a_house_assertion_in_either_numeral():
    assert find_astro_claims("月亮在第七宫。") == ["月亮在第7宫"]
    assert find_astro_claims("月亮位于第 7 宫。") == ["月亮在第7宫"]


def test_claims_detects_an_aspect_assertion():
    assert find_astro_claims("太阳刑月亮。") == ["太阳刑月亮"]


def test_claims_deduplicates_repeated_assertions():
    """同一句话说两遍只算一条 —— 报错信息里重复三遍不会让人更明白。"""
    assert find_astro_claims("太阳在双子。太阳在双子。") == ["太阳在双子"]


@pytest.mark.parametrize(
    "text",
    [
        "我朋友是处女座，她特别爱干净。",
        "双子座的人通常很善变。",
        "你是典型的天蝎座性格。",
        "第七宫代表伴侣与婚姻。",
        "火星是天蝎座的守护星。",
        "",
        "   ",
    ],
    ids=["日常提及星座", "泛论星座", "断言但无谓语", "讲宫位知识", "讲守护星", "空串", "全空白"],
)
def test_claims_never_fires_on_prose(text: str):
    """**裸星座名与讲知识的句子一律不报。**

    这是 M3 最核心的取舍（见 ``M3-FINDINGS.md`` §二）：星盘的词表是开放的，
    「双子座」是日常词汇，裸词扫描必然误伤 —— 而且误伤的是**用户自己的话**。

    下面每一条都是"看起来像、其实不是断言"的写法。有人日后想把探测放宽成裸词
    扫描时，这几条会红。
    """
    assert find_astro_claims(text) == []


def test_a_house_number_beyond_twelve_is_not_a_claim():
    """「第十三宫」不是断言错误，是压根不存在这种说法。"""
    assert find_astro_claims("月亮在第十三宫。") == []


def test_claims_and_fabrications_agree_on_what_counts_as_a_claim(astro_chart_dict: dict[str, Any]):
    """探测与核验必须认出**同一批**句子 —— 否则两边的判据会各自漂移。

    构造一句必定为假的断言：探测要看得见它，核验也要报它。若日后只改了其中
    一处的句式，这条会红。
    """
    sun = _point(astro_chart_dict, "太阳")
    text = f"您的太阳在{_other_sign(sun['sign'])}座。"

    assert find_astro_claims(text) == [f"太阳在{_other_sign(sun['sign'])}"]
    assert [flag["kind"] for flag in find_astro_fabrications(text, astro_chart_dict)] == ["astro_sign"]


def test_a_fabrication_carries_a_usable_excerpt(astro_chart_dict: dict[str, Any]):
    """命中必须带**能定位到原文**的上下文片段。

    ``_excerpt`` 找不到锚点时返回空串。落宫断言的锚点若取归一化后的「7宫」而不是
    原文的「第七宫」，就永远找不到 —— 于是片段**静默**变空，前端再也定位不到
    那句话，而没有任何东西会报错。
    """
    moon = _point(astro_chart_dict, "月亮")
    wrong = _other_house(int(moon["house"]))

    flags = find_astro_fabrications(f"关于您，月亮在{_ordinal(wrong)}宫。", astro_chart_dict)

    assert [flag["kind"] for flag in flags] == ["astro_house"]
    assert flags[0]["excerpt"], "上下文片段不能是空的 —— 那说明锚点没在原文里找到"
    assert f"{_ordinal(wrong)}宫" in flags[0]["excerpt"]


# --------------------------------------------------------------------------- #
# Grounding：两张盘分别核验、合并上报
# --------------------------------------------------------------------------- #
def test_every_chart_kind_written_by_a_tool_has_a_finder():
    """工具往 state 里写的每一种盘，都必须有对应的核验函数。

    漏一个**不会报错** —— 那种盘会完全不核验，模型可以随便编而没有任何东西会响。
    这是这条防线最安静的失效方式，所以用机械手段盯着，而不是靠记性。
    """
    import ast
    from pathlib import Path

    from metaphys.middlewares.grounding import _FINDERS

    tools_root = Path(__file__).resolve().parents[1] / "packages" / "harness" / "metaphys" / "tools"
    kinds: set[str] = set()
    for path in tools_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "charts" and isinstance(value, ast.Dict):
                    kinds |= {
                        item.value
                        for item in value.keys
                        if isinstance(item, ast.Constant) and isinstance(item.value, str)
                    }

    assert kinds, "一个盘种都没扫到 —— 判据本身失效了，不是「没有工具写盘」"
    assert kinds == set(_FINDERS), f"这些盘种没有核验函数：{kinds - set(_FINDERS)}"


def test_middleware_checks_the_astro_chart_too(astro_chart_dict: dict[str, Any]):
    mw = GroundingMiddleware()
    sun = _point(astro_chart_dict, "太阳")
    state = {
        "messages": [AIMessage(f"您的太阳在{_other_sign(sun['sign'])}座。")],
        "charts": {"astro": astro_chart_dict},
    }

    update = mw.after_model(state, None)

    assert update is not None
    assert any(flag["kind"].startswith("astro_") for flag in update["grounding_flags"])


def test_middleware_reports_the_charts_it_actually_checked(
    bazi_chart_dict: dict[str, Any], astro_chart_dict: dict[str, Any]
):
    """更正文案只说核验过的盘。

    若两张盘都在、却只写「与命盘不符」，用户会以为星盘也一并核过了；反过来，
    只排了星盘却写「命盘」，用户根本不知道说的是哪张盘。
    """
    mw = GroundingMiddleware()
    sun = _point(astro_chart_dict, "太阳")
    wrong_sign = _other_sign(sun["sign"])

    astro_only = mw.after_model(
        {"messages": [AIMessage(f"太阳在{wrong_sign}座。")], "charts": {"astro": astro_chart_dict}},
        None,
    )
    assert astro_only is not None
    assert "星盘" in astro_only["messages"][0].content
    assert "命盘" not in astro_only["messages"][0].content

    both = mw.after_model(
        {
            "messages": [AIMessage(f"您的日柱是甲子，太阳在{wrong_sign}座。")],
            "charts": {"bazi": bazi_chart_dict, "astro": astro_chart_dict},
        },
        None,
    )
    assert both is not None
    kinds = {flag["kind"] for flag in both["grounding_flags"]}
    assert any(kind.startswith("astro_") for kind in kinds)
    assert any(not kind.startswith("astro_") for kind in kinds), "八字那条被星盘的核验挤掉了"
    correction = both["messages"][0].content
    assert "命盘" in correction and "星盘" in correction


def test_a_bazi_only_chart_does_not_judge_astro_sentences(bazi_chart_dict: dict[str, Any]):
    """只有八字盘时，一句星盘的话不该被八字词表判成编造。

    两套判据的**词表毫无重叠**，混用会把一个盘种的内容整片报成编造。
    """
    mw = GroundingMiddleware()

    update = mw.after_model(
        {
            "messages": [AIMessage("您的太阳在双子座，月亮在双鱼座，上升在狮子座。")],
            "charts": {"bazi": bazi_chart_dict},
        },
        None,
    )

    assert update is None


# --------------------------------------------------------------------------- #
# Clarification
# --------------------------------------------------------------------------- #
def test_wrap_tool_call_short_circuits_ask_clarification():
    mw = ClarificationMiddleware()
    request = _request(
        {
            "name": ASK_CLARIFICATION_TOOL_NAME,
            "args": {"question": "您的出生地是哪里？", "missing_fields": ["place"]},
            "id": "call_1",
        }
    )
    executed: list[Any] = []

    result = mw.wrap_tool_call(request, executed.append)

    assert executed == [], "追问必须短路工具函数体，否则等于问了两遍"
    assert isinstance(result, Command)
    assert result.goto == END, "追问要结束本轮，等用户回答"
    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.content == "您的出生地是哪里？"
    assert message.tool_call_id == "call_1"
    assert message.artifact["missing_fields"] == ["place"]


def test_wrap_tool_call_falls_back_when_question_is_blank():
    mw = ClarificationMiddleware()
    result = mw.wrap_tool_call(_request({"name": ASK_CLARIFICATION_TOOL_NAME, "args": {}, "id": "c"}), lambda r: None)
    assert result.update["messages"][0].content


def test_wrap_tool_call_passes_other_tools_through():
    mw = ClarificationMiddleware()
    request = _request({"name": "bazi_chart", "args": {}, "id": "c"})
    sentinel = object()

    assert mw.wrap_tool_call(request, lambda r: sentinel) is sentinel


def test_async_wrap_tool_call_short_circuits_too():
    mw = ClarificationMiddleware()
    request = _request({"name": ASK_CLARIFICATION_TOOL_NAME, "args": {"question": "?"}, "id": "c"})

    async def handler(_: Any) -> Any:
        raise AssertionError("不应执行")

    result = asyncio.run(mw.awrap_tool_call(request, handler))
    assert result.goto == END


def test_drops_sibling_tool_calls_in_the_same_turn():
    """一轮里既有追问又有排盘 —— 只能留下追问。

    否则用户会看到"它一边问我出生地、一边已经给了一份时柱多半是错的命盘"。
    """
    ai = AIMessage(
        "",
        id="ai-1",
        tool_calls=[
            {"name": ASK_CLARIFICATION_TOOL_NAME, "args": {"question": "出生地？"}, "id": "a"},
            {"name": "bazi_chart", "args": {"place": "北京"}, "id": "b"},
        ],
    )

    update = _drop_sibling_tool_calls({"messages": [HumanMessage("帮我排盘"), ai]})

    assert update is not None
    rewritten = update["messages"][0]
    assert [call["name"] for call in rewritten.tool_calls] == [ASK_CLARIFICATION_TOOL_NAME]
    assert rewritten.id == ai.id, "必须保留 id —— add_messages 按 id 替换，否则会追加而非覆盖"


def test_normal_tool_calls_are_never_dropped():
    """回归：这里曾把每一轮**正常**的工具调用都清空成 ``[]``。

    当时的判据是"kept 与 tool_calls 长度不等就改写"，而在没有 ask_clarification
    的一轮里 kept 恒为空 —— 于是模型再也调不动任何工具，且不报任何错。
    """
    ai = AIMessage("", tool_calls=[{"name": "bazi_chart", "args": {}, "id": "b"}])
    assert _drop_sibling_tool_calls({"messages": [ai]}) is None


def test_a_lone_clarification_call_is_left_alone():
    ai = AIMessage("", tool_calls=[{"name": ASK_CLARIFICATION_TOOL_NAME, "args": {}, "id": "a"}])
    assert _drop_sibling_tool_calls({"messages": [ai]}) is None


def test_earlier_rounds_are_never_rewritten():
    """只检查最后一条消息。

    若向前回溯找"最近一条带工具调用的 AIMessage"，会捞到**上一轮**的调用并改写它 ——
    等于篡改历史，而且同样不报错。
    """
    older = AIMessage("", id="ai-old", tool_calls=[{"name": "bazi_chart", "args": {}, "id": "b"}])
    tool_result = ToolMessage("命盘如下", tool_call_id="b")
    newer = AIMessage("根据命盘……", id="ai-new")

    assert _drop_sibling_tool_calls({"messages": [HumanMessage("排盘"), older, tool_result, newer]}) is None


def test_no_messages_is_not_an_error():
    assert _drop_sibling_tool_calls({"messages": []}) is None
    assert _drop_sibling_tool_calls({}) is None


def test_clarification_question_is_readable_from_the_tool_call():
    """追问轮里助手**确实说了话** —— 只不过话在工具参数里，``content`` 是空的。

    回归：「帮我排盘」而缺出生地的那一轮曾返回空回复，用户看到一片沉默 ——
    而这恰恰是最需要把话说明白的时刻。
    """
    ai = AIMessage(
        "",
        tool_calls=[
            {
                "name": ASK_CLARIFICATION_TOOL_NAME,
                "args": {"question": "请问您的出生地是哪里？", "missing_fields": ["place"]},
                "id": "a",
            }
        ],
    )

    assert clarification_question(ai) == "请问您的出生地是哪里？"


@pytest.mark.parametrize(
    ("message", "why"),
    [
        (AIMessage("普通的一句话"), "不是追问"),
        (AIMessage("", tool_calls=[{"name": "bazi_chart", "args": {}, "id": "b"}]), "调的是别的工具"),
        (AIMessage("", tool_calls=[{"name": ASK_CLARIFICATION_TOOL_NAME, "args": {}, "id": "a"}]), "没给问句"),
        (
            AIMessage("", tool_calls=[{"name": ASK_CLARIFICATION_TOOL_NAME, "args": {"question": "   "}, "id": "a"}]),
            "问句全是空白",
        ),
        (HumanMessage("用户说的话"), "不是 AI 消息"),
        (ToolMessage("工具结果", tool_call_id="c"), "是工具结果"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_clarification_question_is_empty_when_there_is_none(message: Any, why: str):
    """取不到问句时要给空串而不是抛异常 —— 调用方拿它做回退，不是主路径。"""
    assert clarification_question(message) == "", f"这条不该被当成追问：{why}"


# --------------------------------------------------------------------------- #
# 工具异常兜底
# --------------------------------------------------------------------------- #
def test_tool_exception_becomes_an_error_message():
    mw = ToolErrorHandlingMiddleware()
    request = _request({"name": "bazi_chart", "args": {}, "id": "call_9"})

    def handler(_: ToolCallRequest) -> Any:
        raise RuntimeError("依赖服务不可用")

    result = mw.wrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call_9"
    assert "RuntimeError" in result.content
    assert "不要据此推测任何命盘数据" in result.content, "兜底消息必须堵住'报错就自己编一个'这条路"
    assert result.artifact["error_type"] == "RuntimeError"


def test_control_flow_signals_are_not_swallowed():
    """``GraphBubbleUp`` 是 interrupt 一类的中断信号，不是错误。

    被兜底逻辑吞掉会让整个人机中断机制失效 —— 表现为"该停下来问用户的时候没有停"，
    而且没有任何报错。
    """

    class ControlSignal(GraphBubbleUp):
        pass

    mw = ToolErrorHandlingMiddleware()
    request = _request({"name": "bazi_chart", "args": {}, "id": "c"})

    def handler(_: ToolCallRequest) -> Any:
        raise ControlSignal("interrupt")

    with pytest.raises(ControlSignal):
        mw.wrap_tool_call(request, handler)


def test_async_tool_exception_becomes_an_error_message():
    mw = ToolErrorHandlingMiddleware()
    request = _request({"name": "bazi_chart", "args": {}, "id": "c"})

    async def handler(_: ToolCallRequest) -> Any:
        raise ValueError("坏参数")

    result = asyncio.run(mw.awrap_tool_call(request, handler))
    assert isinstance(result, ToolMessage) and result.status == "error"


def test_passing_tools_are_returned_untouched():
    mw = ToolErrorHandlingMiddleware()
    request = _request({"name": "bazi_chart", "args": {}, "id": "c"})
    sentinel = ToolMessage("正常结果", tool_call_id="c")

    assert mw.wrap_tool_call(request, lambda _: sentinel) is sentinel


@pytest.mark.parametrize(
    "label,field", [("胎元", "tai_yuan"), ("命宫", "ming_gong"), ("身宫", "shen_gong"), ("胎息", "tai_xi")]
)
def test_auxiliary_ganzhi_checks_field_identity(label, field):
    chart = {field: "癸酉", "day_master": "甲", "year_pillar": {"stem": "甲", "branch": "子"}}
    assert find_fabrications(f"{label}是癸酉", chart) == []
    assert any(flag["kind"] == "auxiliary" for flag in find_fabrications(f"{label}是甲子", chart))
    assert find_fabrications(f"{label}是癸酉", {})
    # Birth metadata cannot whitelist invented numerical claims.
    assert find_fabrications("胎元是丙寅", {**chart, "profile": {"name": "丙寅"}})
