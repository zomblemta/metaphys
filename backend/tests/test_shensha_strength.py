"""神煞规则表与五行旺衰评分测试。

神煞是**查表**，所以测的是表本身对不对；旺衰是**加权求和**，所以测的是
分党、通根、透干这些分量是否自洽，以及流派切换是否真的改变结果。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from metaphys.engines.bazi import compute_bazi
from metaphys.engines.bazi.ganzhi import STEMS, ten_god, twelve_stage
from metaphys.engines.bazi.shensha import find_shensha, shen_sha_names
from metaphys.engines.bazi.strength import ELEMENTS, _verdict, analyze_strength
from metaphys.schemas.chart import BirthProfile, Gender, Pillar, School

BEIJING = (39.9042, 116.4074)
ALL_BRANCHES = "子丑寅卯辰巳午未申酉戌亥"


def _pillars(*specs: str) -> list[tuple[str, Pillar]]:
    """由 "甲寅" 形式的字符串构造带柱名的柱列表。"""
    names = ("年", "月", "日", "时")
    return [(names[i], Pillar(stem=gz[0], branch=gz[1])) for i, gz in enumerate(specs)]


def _hits(day_master: str, base_branch: str, target_branch: str):
    """造一张「年支为 base、时支为 target」的盘，取名含 target 的命中。"""
    return find_shensha(day_master, _pillars(f"甲{base_branch}", "甲子", "甲子", f"甲{target_branch}"))


# ---------------------------------------------------------------------------
# 神煞 —— 表本身的正确性
# ---------------------------------------------------------------------------


class TestShenShaTables:
    @pytest.mark.parametrize(
        ("day_master", "targets"),
        [
            ("甲", "丑未"),
            ("戊", "丑未"),
            ("庚", "丑未"),
            ("乙", "子申"),
            ("己", "子申"),
            ("丙", "亥酉"),
            ("丁", "亥酉"),
            ("壬", "卯巳"),
            ("癸", "卯巳"),
            ("辛", "午寅"),
        ],
    )
    def test_tian_yi_table(self, day_master, targets):
        """天乙贵人：甲戊庚牛羊，乙己鼠猴乡，丙丁猪鸡位，壬癸兔蛇藏，六辛逢马虎。"""
        for branch in targets:
            assert any(h.name == "天乙贵人" for h in _hits(day_master, "子", branch)), (
                f"{day_master} 的天乙贵人应含 {branch}"
            )

    @pytest.mark.parametrize(
        ("day_master", "target"),
        [
            ("甲", "巳"),
            ("乙", "午"),
            ("丙", "申"),
            ("丁", "酉"),
            ("戊", "申"),
            ("己", "酉"),
            ("庚", "亥"),
            ("辛", "子"),
            ("壬", "寅"),
            ("癸", "卯"),
        ],
    )
    def test_wen_chang_table(self, day_master, target):
        """文昌贵人：甲乙巳午报君知，丙戊申宫丁己鸡，庚猪辛鼠壬逢虎，癸人见卯入云梯。"""
        hits = _hits(day_master, "子", target)
        assert any(h.name == "文昌贵人" for h in hits)

    @pytest.mark.parametrize(
        ("base", "expected"),
        [
            ("寅", {"桃花": "卯", "驿马": "申", "华盖": "戌", "将星": "午", "劫煞": "亥", "亡神": "巳"}),
            ("午", {"桃花": "卯", "驿马": "申", "华盖": "戌", "将星": "午", "劫煞": "亥", "亡神": "巳"}),
            ("戌", {"桃花": "卯", "驿马": "申", "华盖": "戌", "将星": "午", "劫煞": "亥", "亡神": "巳"}),
            ("申", {"桃花": "酉", "驿马": "寅", "华盖": "辰", "将星": "子", "劫煞": "巳", "亡神": "亥"}),
            ("子", {"桃花": "酉", "驿马": "寅", "华盖": "辰", "将星": "子", "劫煞": "巳", "亡神": "亥"}),
            ("辰", {"桃花": "酉", "驿马": "寅", "华盖": "辰", "将星": "子", "劫煞": "巳", "亡神": "亥"}),
            ("巳", {"桃花": "午", "驿马": "亥", "华盖": "丑", "将星": "酉", "劫煞": "寅", "亡神": "申"}),
            ("酉", {"桃花": "午", "驿马": "亥", "华盖": "丑", "将星": "酉", "劫煞": "寅", "亡神": "申"}),
            ("丑", {"桃花": "午", "驿马": "亥", "华盖": "丑", "将星": "酉", "劫煞": "寅", "亡神": "申"}),
            ("亥", {"桃花": "子", "驿马": "巳", "华盖": "未", "将星": "卯", "劫煞": "申", "亡神": "寅"}),
            ("卯", {"桃花": "子", "驿马": "巳", "华盖": "未", "将星": "卯", "劫煞": "申", "亡神": "寅"}),
            ("未", {"桃花": "子", "驿马": "巳", "华盖": "未", "将星": "卯", "劫煞": "申", "亡神": "寅"}),
        ],
    )
    def test_san_he_tables(self, base, expected):
        """三合局类神煞：同局三支的结果必须完全一致。"""
        for name, target in expected.items():
            assert any(h.name == name for h in _hits("甲", base, target)), f"年支{base} 应推出 {name} 在 {target}"

    @pytest.mark.parametrize(
        ("day_master", "branch"),
        [("甲", "卯"), ("丙", "午"), ("戊", "午"), ("庚", "酉"), ("壬", "子")],
    )
    def test_yang_ren_matches_standard(self, day_master, branch):
        """阳干羊刃须与通行口诀吻合（甲卯、丙戊午、庚酉、壬子）。"""
        assert any(h.name == "羊刃" for h in _hits(day_master, "子", branch))

    @pytest.mark.parametrize("day_master", list(STEMS))
    def test_yang_ren_is_earthly_peak(self, day_master):
        """羊刃的定义即十二长生的帝旺 —— 两者必须永远自洽。"""
        peaks = [b for b in ALL_BRANCHES if twelve_stage(day_master, b) == "帝旺"]
        assert len(peaks) == 1, f"{day_master} 的帝旺位应唯一"
        assert any(h.name == "羊刃" for h in _hits(day_master, "子", peaks[0]))

    def test_base_is_recorded(self):
        """神煞必须记下以何为准 —— 查法流派分歧大，只给名字无法复核。"""
        hits = find_shensha("辛", _pillars("庚午", "壬午", "辛亥", "癸巳"))
        assert all(h.base == "日干" for h in hits if h.name in {"天乙贵人", "文昌贵人", "羊刃"})
        assert all(h.base in {"年支", "日支"} for h in hits if h.name == "驿马")

    def test_branch_based_checks_both_bases(self):
        """年支与日支两种查法并用，结论不同时分别记录而非二选一。

        年支午属寅午戌局（驿马在申）→ 命中时柱申；
        日支亥属亥卯未局（驿马在巳）→ 命中月柱巳。
        同一神煞由两个基准各推出一条，两条都须保留。
        """
        hits = find_shensha("甲", _pillars("甲午", "甲巳", "甲亥", "甲申"))
        yi_ma = [h for h in hits if h.name == "驿马"]
        assert {(h.base, h.pillar, h.branch) for h in yi_ma} == {
            ("年支", "时", "申"),
            ("日支", "月", "巳"),
        }

    def test_deduplication(self):
        """同年支同柱重复命中只记一条。"""
        hits = find_shensha("甲", _pillars("甲午", "甲午", "甲午", "甲子"))
        keys = [(h.name, h.base, h.pillar, h.branch) for h in hits]
        assert len(keys) == len(set(keys))

    def test_stable_ordering(self):
        """输出顺序必须稳定 —— 前端展示与回归测试都依赖它。"""
        args = ("辛", _pillars("庚午", "壬午", "辛亥", "癸巳"))
        assert find_shensha(*args) == find_shensha(*args)

    def test_empty_pillars(self):
        assert find_shensha("甲", []) == []
        assert shen_sha_names("甲", []) == []

    def test_known_anchor(self):
        """1990-06-15 北京命盘（庚午 壬午 辛亥 癸巳）的神煞。

        年支午属寅午戌局，日支亥属亥卯未局。
        羊刃（辛帝旺在申）、桃花（寅午戌在卯）、华盖（在戌）此盘均无 —— 不得凭空出现。
        """
        pillars = _pillars("庚午", "壬午", "辛亥", "癸巳")
        hits = find_shensha("辛", pillars)

        assert set(shen_sha_names("辛", pillars)) == {
            "天乙贵人",
            "将星",
            "劫煞",
            "亡神",
            "驿马",
        }
        assert {h.pillar for h in hits if h.name == "天乙贵人"} == {"年", "月"}
        assert all(h.branch == "巳" for h in hits if h.name in {"亡神", "驿马"})

    def test_shensha_flows_through_engine(self):
        chart = compute_bazi(
            BirthProfile(
                gender=Gender.MALE,
                birth_datetime=datetime(1990, 6, 15, 10, 30),
                place="北京",
                latitude=BEIJING[0],
                longitude=BEIJING[1],
            )
        )
        assert {h.name for h in chart.shen_sha} == {
            "天乙贵人",
            "将星",
            "劫煞",
            "亡神",
            "驿马",
        }


# ---------------------------------------------------------------------------
# 旺衰评分
# ---------------------------------------------------------------------------


class TestStrength:
    def test_day_master_element(self):
        result = analyze_strength("辛", _pillars("庚午", "壬午", "辛亥", "癸巳"))
        assert result.day_master_element == "金"

    def test_support_plus_oppose_equals_total(self):
        """同党异党穷尽五行，二者之和必须等于总分。"""
        result = analyze_strength("辛", _pillars("庚午", "壬午", "辛亥", "癸巳"))
        total = sum(s.score for s in result.scores)
        assert result.support_score + result.oppose_score == pytest.approx(total, abs=0.01)

    def test_scores_cover_all_five_elements(self):
        result = analyze_strength("辛", _pillars("庚午", "壬午", "辛亥", "癸巳"))
        assert [s.element for s in result.scores] == list(ELEMENTS)
        assert sum(s.percent for s in result.scores) == pytest.approx(100.0, abs=0.1)

    def test_ratio_in_range(self):
        result = analyze_strength("辛", _pillars("庚午", "壬午", "辛亥", "癸巳"))
        assert 0.0 <= result.support_ratio <= 1.0

    def test_weak_chart_verdict(self):
        """辛金生午月，官杀当令而金寡 —— 判身弱。"""
        result = analyze_strength("辛", _pillars("庚午", "壬午", "辛亥", "癸巳"))
        assert result.verdict in {"身弱", "偏弱"}
        assert result.support_ratio < 0.48

    def test_strong_chart_verdict(self):
        """甲木生寅月，比劫印星重重 —— 判身强。"""
        result = analyze_strength("甲", _pillars("壬子", "甲寅", "甲子", "乙亥"))
        assert result.verdict in {"身强", "偏强"}
        assert result.support_ratio > 0.52

    def test_verdict_boundaries(self):
        """判语分档必须单调，且分档点即是解读层引用强弱的依据。"""
        assert [_verdict(r) for r in (0.00, 0.39)] == ["身弱", "身弱"]
        assert _verdict(0.40) == "偏弱"
        assert _verdict(0.48) == "中和"
        assert _verdict(0.52) == "偏强"
        assert _verdict(0.60) == "身强"
        assert _verdict(1.00) == "身强"

        order = ["身弱", "偏弱", "中和", "偏强", "身强"]
        ranks = [_verdict(r / 100) for r in range(0, 101)]
        assert ranks == sorted(ranks, key=order.index), "占比升高不得使判语变弱"

    def test_school_changes_result(self):
        """流派不同则权重不同，结果必须真的改变 —— 否则流派配置形同虚设。"""
        pillars = _pillars("庚午", "壬午", "辛亥", "癸巳")
        ziping = analyze_strength("辛", pillars, School.ZIPING)
        xinpai = analyze_strength("辛", pillars, School.XINPAI)
        assert ziping.support_ratio != xinpai.support_ratio

    def test_every_school_recorded(self):
        pillars = _pillars("庚午", "壬午", "辛亥", "癸巳")
        for school in School:
            assert analyze_strength("辛", pillars, school).school is school

    def test_rationale_recorded(self):
        """必须说明所用流派的取法依据 —— 解读层要据此标注前提。"""
        result = analyze_strength("辛", _pillars("庚午", "壬午", "辛亥", "癸巳"))
        assert result.notes
        assert "月令" in result.notes[0]

    def test_month_command_dominates_ziping(self):
        """子平法月令最重 —— 改变月支对总分的影响必须大于改变年支。"""
        base = _pillars("庚午", "壬午", "辛亥", "癸巳")
        alt_year = _pillars("庚子", "壬午", "辛亥", "癸巳")
        alt_month = _pillars("庚午", "壬子", "辛亥", "癸巳")

        def scores(pillars):
            return {s.element: s.score for s in analyze_strength("辛", pillars, School.ZIPING).scores}

        baseline = scores(base)
        delta_year = sum(abs(baseline[e] - scores(alt_year)[e]) for e in ELEMENTS)
        delta_month = sum(abs(baseline[e] - scores(alt_month)[e]) for e in ELEMENTS)
        assert delta_month > delta_year

    def test_rooting_detected(self):
        """辛金在时支巳（藏庚）中有根。"""
        result = analyze_strength("辛", _pillars("庚午", "壬午", "辛亥", "癸巳"))
        assert result.is_rooted is True
        assert result.rooted_in == ["时"]

    def test_no_root_flagged(self):
        """地支全无同我五行时须标记 —— 无根是身弱的关键分野。"""
        result = analyze_strength("甲", _pillars("庚申", "辛酉", "甲申", "庚午"))
        assert result.is_rooted is False
        assert result.rooted_in == []
        assert any("无根" in n for n in result.notes)

    def test_heavy_root_flagged(self):
        result = analyze_strength("甲", _pillars("甲寅", "甲寅", "甲寅", "甲寅"))
        assert result.is_rooted is True
        assert len(result.rooted_in) >= 3
        assert any("根重" in n for n in result.notes)

    def test_transparent_stems_detected(self):
        """年干庚与日主辛同属金即透干；日干自身不算。"""
        result = analyze_strength("辛", _pillars("庚午", "壬午", "辛亥", "癸巳"))
        assert result.transparent_stems == ["年干庚"]

    def test_day_stem_excluded_from_scoring(self):
        """日干即日主本身，不得计入天干得分 —— 否则日主给自己加分。"""
        # 其余三柱相同，仅日干阴阳不同（同五行），得分应完全一致
        a = analyze_strength("甲", _pillars("丙寅", "丙寅", "甲寅", "丙寅"))
        b = analyze_strength("乙", _pillars("丙寅", "丙寅", "乙寅", "丙寅"))
        assert {s.element: s.score for s in a.scores} == {s.element: s.score for s in b.scores}

    def test_reproducible(self):
        args = ("辛", _pillars("庚午", "壬午", "辛亥", "癸巳"))
        assert analyze_strength(*args) == analyze_strength(*args)

    def test_three_pillar_chart_supported(self):
        """时辰未知时只有三柱 —— 旺衰仍须可算，不得崩溃。"""
        result = analyze_strength("辛", _pillars("庚午", "壬午", "辛亥"))
        assert result.verdict
        assert sum(s.score for s in result.scores) > 0

    def test_flows_through_engine(self):
        chart = compute_bazi(
            BirthProfile(
                gender=Gender.MALE,
                birth_datetime=datetime(1990, 6, 15, 10, 30),
                place="北京",
                latitude=BEIJING[0],
                longitude=BEIJING[1],
            ),
            school=School.XINPAI,
        )
        assert chart.strength is not None
        assert chart.strength.school is School.XINPAI
        assert chart.school is School.XINPAI

    def test_ten_god_partition_is_total(self):
        """十神与分党必须自洽：任一天干非属同党即属异党，无第三种。"""
        same_party = {"比肩", "劫财", "正印", "偏印"}
        other_party = {"食神", "伤官", "正财", "偏财", "正官", "七杀"}
        for stem in STEMS:
            god = ten_god("辛", stem)
            assert god in same_party | other_party, f"{stem} 的十神 {god} 不在任何一党"
            assert (god in same_party) != (god in other_party)
