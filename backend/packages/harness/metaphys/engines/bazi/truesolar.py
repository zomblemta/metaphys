"""真太阳时校正 —— 八字排盘的第一道关口。

中国统一使用"北京时间"（120°E 基准），但出生地的实际太阳时由**当地经度**决定。
经度每差 1° 即差 4 分钟。跨时辰边界时，这个差值直接决定时柱正确与否 ——
且错误是静默的，用户无从察觉。因此本模块是排盘链路中不可跳过的一环。

公式::

    地方平太阳时 = UTC + 经度/15 小时
    真太阳时     = 地方平太阳时 + 均时差

其中 UTC 由钟表时间减去该时刻**实际生效的**时区偏移得到。这一步自动吸收了
夏令时，因此 1986–1991 夏令时年份无需在排盘主流程里写任何特殊分支。

均时差用 ``swe.time_equ``（Swiss Ephemeris）计算，优于常见的自写近似公式，
且 pyswisseph 本就是星盘侧的依赖，无额外成本。

本模块为纯函数，零 LLM 依赖，可脱离 agent 独立测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import swisseph as swe

# 钟表时间 → UTC 的换算（含夏令时表）已抽到 engines/china_time.py：星盘引擎
# 需要**同一份**换算才能保证两张盘对应同一瞬间。此处再导出，保持既有引用不变。
from metaphys.engines.china_time import (
    CHINA_STANDARD_MERIDIAN,
    NonexistentLocalTimeError,
    in_dst_fold,
    is_dst_active,
    utc_offset_hours,
)


@dataclass(frozen=True)
class TrueSolarTimeResult:
    """真太阳时校正结果。"""

    clock_time: datetime  # 输入的钟表时间（用户报出的时间）
    true_solar: datetime  # 校正后的真太阳时
    utc_offset_hours: int  # 该时刻实际生效的时区偏移（8 或 9）
    longitude_minutes: float  # 经度时差（分钟，相对 120°E，东经为正）
    equation_of_time_minutes: float  # 均时差（分钟）
    day_rolled: bool  # 真太阳时是否跨自然日 —— 影响日柱
    notes: list[str] = field(default_factory=list)

    @property
    def total_offset_minutes(self) -> float:
        """相对钟表时间的总偏移量（分钟）。"""
        return self.longitude_minutes + self.equation_of_time_minutes


def equation_of_time_minutes(utc: datetime) -> float:
    """均时差（分钟）。真太阳时 = 平太阳时 + 均时差。

    年变化幅度约 ±16 分钟，是除经度外影响时柱判定的第二个因素。
    """
    jd = swe.julday(utc.year, utc.month, utc.day, utc.hour + utc.minute / 60 + utc.second / 3600)
    return swe.time_equ(jd) * 24 * 60


def to_true_solar_time(clock_time: datetime, longitude: float) -> TrueSolarTimeResult:
    """把钟表时间换算为出生地的真太阳时。

    Args:
        clock_time: 用户报出的出生时间（中国钟表时间，naive）
        longitude: 出生地经度，东经为正

    Raises:
        NonexistentLocalTimeError: 钟表时间落在夏令时缺口内。
    """
    notes: list[str] = []

    offset = utc_offset_hours(clock_time)
    # **先判重复区间**：折叠小时同时也是"夏令时生效中"（该区间是 DST 区间的末尾
    # 一小时），所以若先判 is_dst_active，这里的分支永远轮不到 —— 曾经如此，
    # 结果是一个钟表上出现两次的时刻被静默按夏令时解释、连告警都没有。
    if in_dst_fold(clock_time):
        notes.append(
            "该时刻处于夏令时结束的重复区间（01:00–02:00 出现两次），已按夏令时(UTC+9)解释；"
            "若实际采用的是标准时，时柱可能相差一个时辰，请与用户确认"
        )
    elif is_dst_active(clock_time):
        notes.append("该时刻处于中国夏令时(1986-1991)，钟表快于标准时 1 小时，已校正")

    utc = clock_time - timedelta(hours=offset)
    mean_local = utc + timedelta(hours=longitude / 15.0)
    eot = equation_of_time_minutes(utc)
    true_solar = mean_local + timedelta(minutes=eot)

    # 经度时差相对 120° 基准线 —— 仅用于展示，不参与计算
    longitude_minutes = (longitude - CHINA_STANDARD_MERIDIAN) * 4.0

    day_rolled = true_solar.date() != clock_time.date()
    if day_rolled:
        notes.append(f"真太阳时跨日：{clock_time.date()} → {true_solar.date()}，日柱须按真太阳时日期推算")

    return TrueSolarTimeResult(
        clock_time=clock_time,
        true_solar=true_solar,
        utc_offset_hours=offset,
        longitude_minutes=longitude_minutes,
        equation_of_time_minutes=eot,
        day_rolled=day_rolled,
        notes=notes,
    )


# ``utc_offset_hours`` 与 ``NonexistentLocalTimeError`` 的真源已移到
# ``engines/china_time.py``，这里保留在 ``__all__`` 里是**刻意的再导出** ——
# 既有调用方（bazi_chart 工具、测试）从本模块引用它们，不该因为一次重构而改。
__all__ = [
    "CHINA_STANDARD_MERIDIAN",
    "NonexistentLocalTimeError",
    "TrueSolarTimeResult",
    "equation_of_time_minutes",
    "to_true_solar_time",
    "utc_offset_hours",
]
