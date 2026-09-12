"""H2 spike —— 真太阳时校正。

验证目标：出生地经度与均时差对时柱判定的影响，并确认中国历史时区变更
（1986-1991 夏令时）被正确处理。

M1 阶段本模块迁移为 engines/bazi/truesolar.py。

公式：
    地方平太阳时 = UTC + 经度/15 小时
    真太阳时     = 地方平太阳时 + 均时差

其中 UTC 由当地钟表时间减去该时刻的实际时区偏移得到 —— 这一步自动吸收了
夏令时，因此夏令时年份无需特殊分支。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import swisseph as swe

# 中国标准时间基准经线
CHINA_STANDARD_MERIDIAN = 120.0

# 中国夏令时区间（1986-1991）。DST 期间钟表比标准时快 1 小时。
# 起止均为当地时间，起始 02:00 跳至 03:00，结束 02:00 跳回 01:00。
_DST_RANGES: tuple[tuple[datetime, datetime], ...] = (
    (datetime(1986, 5, 4, 2), datetime(1986, 9, 14, 2)),
    (datetime(1987, 4, 12, 2), datetime(1987, 9, 13, 2)),
    (datetime(1988, 4, 10, 2), datetime(1988, 9, 11, 2)),
    (datetime(1989, 4, 16, 2), datetime(1989, 9, 17, 2)),
    (datetime(1990, 4, 15, 2), datetime(1990, 9, 16, 2)),
    (datetime(1991, 4, 14, 2), datetime(1991, 9, 15, 2)),
)


@dataclass(frozen=True)
class TrueSolarTimeResult:
    """真太阳时校正结果。"""

    clock_time: datetime  # 输入的钟表时间（用户报出的时间）
    true_solar: datetime  # 校正后的真太阳时
    utc_offset_hours: int  # 该时刻实际时区偏移（8 或 9）
    longitude_minutes: float  # 经度时差（分钟，东经为正）
    equation_of_time_minutes: float
    day_rolled: bool  # 是否跨越自然日 —— 影响日柱
    notes: list[str] = field(default_factory=list)

    @property
    def total_offset_minutes(self) -> float:
        return self.longitude_minutes + self.equation_of_time_minutes


def _dst_active(clock_time: datetime) -> bool:
    """判断钟表时间是否落在中国夏令时区间内。"""
    naive = clock_time.replace(tzinfo=None)
    return any(start <= naive < end for start, end in _DST_RANGES)


def utc_offset_hours(clock_time: datetime) -> int:
    """返回该钟表时刻中国实际使用的 UTC 偏移。

    1949 年前中国分五个时区，本模块不做推断 —— 由调用方保证输入已是标准时。
    """
    return 9 if _dst_active(clock_time) else 8


def equation_of_time_minutes(utc: datetime) -> float:
    """均时差（分钟）。真太阳时 = 平太阳时 + 均时差。"""
    jd = swe.julday(utc.year, utc.month, utc.day, utc.hour + utc.minute / 60 + utc.second / 3600)
    return swe.time_equ(jd) * 24 * 60


def to_true_solar_time(clock_time: datetime, longitude: float) -> TrueSolarTimeResult:
    """把钟表时间换算为出生地的真太阳时。

    Args:
        clock_time: 用户报出的出生时间（中国钟表时间）
        longitude: 出生地经度，东经为正
    """
    notes: list[str] = []

    offset = utc_offset_hours(clock_time)
    if offset == 9:
        notes.append("该时刻处于中国夏令时(1986-1991)，钟表快于标准时 1 小时，已校正")

    utc = clock_time - timedelta(hours=offset)
    mean_local = utc + timedelta(hours=longitude / 15.0)
    eot = equation_of_time_minutes(utc)
    true_solar = mean_local + timedelta(minutes=eot)

    # 经度时差相对 120° 基准线 —— 仅用于展示
    longitude_minutes = (longitude - CHINA_STANDARD_MERIDIAN) * 4.0

    day_rolled = true_solar.date() != clock_time.date()
    if day_rolled:
        notes.append(f"真太阳时跨日：{clock_time.date()} → {true_solar.date()}，日柱按真太阳时日期推算")

    return TrueSolarTimeResult(
        clock_time=clock_time,
        true_solar=true_solar,
        utc_offset_hours=offset,
        longitude_minutes=longitude_minutes,
        equation_of_time_minutes=eot,
        day_rolled=day_rolled,
        notes=notes,
    )


# --------------------------------------------------------------------------
# 验证
# --------------------------------------------------------------------------


def _shichen(hour: int) -> str:
    """时辰名。子时跨日，此处简化为 23-1 归子。"""
    names = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
    return names[((hour + 1) // 2) % 12]


def _main() -> int:
    from lunar_python import Solar

    print("=" * 78)
    print("H2 真太阳时校正验证")
    print("=" * 78)

    # --- 均时差与天文常识比对 ---
    print("\n[1] 均时差极值检验（应与天文年历一致）")
    checks = [
        (datetime(2024, 11, 3, 12), +16.4, "11月初 极大"),
        (datetime(2024, 2, 11, 12), -14.2, "2月中 极小"),
        (datetime(2024, 5, 14, 12), +3.7, "5月中"),
    ]
    ok = True
    for dt, expect, label in checks:
        got = equation_of_time_minutes(dt)
        good = abs(got - expect) < 0.5
        ok &= good
        print(f"    {label:<12} 期望≈{expect:+.1f}min  实得{got:+.2f}min  {'✓' if good else '✗'}")

    # --- 真太阳时对时柱的影响（本 spike 的核心命题）---
    print("\n[2] 真太阳时对时柱的影响（同一钟表时间，不同出生地）")
    print(
        f"    {'出生地':<10}{'经度':<9}{'钟表时':<9}{'真太阳时':<11}{'钟表时辰':<10}{'真太阳时辰':<11}{'时柱是否改变'}"
    )
    cases = [
        ("北京", 116.40),
        ("上海", 121.47),
        ("乌鲁木齐", 87.62),
        ("拉萨", 91.14),
    ]
    clock = datetime(1990, 6, 15, 10, 30)
    changed_any = False
    for city, lon in cases:
        r = to_true_solar_time(clock, lon)
        sc_clock = _shichen(clock.hour)
        sc_true = _shichen(r.true_solar.hour)
        changed = sc_clock != sc_true
        changed_any |= changed
        print(
            f"    {city:<10}{lon:<9.2f}{clock.strftime('%H:%M'):<9}"
            f"{r.true_solar.strftime('%H:%M:%S'):<11}{sc_clock + '时':<10}{sc_true + '时':<11}"
            f"{'★ 改变' if changed else '不变'}"
        )

    # 用 lunar-python 实证时柱差异
    print("\n[3] 实证时柱差异（lunar-python 排盘）")
    for city, lon in [("北京", 116.40), ("乌鲁木齐", 87.62)]:
        r = to_true_solar_time(clock, lon)
        t1 = (
            Solar.fromYmdHms(clock.year, clock.month, clock.day, clock.hour, clock.minute, 0)
            .getLunar()
            .getEightChar()
            .getTime()
        )
        ts = r.true_solar
        t2 = (
            Solar.fromYmdHms(ts.year, ts.month, ts.day, ts.hour, ts.minute, ts.second)
            .getLunar()
            .getEightChar()
            .getTime()
        )
        mark = "★ 不同" if t1 != t2 else "相同"
        print(f"    {city:<8} 钟表时柱 {t1}   真太阳时时柱 {t2}   {mark}")

    # --- 夏令时 ---
    print("\n[4] 中国夏令时(1986-1991)处理")
    dst_cases = [
        (datetime(1988, 6, 1, 2, 30), "夏令时期间"),
        (datetime(1988, 11, 1, 2, 30), "非夏令时"),
        (datetime(1992, 6, 1, 2, 30), "夏令时取消后"),
    ]
    for dt, label in dst_cases:
        r = to_true_solar_time(dt, 116.40)
        print(
            f"    {dt}  {label:<14} UTC+{r.utc_offset_hours}  "
            f"真太阳时 {r.true_solar.strftime('%H:%M:%S')}  {('| ' + r.notes[0]) if r.notes else ''}"
        )

    # --- 跨日 ---
    print("\n[5] 跨日情形（影响日柱）")
    for dt, lon, label in [
        (datetime(1990, 6, 15, 0, 20), 87.62, "乌鲁木齐 凌晨"),
        (datetime(1990, 6, 15, 23, 50), 121.47, "上海 深夜"),
    ]:
        r = to_true_solar_time(dt, lon)
        print(
            f"    {label:<14} 钟表 {dt:%Y-%m-%d %H:%M} → 真太阳时 "
            f"{r.true_solar:%Y-%m-%d %H:%M:%S}   跨日={r.day_rolled}"
        )

    print("\n" + "=" * 78)
    print(f"结论：均时差检验 {'通过' if ok else '未通过'}；真太阳时{'确实' if changed_any else '未'}改变时柱判定")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
