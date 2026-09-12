"""中国钟表时间 → UTC 的**唯一**换算口。

八字引擎要先知道"这一小时的钟表时间对应哪个 UTC 瞬间"才能算真太阳时与节气；
星盘引擎要先知道同一个瞬间才能算上升点。两套体系必须得到**同一个**答案，
否则同一份出生信息会排出两张互相对不上的盘 —— 而且不会有任何东西报错。

**为什么不能各自用现成的时区库。** 直觉做法是给星盘传 ``tz_str="Asia/Shanghai"``，
让 tzdata 去查历史。实测（本机 tzdata）：

=======================  ==========================  ==========
出生时刻                  pytz 查到的偏移               本模块
=======================  ==========================  ==========
1900-07-01               **+8:06**（LMT，不是整点）      +8
1919-07-01               **+9**（中国当时有夏令时）       +8
1940-07-01               **+9**                        +8
1930-05-20               +8                            +8
1949-07-01 起             +8                            +8
1986–1991 夏令时内         +9                            +9
1990-09-16 01:30（折叠）   **抛 AmbiguousTimeError**      +9（按夏令时）
=======================  ==========================  ==========

两处分歧都会静默错盘：

* **1949 年前**：偏移差 1 小时（1900 年差 6 分钟）。对星盘而言上升点约
  4 分钟走 1°，1 小时就是 15° —— 足以让上升星座与整个宫位错开一宫。
* **折叠小时**：tzdata 直接抛错，而八字引擎会照常出一张盘。同一个时间戳，
  一条链路能用、另一条崩，这本身就是不一致。

本模块的选择是**固定 8/9 两档、不查 tzdata**，与八字引擎完全一致。这不是
"更正确"，而是"两边一致且可预期"；1949 年前中国实际分五个时区，本模块
不做推断（见 :func:`utc_offset_hours` 的 Raises 段）。

纯函数，零 LLM、零历法库依赖 —— 两个引擎都要用它，它不能反过来依赖任何一个。
"""

from __future__ import annotations

from datetime import datetime, timedelta

#: 中国标准时间基准经线。
CHINA_STANDARD_MERIDIAN = 120.0

#: 标准时偏移（小时）。中国自 1949 年起统一使用 UTC+8。
CHINA_STANDARD_OFFSET_HOURS = 8

#: 中国夏令时区间（1986–1991）。DST 期间钟表比标准时快 1 小时。
#: 起止均为**当地时间**：起始日 02:00 跳至 03:00（02:00–03:00 不存在），
#: 结束日 02:00 跳回 01:00（01:00–02:00 出现两次）。
_DST_RANGES: tuple[tuple[datetime, datetime], ...] = (
    (datetime(1986, 5, 4, 2), datetime(1986, 9, 14, 2)),
    (datetime(1987, 4, 12, 2), datetime(1987, 9, 13, 2)),
    (datetime(1988, 4, 10, 2), datetime(1988, 9, 11, 2)),
    (datetime(1989, 4, 16, 2), datetime(1989, 9, 17, 2)),
    (datetime(1990, 4, 15, 2), datetime(1990, 9, 16, 2)),
    (datetime(1991, 4, 14, 2), datetime(1991, 9, 15, 2)),
)


class NonexistentLocalTimeError(ValueError):
    """钟表时间落在夏令时"弹簧缺口"内 —— 该时刻在现实中不存在。

    夏令时起始日 02:00 直接跳到 03:00，因此 02:00–03:00 这一小时没有对应
    的真实时刻。用户若报出这个区间的时间，说明记忆有误（或报的是夏令时
    时间但记错了日期）。此时**不能**猜一个值继续排盘 —— 静默产出错误命盘
    正是本项目要杜绝的，故抛出异常交由上层追问。
    """


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None)


def is_dst_active(clock_time: datetime) -> bool:
    """钟表时间是否落在中国夏令时区间内。"""
    naive = _naive(clock_time)
    return any(start <= naive < end for start, end in _DST_RANGES)


def in_dst_gap(clock_time: datetime) -> bool:
    """是否落在夏令时起始的"不存在的一小时"内（起始日 [02:00, 03:00)）。"""
    naive = _naive(clock_time)
    return any(start <= naive < start + timedelta(hours=1) for start, _ in _DST_RANGES)


def in_dst_fold(clock_time: datetime) -> bool:
    """是否落在夏令时结束的"重复的一小时"内（结束日 [01:00, 02:00)）。

    这一小时在钟表上出现两次：先一次是夏令时（UTC+9），后一次是标准时（UTC+8）。
    本模块**无法从钟表时间区分二者**，:func:`utc_offset_hours` 一律返回 9
    （夏令时）—— 因为该区间整个落在 DST 区间之内，边界上先被 ``is_dst_active``
    捕获。

    所以这个判据的用途**不是**改变偏移，而是给调用方一个"此处存在歧义"的信号，
    让它去告警或追问。谁在这里按 8 计算，就会与 :func:`utc_offset_hours` 打架。
    """
    naive = _naive(clock_time)
    return any(end - timedelta(hours=1) <= naive < end for _, end in _DST_RANGES)


def utc_offset_hours(clock_time: datetime) -> int:
    """返回该钟表时刻中国实际使用的 UTC 偏移（8 或 9）。

    Raises:
        NonexistentLocalTimeError: 该时刻落在夏令时缺口内，现实中不存在。

    1949 年前中国分五个时区，本模块不做推断 —— 由调用方保证输入已是标准时。
    """
    if in_dst_gap(clock_time):
        raise NonexistentLocalTimeError(
            f"{clock_time:%Y-%m-%d %H:%M} 落在中国夏令时起始的跳变区间内"
            "（02:00 直接跳到 03:00），该钟表时间在现实中不存在，请确认出生时间"
        )
    return 9 if is_dst_active(clock_time) else CHINA_STANDARD_OFFSET_HOURS


def to_utc(clock_time: datetime) -> datetime:
    """钟表时间 → UTC。

    Raises:
        NonexistentLocalTimeError: 该时刻落在夏令时缺口内。
    """
    return _naive(clock_time) - timedelta(hours=utc_offset_hours(clock_time))


def tz_str_for(clock_time: datetime) -> str:
    """该钟表时刻对应的**固定偏移**时区名，供只有 ``tz_str`` 接口的库使用。

    返回 POSIX 风格 ``Etc/GMT±N``。**注意符号是反的** —— ``Etc/GMT-8`` 才是
    UTC+8，这是 POSIX 的历史包袱（为兼容旧版 ``posixrules``）。

    之所以要传偏移而不是先把时间换算成 UTC 再传 ``Etc/GMT``：星盘库会把
    ``tz_str`` 标注在图上、并据此渲染本地时刻，传 UTC 会让整张图的时刻标注
    偏 8 小时，而盘面本身是对的 —— 一种"看上去就不对、但算出来没错"的错法。

    Raises:
        NonexistentLocalTimeError: 该时刻落在夏令时缺口内。
    """
    offset = utc_offset_hours(clock_time)
    sign = "-" if offset >= 0 else "+"
    return f"Etc/GMT{sign}{abs(offset)}"


__all__ = [
    "CHINA_STANDARD_MERIDIAN",
    "CHINA_STANDARD_OFFSET_HOURS",
    "NonexistentLocalTimeError",
    "in_dst_fold",
    "in_dst_gap",
    "is_dst_active",
    "to_utc",
    "tz_str_for",
    "utc_offset_hours",
]
