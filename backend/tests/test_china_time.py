"""``engines/china_time.py`` —— 时区偏移的唯一真源。

这个模块存在的理由是"两条链路必须得到同一个瞬间"，所以这里的用例不只是
在测一个查表函数，而是在**钉住那个一致性**。样本刻意包含 1900/1919/1940 ——
现成时区库（tzdata）在这三年给出的答案与本模块**不同**（+8:06 / +9 / +9），
若有人把实现换回 ``Asia/Shanghai``，这些用例会红。

只挑 1986–1991 或 1949 后的年份测，会得到一个恒真的用例 —— 因为那些年份
两条路径恰好一致。**一个只在双方一致处采样的不变量测试，等于没有测。**
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from metaphys.engines.bazi.truesolar import to_true_solar_time
from metaphys.engines.china_time import (
    CHINA_STANDARD_MERIDIAN,
    NonexistentLocalTimeError,
    in_dst_fold,
    is_dst_active,
    to_utc,
    tz_str_for,
    utc_offset_hours,
)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        # --- 1949 后：与 tzdata 一致 ---------------------------------------
        (datetime(1949, 7, 1, 10), 8),
        (datetime(1986, 7, 1, 10), 9),
        (datetime(1988, 6, 1, 2, 30), 9),
        (datetime(1990, 6, 15, 10, 30), 9),
        (datetime(1992, 6, 1, 2, 30), 8),
        (datetime(2024, 2, 4, 17, 30), 8),
        # --- 1949 前：与 tzdata **不一致**，这里必须有样本 ------------------
        # tzdata 说 +8:06（LMT）/ +9 / +9；本模块一律 +8。
        (datetime(1900, 7, 1, 10), 8),
        (datetime(1919, 7, 1, 10), 8),
        (datetime(1930, 5, 20, 10), 8),
        (datetime(1940, 7, 1, 10), 8),
    ],
    ids=lambda value: value.strftime("%Y-%m-%d") if isinstance(value, datetime) else str(value),
)
def test_offset_table(moment: datetime, expected: int) -> None:
    """钟表时刻 → 偏移。含 1949 前三年，防止实现被换回 tzdata 而无人察觉。"""
    assert utc_offset_hours(moment) == expected


@pytest.mark.parametrize(
    "moment",
    [datetime(1986, 5, 4, 2, 30), datetime(1990, 4, 15, 2, 30), datetime(1991, 4, 14, 2, 59)],
)
def test_the_dst_gap_has_no_real_instant(moment: datetime) -> None:
    """缺口内的一小时在现实中不存在，必须抛错而不是猜一个值。"""
    with pytest.raises(NonexistentLocalTimeError):
        utc_offset_hours(moment)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(1990, 4, 15, 1, 59), 8),  # 缺口前一刻
        (datetime(1990, 4, 15, 3, 0), 9),  # 缺口结束后，钟表已跳到 03:00
        (datetime(1990, 9, 16, 2, 0), 8),  # 夏令时结束的那一刻
    ],
)
def test_the_dst_gap_edges_are_exact(moment: datetime, expected: int) -> None:
    """缺口边界是闭开区间 —— 差一分钟就换一种答案，不能靠"大概"。"""
    assert utc_offset_hours(moment) == expected


def test_the_fold_hour_resolves_to_daylight_time() -> None:
    """夏令时结束的重复小时按**夏令时**（UTC+9）解释。

    这一小时在钟表上出现两次（先 UTC+9 后 UTC+8），仅凭钟表时间无法区分。
    本模块固定选 9 —— 因为它整个落在 DST 区间内。选哪个都能自圆其说，但
    必须与 ``utc_offset_hours`` 一致，否则同一时刻在两条链路上差一小时。

    注意 tzdata 在这里是**抛错**的，本模块是取值 —— 差别不是"谁更对"，
    而是"出错时是崩掉还是带着告警继续"。
    """
    fold = datetime(1990, 9, 16, 1, 30)
    assert in_dst_fold(fold)
    assert utc_offset_hours(fold) == 9


def test_the_fold_ambiguity_is_actually_reported() -> None:
    """**回归**：歧义必须真的被告警，而不是躺在一个够不到的分支里。

    折叠小时同时也是"夏令时生效中"，所以 ``if is_dst_active ... elif in_dst_fold``
    的写法会让折叠分支**永远不执行** —— 曾经如此：一个钟表上出现两次的时刻被
    静默按夏令时解释，``notes`` 里连一句话都没有，而 docstring 还写着"已按
    标准时解释"。判据存在、文档写着、代码却不走那条路，是最难发现的一类失效。
    """
    result = to_true_solar_time(datetime(1990, 9, 16, 1, 30), 116.4)
    assert any("重复区间" in note for note in result.notes), (
        f"折叠小时的歧义未被报告，notes={result.notes!r} —— 该时刻在钟表上出现两次，"
        "静默取一个值会让时柱错一个时辰而用户无从察觉"
    )


def test_local_time_and_the_offset_agree_on_the_utc_instant() -> None:
    """本地时刻 − 偏移 == UTC，且偏移自洽。"""
    moment = datetime(1990, 6, 15, 10, 30)
    utc = to_utc(moment)
    assert utc == datetime(1990, 6, 15, 1, 30)
    assert moment - utc == timedelta(hours=utc_offset_hours(moment))


@pytest.mark.parametrize(
    ("moment", "expected"),
    [(datetime(1990, 6, 15, 10, 30), "Etc/GMT-9"), (datetime(2024, 2, 4, 17, 30), "Etc/GMT-8")],
)
def test_tz_str_is_posix_inverted(moment: datetime, expected: str) -> None:
    """``Etc/GMT-9`` 才是 UTC+9 —— POSIX 的符号是反的。

    写反了不会报错，只会让每一张盘静默偏 16 小时。所以这里不止比字符串，
    还把名字交回 ``zoneinfo`` 解析一遍，断言它真的等于我们算出的偏移。
    """
    name = tz_str_for(moment)
    assert name == expected
    assert datetime(2000, 1, 1, tzinfo=ZoneInfo(name)).utcoffset() == timedelta(hours=utc_offset_hours(moment))


def test_truesolar_and_china_time_cannot_drift() -> None:
    """``truesolar`` 只是再导出 —— 两个入口对同一时刻必须给出同一偏移。

    抽模块的全部意义在于"只有一个实现"。若哪天有人在 ``truesolar`` 里又写了
    一份偏移表，这条会红。
    """
    for moment in (datetime(1990, 6, 15, 10, 30), datetime(2024, 2, 4, 17, 30), datetime(1919, 7, 1, 10)):
        assert to_true_solar_time(moment, CHINA_STANDARD_MERIDIAN).utc_offset_hours == utc_offset_hours(moment)


def test_the_meridian_constant_is_the_one_truesolar_uses() -> None:
    """基准经线 120°E，经度时差按每度 4 分钟。"""
    result = to_true_solar_time(datetime(1990, 6, 15, 10, 30), 121.0)
    assert result.longitude_minutes == pytest.approx(4.0)
    assert is_dst_active(datetime(1990, 6, 15, 10, 30))
