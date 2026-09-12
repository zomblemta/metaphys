"""八字排盘回归测试集。

这是"专业向"定位能站住的证据 —— 排盘引擎的正确性不靠肉眼抽查，
靠一批**已知答案**的硬案例。每一条断言都对应一个会让产品静默出错的场景。

覆盖：真太阳时 · 经度改变时柱 · 夏令时（含缺口） · 跨日 · 节气边界 · 闰月 · 精度降级。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from metaphys.engines.bazi import (
    InvalidLunarDateError,
    NonexistentLocalTimeError,
    compute_bazi,
    to_true_solar_time,
)
from metaphys.schemas.chart import BirthProfile, Calendar, Gender, TimeAccuracy

# 常用出生地坐标
BEIJING = (39.9042, 116.4074)
SHANGHAI = (31.2304, 121.4737)
URUMQI = (43.8256, 87.6168)
LHASA = (29.6500, 91.1208)


def make_profile(
    dt: datetime,
    place_coords: tuple[float, float],
    *,
    place: str = "测试地",
    gender: Gender = Gender.MALE,
    accuracy: TimeAccuracy = TimeAccuracy.EXACT,
    calendar: Calendar = Calendar.SOLAR,
    is_leap_month: bool = False,
) -> BirthProfile:
    lat, lng = place_coords
    return BirthProfile(
        gender=gender,
        birth_datetime=dt,
        place=place,
        latitude=lat,
        longitude=lng,
        time_accuracy=accuracy,
        calendar=calendar,
        is_leap_month=is_leap_month,
    )


# ---------------------------------------------------------------------------
# 真太阳时 —— 本项目的标志性论证
# ---------------------------------------------------------------------------


class TestTrueSolarTime:
    """均时差与经度校正。不做校正则西部城市的时柱从源头就是错的。"""

    @pytest.mark.parametrize(
        ("when", "expected_minutes", "label"),
        [
            (datetime(2024, 11, 3, 12), 16.4, "11月初 极大"),
            (datetime(2024, 2, 11, 12), -14.2, "2月中 极小"),
            (datetime(2024, 5, 14, 12), 3.7, "5月中"),
        ],
    )
    def test_equation_of_time_extremes(self, when, expected_minutes, label):
        """均时差极值须与天文年历吻合（容差 0.5 分钟）。"""
        from metaphys.engines.bazi.truesolar import equation_of_time_minutes

        assert equation_of_time_minutes(when) == pytest.approx(expected_minutes, abs=0.5), label

    def test_longitude_changes_hour_pillar(self):
        """同一钟表时间，乌鲁木齐与北京的时柱必须不同 —— 这是不做校正就会静默出错的铁证。"""
        dt = datetime(1990, 6, 15, 10, 30)
        beijing = compute_bazi(make_profile(dt, BEIJING))
        urumqi = compute_bazi(make_profile(dt, URUMQI))

        assert beijing.year_pillar.gan_zhi == "庚午"
        assert beijing.month_pillar.gan_zhi == "壬午"
        assert beijing.day_pillar.gan_zhi == "辛亥"

        assert beijing.time_pillar is not None and beijing.time_pillar.gan_zhi == "癸巳"
        assert urumqi.time_pillar is not None and urumqi.time_pillar.gan_zhi == "壬辰"

        # 三柱相同，仅时柱不同 —— 差异必须可归因于经度，而非其他因素
        assert beijing.four_pillars.split()[:3] == urumqi.four_pillars.split()[:3]

    def test_eastern_cities_unaffected(self):
        """东部城市经度接近 120°E，时柱不应因校正而改变。"""
        dt = datetime(1990, 6, 15, 10, 30)
        for coords in (BEIJING, SHANGHAI):
            chart = compute_bazi(make_profile(dt, coords))
            assert chart.time_pillar is not None
            assert chart.time_pillar.gan_zhi == "癸巳"

    def test_lhasa_also_shifts(self):
        """拉萨经度 91.12°E，同样跨时辰边界 —— 验证不是乌鲁木齐个例。"""
        chart = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), LHASA))
        assert chart.time_pillar is not None
        assert chart.time_pillar.gan_zhi == "壬辰"

    def test_same_instant_same_year_month_pillar(self):
        """同一瞬间出生者，年月二柱必须相同；时柱由当地真太阳时决定，可以不同。

        全国统一使用北京时间，故两地钟表读数相同即同一瞬间。
        """
        dt = datetime(1990, 6, 15, 10, 30)
        beijing = compute_bazi(make_profile(dt, BEIJING))
        urumqi = compute_bazi(make_profile(dt, URUMQI))

        assert beijing.year_pillar.gan_zhi == urumqi.year_pillar.gan_zhi
        assert beijing.month_pillar.gan_zhi == urumqi.month_pillar.gan_zhi
        assert beijing.time_pillar is not None and urumqi.time_pillar is not None
        assert beijing.time_pillar.gan_zhi != urumqi.time_pillar.gan_zhi

    def test_same_instant_near_solar_term(self):
        """回归测试：靠近节气出生时，年月二柱尤其不能随经度漂移。

        2024 立春为 02-04 16:27（北京时间）。取 17:30 出生：
        北京真太阳时约 17:02（已过立春），乌鲁木齐约 15:07（尚未到）。
        若整盘按真太阳时推算，两地会排出不同的年柱与月柱 —— 而它们本是同一瞬间。
        """
        dt = datetime(2024, 2, 4, 17, 30)
        beijing = compute_bazi(make_profile(dt, BEIJING))
        urumqi = compute_bazi(make_profile(dt, URUMQI))

        assert beijing.year_pillar.gan_zhi == "甲辰"
        assert urumqi.year_pillar.gan_zhi == "甲辰", "同一瞬间，年柱不得随出生地改变"
        assert beijing.month_pillar.gan_zhi == urumqi.month_pillar.gan_zhi

    def test_day_rollover_detected(self):
        """真太阳时可跨自然日 —— 此时日柱须按前一日推算，否则整盘错位。"""
        result = to_true_solar_time(datetime(1990, 6, 15, 0, 20), URUMQI[1])
        assert result.day_rolled is True
        assert result.true_solar.date() == datetime(1990, 6, 14).date()
        assert any("跨日" in n for n in result.notes)


# ---------------------------------------------------------------------------
# 夏令时
# ---------------------------------------------------------------------------


class TestDaylightSaving:
    """中国 1986–1991 实行夏令时，钟表快于标准时 1 小时。"""

    def test_dst_recognized(self):
        assert to_true_solar_time(datetime(1988, 6, 1, 2, 30), BEIJING[1]).utc_offset_hours == 9

    def test_non_dst_recognized(self):
        assert to_true_solar_time(datetime(1988, 11, 1, 2, 30), BEIJING[1]).utc_offset_hours == 8

    def test_after_dst_abolished(self):
        assert to_true_solar_time(datetime(1992, 6, 1, 2, 30), BEIJING[1]).utc_offset_hours == 8

    def test_dst_note_emitted(self):
        result = to_true_solar_time(datetime(1988, 6, 1, 2, 30), BEIJING[1])
        assert any("夏令时" in n for n in result.notes)

    def test_dst_gap_raises(self):
        """夏令时起始日 02:00 直接跳到 03:00，02:00–03:00 在现实中不存在。

        此时不能猜一个值继续排盘 —— 静默产出错误命盘正是要杜绝的失败模式。
        """
        with pytest.raises(NonexistentLocalTimeError):
            to_true_solar_time(datetime(1986, 5, 4, 2, 30), BEIJING[1])

    def test_dst_gap_boundaries(self):
        """缺口边界：[02:00, 03:00) 报错，01:59 与 03:00 正常。"""
        to_true_solar_time(datetime(1986, 5, 4, 1, 59), BEIJING[1])
        with pytest.raises(NonexistentLocalTimeError):
            to_true_solar_time(datetime(1986, 5, 4, 2, 0), BEIJING[1])
        to_true_solar_time(datetime(1986, 5, 4, 3, 0), BEIJING[1])

    def test_dst_gap_propagates_through_engine(self):
        with pytest.raises(NonexistentLocalTimeError):
            compute_bazi(make_profile(datetime(1986, 5, 4, 2, 30), BEIJING))


# ---------------------------------------------------------------------------
# 节气边界 —— 月柱换柱依据
# ---------------------------------------------------------------------------


class TestSolarTermBoundary:
    """八字换**年柱**以立春为界（非正月初一），换**月柱**以节为界。"""

    def test_year_pillar_flips_at_lichun_2024(self):
        """2024 立春为 02-04 16:27（北京时间）。此前属癸卯年，此后属甲辰年。"""
        before = compute_bazi(make_profile(datetime(2024, 2, 4, 10, 0), BEIJING))
        after = compute_bazi(make_profile(datetime(2024, 2, 4, 20, 0), BEIJING))

        assert before.year_pillar.gan_zhi == "癸卯"
        assert after.year_pillar.gan_zhi == "甲辰"

    def test_day_after_lichun_is_new_year(self):
        """春节（2024-02-10）在立春之后 —— 命理年份以立春为准，与农历年无关。"""
        chart = compute_bazi(make_profile(datetime(2024, 2, 5, 12, 0), BEIJING))
        assert chart.year_pillar.gan_zhi == "甲辰"

    def test_near_boundary_after_flagged(self):
        """交节之后出生 —— 换柱已发生，须标记。"""
        chart = compute_bazi(make_profile(datetime(2024, 2, 4, 16, 30), BEIJING))
        assert chart.nearby_solar_term is not None
        assert chart.nearby_solar_term.name == "立春"
        assert any("节气" in w for w in chart.warnings)

    def test_near_boundary_before_flagged(self):
        """交节**之前**出生 —— 此时换柱的是即将到来的节。

        回归测试：只查 ``getPrevJieQi`` 的实现在这里会静默返回 None，
        恰好漏掉最需要预警的情形。
        """
        chart = compute_bazi(make_profile(datetime(2024, 2, 4, 16, 0), BEIJING))
        assert chart.nearby_solar_term is not None
        assert chart.nearby_solar_term.name == "立春"
        assert chart.nearby_solar_term.minutes_away < 0, "出生时刻在交节之前"
        assert chart.year_pillar.gan_zhi == "癸卯", "未过立春，仍属癸卯年"

    def test_boundary_detection_is_symmetric(self):
        """交节前后 N 分钟都应被捕获，且指向同一个节。"""
        before = compute_bazi(make_profile(datetime(2024, 2, 4, 16, 0), BEIJING)).nearby_solar_term
        after = compute_bazi(make_profile(datetime(2024, 2, 4, 16, 30), BEIJING)).nearby_solar_term
        assert before is not None and after is not None
        assert before.name == after.name == "立春"
        assert before.at == after.at

    def test_zhongqi_does_not_trigger(self):
        """中气不换月柱，在其附近出生不得告警 —— 否则是误报。

        2024 大寒为 01-20 22:07，丑月自小寒延续至立春，大寒前后月柱不变。
        """
        before = compute_bazi(make_profile(datetime(2024, 1, 20, 21, 0), BEIJING))
        after = compute_bazi(make_profile(datetime(2024, 1, 21, 0, 0), BEIJING))

        assert before.month_pillar.gan_zhi == after.month_pillar.gan_zhi, "大寒不换月柱"
        assert before.nearby_solar_term is None
        assert after.nearby_solar_term is None

    def test_far_from_boundary_not_flagged(self):
        chart = compute_bazi(make_profile(datetime(2024, 6, 15, 12, 0), BEIJING))
        assert chart.nearby_solar_term is None

    def test_month_pillar_matches_boundary(self):
        """月柱确实在节上交换 —— 与告警所指的节自洽。"""
        before = compute_bazi(make_profile(datetime(2024, 2, 4, 16, 0), BEIJING))
        after = compute_bazi(make_profile(datetime(2024, 2, 4, 16, 30), BEIJING))
        assert before.month_pillar.gan_zhi != after.month_pillar.gan_zhi


# ---------------------------------------------------------------------------
# 历法
# ---------------------------------------------------------------------------


class TestCalendar:
    def test_lunar_new_year_2024(self):
        """农历 2024 **正月初一** = 公历 2024-02-10。

        注意月份是 1 不是 2 —— 春节是正月初一。写成 (2, 10) 会变成农历二月初十，
        静默滑到 2024-03-19。
        """
        chart = compute_bazi(make_profile(datetime(2024, 1, 1, 12, 0), BEIJING, calendar=Calendar.LUNAR))
        assert chart.true_solar_time.date() == datetime(2024, 2, 10).date()

    def test_lunar_month_start(self):
        """农历 2024 二月初一 = 公历 2024-03-10。"""
        chart = compute_bazi(make_profile(datetime(2024, 2, 1, 12, 0), BEIJING, calendar=Calendar.LUNAR))
        assert chart.true_solar_time.date() == datetime(2024, 3, 10).date()

    def test_leap_month_differs_from_regular_month(self):
        """闰月必须显式声明。若被当作普通月份，会静默解析成完全错误的日期。"""
        common = make_profile(datetime(2023, 2, 1, 12, 0), BEIJING, calendar=Calendar.LUNAR, is_leap_month=False)
        leap = make_profile(datetime(2023, 2, 1, 12, 0), BEIJING, calendar=Calendar.LUNAR, is_leap_month=True)

        common_date = compute_bazi(common).true_solar_time.date()
        leap_date = compute_bazi(leap).true_solar_time.date()

        # 2023 二月初一 = 02-20；闰二月初一 = 03-22
        assert common_date == datetime(2023, 2, 20).date()
        assert leap_date == datetime(2023, 3, 22).date()
        assert leap_date > common_date, "闰月在同名月份之后"

    def test_leap_month_pillar_differs(self):
        """闰月与普通月必须排出不同的月柱 —— 否则用户拿到的是错盘。"""
        common = compute_bazi(make_profile(datetime(2023, 2, 1, 12, 0), BEIJING, calendar=Calendar.LUNAR))
        leap = compute_bazi(
            make_profile(datetime(2023, 2, 1, 12, 0), BEIJING, calendar=Calendar.LUNAR, is_leap_month=True)
        )
        assert common.month_pillar.gan_zhi != leap.month_pillar.gan_zhi

    def test_nonexistent_leap_month_rejected(self):
        """闰月不存在时必须报错，不得静默按普通月处理。

        2023 年闰二月，没有闰三月。
        """
        with pytest.raises(InvalidLunarDateError, match="闰月"):
            compute_bazi(
                make_profile(datetime(2023, 3, 1, 12, 0), BEIJING, calendar=Calendar.LUNAR, is_leap_month=True)
            )

    def test_lunar_day_31_not_silently_accepted(self):
        """农历单月只有 29 或 30 天 —— 非法日期必须报错而非回绕。"""
        with pytest.raises(InvalidLunarDateError):
            compute_bazi(make_profile(datetime(2024, 1, 31, 12, 0), BEIJING, calendar=Calendar.LUNAR))

    def test_error_is_not_bare_exception(self):
        """收窄异常类型 —— 上层要据此区分"用户输入有误"与"引擎崩溃"。"""
        assert issubclass(InvalidLunarDateError, ValueError)

    def test_solar_input_default(self):
        """公历输入不做历法转换 —— 日期部分原样保留。"""
        chart = compute_bazi(make_profile(datetime(2024, 6, 15, 12, 0), BEIJING))
        assert chart.clock_time == datetime(2024, 6, 15, 12, 0)
        assert chart.true_solar_time.date() == datetime(2024, 6, 15).date()


# ---------------------------------------------------------------------------
# 精度降级 —— 专业度的直接体现
# ---------------------------------------------------------------------------


class TestTimeAccuracy:
    def test_unknown_time_drops_hour_pillar(self):
        """时辰未知时**不得**产出时柱，否则等于编造。"""
        chart = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING, accuracy=TimeAccuracy.UNKNOWN))
        assert chart.time_pillar is None
        assert len(chart.pillars) == 3
        assert any("时辰未知" in w for w in chart.warnings)

    def test_unknown_time_keeps_three_pillars(self):
        """时辰未知不影响年、月、日三柱 —— 降级而非放弃。"""
        exact = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING))
        unknown = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING, accuracy=TimeAccuracy.UNKNOWN))
        assert unknown.year_pillar == exact.year_pillar
        assert unknown.month_pillar == exact.month_pillar
        assert unknown.day_pillar == exact.day_pillar

    def test_unknown_time_reports_reported_clock(self):
        """``clock_time`` 必须是用户报出的时间，不能是内部占位值。"""
        chart = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING, accuracy=TimeAccuracy.UNKNOWN))
        assert chart.clock_time == datetime(1990, 6, 15, 10, 30)

    def test_hour_known_full_chart(self):
        chart = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING, accuracy=TimeAccuracy.HOUR_KNOWN))
        assert chart.time_pillar is not None
        assert len(chart.pillars) == 4


# ---------------------------------------------------------------------------
# 排盘完整性
# ---------------------------------------------------------------------------


class TestChartCompleteness:
    def test_known_anchor(self):
        """1990-06-15 10:30 男 北京 → 庚午 壬午 辛亥 癸巳。

        M0 已用五虎遁（庚年正月起戊寅 → 五月壬午）与
        五鼠遁（辛日子时起戊子 → 巳时癸巳）手工核对无误。
        """
        chart = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING))
        assert chart.four_pillars == "庚午 壬午 辛亥 癸巳"
        assert chart.day_master == "辛"

    def test_ten_gods_and_hidden_stems(self):
        chart = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING))
        assert chart.day_pillar.stem_ten_god == "日主"
        assert chart.year_pillar.stem_ten_god == "劫财"
        assert chart.year_pillar.hidden_stems == ["丁", "己"]
        assert chart.year_pillar.na_yin == "路旁土"
        assert chart.year_pillar.di_shi == "病"

    def test_extended_palaces(self):
        chart = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING))
        assert chart.tai_yuan is not None
        assert chart.ming_gong is not None
        assert chart.shen_gong is not None
        assert chart.tai_xi is not None

    def test_da_yun_sequence(self):
        """大运顺逆与干支序列 —— 阳年男顺排。"""
        chart = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING))
        assert chart.qi_yun_desc is not None
        assert [d.gan_zhi for d in chart.da_yun[:5]] == ["癸未", "甲申", "乙酉", "丙戌", "丁亥"]
        assert chart.da_yun[0].start_year > 1990

    def test_female_da_yun_differs(self):
        """性别决定大运顺逆 —— 男女不可同盘。"""
        male = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING, gender=Gender.MALE))
        female = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING, gender=Gender.FEMALE))
        assert [d.gan_zhi for d in male.da_yun[:3]] != [d.gan_zhi for d in female.da_yun[:3]]

    def test_reproducible(self):
        """同一输入必得同一命盘 —— 引擎不得含任何非确定性。"""
        profile = make_profile(datetime(1990, 6, 15, 10, 30), BEIJING)
        assert compute_bazi(profile).model_dump() == compute_bazi(profile).model_dump()

    def test_output_validates_as_contract(self):
        """引擎输出必须能通过 Pydantic 契约校验，才能进入 LLM 上下文。"""
        chart = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING))
        assert type(chart).model_validate(chart.model_dump()) == chart

    def test_engine_version_recorded(self):
        """``engine_version`` 用于命盘缓存失效判断 —— 必须存在。"""
        chart = compute_bazi(make_profile(datetime(1990, 6, 15, 10, 30), BEIJING))
        assert chart.engine_version == "m1"


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------


class TestProfileValidation:
    def test_blank_place_rejected(self):
        with pytest.raises(ValueError, match="出生地"):
            BirthProfile(
                gender=Gender.MALE,
                birth_datetime=datetime(1990, 6, 15, 10, 30),
                place="   ",
                latitude=39.9,
                longitude=116.4,
            )

    @pytest.mark.parametrize(("lat", "lng"), [(91.0, 116.4), (39.9, 181.0)])
    def test_out_of_range_coordinates_rejected(self, lat, lng):
        with pytest.raises(ValueError):
            BirthProfile(
                gender=Gender.MALE,
                birth_datetime=datetime(1990, 6, 15, 10, 30),
                place="测试",
                latitude=lat,
                longitude=lng,
            )
