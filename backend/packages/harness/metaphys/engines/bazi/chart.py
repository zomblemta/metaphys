"""八字排盘引擎。

**纯确定性计算，零 LLM 依赖。** 这是本项目架构不变量的物理实现：
命盘只能由这里产出，LLM 只消费不生产。

链路::

    BirthProfile → 公历时刻 ┬→ 绝对时刻（北京标准时） → 年柱 / 月柱 / 大运
                            └→ 出生地真太阳时         → 日柱 / 时柱

**两个时钟域，不可混用：**

* **年柱、月柱由节气决定**，而节气是太阳到达特定视黄经的**全球性瞬间**。
  同一瞬间出生的人，无论身处北京还是乌鲁木齐，年月二柱必须相同。
  故这一路取绝对时刻。
* **日柱、时柱由当地真太阳时决定** —— 这才是出生地经度真正起作用之处，
  也是本项目相对同类产品的核心差异。

若把整盘都按真太阳时推算，靠近节气出生者的月柱会随出生地经度漂移
（乌鲁木齐偏 2.16 小时），且用户无从察觉。反之若整盘都用绝对时刻，
西部城市的时柱又会从源头就错。两者都试过，故有此拆分。

顺序亦不可颠倒：**先归一到公历，再做真太阳时校正**。农历输入若先校正，
等于把农历数字当钟表时间用，会静默得到完全错误的日期。

贴节气 4 小时内出生者会被标记：报时误差叠加时区换算，足以跨越节气边界。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from lunar_python import Lunar, Solar

from metaphys.engines.bazi.ganzhi import (
    BRANCHES,
    STEMS,
    hidden_stems,
    hidden_ten_gods,
    ten_god_for_stem,
    twelve_stage,
)
from metaphys.engines.bazi.shensha import find_shensha
from metaphys.engines.bazi.strength import analyze_strength
from metaphys.engines.bazi.truesolar import NonexistentLocalTimeError, to_true_solar_time
from metaphys.schemas.chart import (
    BaziChart,
    BirthProfile,
    Calendar,
    DaYun,
    Gender,
    Pillar,
    School,
    SolarTermMark,
    TimeAccuracy,
)

ENGINE_VERSION = "m1"

# 距节气不足该分钟数即标记 —— 该区间内月柱可能因报时误差而误判
SOLAR_TERM_ALERT_MINUTES = 240.0

# 十二「节」—— 只有节换月柱，中气（雨水/春分/大寒…）不换。
# 键名需同时容纳中文与拼音两种形式：lunar-python 的节气表跨约 15 个月，
# 同名节气出现两次时，靠后的那个改用拼音大写键（如 2024 立春 = "立春"，
# 2025 立春 = "LI_CHUN"）。只按中文名查找会静默漏掉一半的节。
_JIE_NAMES: dict[str, str] = {
    "立春": "立春",
    "LI_CHUN": "立春",
    "惊蛰": "惊蛰",
    "JING_ZHE": "惊蛰",
    "清明": "清明",
    "QING_MING": "清明",
    "立夏": "立夏",
    "LI_XIA": "立夏",
    "芒种": "芒种",
    "MANG_ZHONG": "芒种",
    "小暑": "小暑",
    "XIAO_SHU": "小暑",
    "立秋": "立秋",
    "LI_QIU": "立秋",
    "白露": "白露",
    "BAI_LU": "白露",
    "寒露": "寒露",
    "HAN_LU": "寒露",
    "立冬": "立冬",
    "LI_DONG": "立冬",
    "大雪": "大雪",
    "DA_XUE": "大雪",
    "小寒": "小寒",
    "XIAO_HAN": "小寒",
}

# 时辰未知时的占位时刻。取正午，尽量远离子时换日边界与时辰边界，
# 使年、月、日三柱不受占位值影响。
_UNKNOWN_TIME_PLACEHOLDER_HOUR = 12


class InvalidLunarDateError(ValueError):
    """农历日期不存在（如闰月不成立、月份超出范围）。

    lunar-python 对此抛的是裸 ``Exception``，直接漏出去会让调用方无法区分
    "用户输入有误"与"引擎内部崩溃"。上层需要前者来触发追问，故在此收窄。
    """


class MissingGenderError(ValueError):
    """排八字需要性别，但出生信息里没有。

    ``BirthProfile.gender`` 是**可选**的（星盘用不上它，见该模型的 docstring），
    所以"缺失"这个状态是合法的输入、却是不合法的**八字**输入。

    必须抛出而不是取默认值：大运顺逆由性别决定，而 ``None is Gender.MALE``
    求值为假 —— 不报错的话男命会被静默排成女命，大运整个反向，用户无从察觉。
    这正是本项目要杜绝的失败模式，宁可让调用方去追问性别。
    """


def _civil_datetime(profile: BirthProfile) -> datetime:
    """把出生信息归一为公历时刻。

    农历输入借 lunar-python 的约定表达闰月：**月份取负数**即表示闰该月
    （如 -2 表示闰二月）。若不显式区分，闰月会被当作普通月份静默解析成
    错误日期，故必须由 ``is_leap_month`` 驱动。
    """
    dt = profile.birth_datetime
    if profile.calendar is not Calendar.LUNAR:
        return dt

    month = -dt.month if profile.is_leap_month else dt.month
    try:
        s = Lunar.fromYmdHms(dt.year, month, dt.day, dt.hour, dt.minute, dt.second).getSolar()
    except Exception as exc:
        kind = "闰月" if profile.is_leap_month else "日期"
        raise InvalidLunarDateError(
            f"农历 {dt.year} 年 {'闰' if profile.is_leap_month else ''}{dt.month} 月 {dt.day} 日不存在"
            f"（{kind}超出该年范围）"
        ) from exc
    return datetime(s.getYear(), s.getMonth(), s.getDay(), s.getHour(), s.getMinute(), s.getSecond())


def _build_pillar(ec, position: str, day_master: str) -> Pillar:
    """从 EightChar 抽取一柱的干支与纳音，十神与地势则按 ``day_master`` 重算。

    十神与十二长生都以**日主**为基准。年柱/月柱取自 ``ec`` 与日柱/时柱不同的
    时刻时，lunar-python 内部算出的十神是相对它自己那盘的日主，会张冠李戴，
    故一律用 ``ganzhi`` 表重算。``position`` 取 Year / Month / Day / Time。
    """
    stem = getattr(ec, f"get{position}Gan")()
    branch = getattr(ec, f"get{position}Zhi")()

    return Pillar(
        stem=stem,
        branch=branch,
        stem_ten_god=ten_god_for_stem(day_master, stem),
        hidden_stems=hidden_stems(branch),
        branch_ten_gods=hidden_ten_gods(day_master, branch),
        na_yin=getattr(ec, f"get{position}NaYin")(),
        di_shi=twelve_stage(day_master, branch),
    )


def _eight_char(moment: datetime):
    """由公历时刻构造 EightChar。

    晚子时（23:00–24:00）的日柱归属，流派有分歧。取 1 = 23:00 即换日，
    与"子时为一日之始"的主流命理约定一致。
    """
    ec = (
        Solar.fromYmdHms(moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second)
        .getLunar()
        .getEightChar()
    )
    ec.setSect(1)
    return ec


def _solar_term_mark(lunar, true_solar: datetime) -> SolarTermMark | None:
    """若出生时刻贴近换月柱的「节」，返回该节气标记。

    两个易错点，都已在 M1 用测试钉死：

    1. **必须看前后两侧。** 出生在交节**之前**时，换柱的是即将到来的那个节。
       只查 ``getPrevJieQi`` 恰好漏掉最需要预警的情形。
    2. **只有节换月柱，中气不换。** ``getPrevJieQi`` 会返回中气（如大寒），
       在它附近出生月柱并不会改变，据此告警即误报。
    """
    table = lunar.getJieQiTable()
    best: SolarTermMark | None = None

    for key, solar in table.items():
        name = _JIE_NAMES.get(key)
        if name is None:
            continue
        at = datetime(
            solar.getYear(),
            solar.getMonth(),
            solar.getDay(),
            solar.getHour(),
            solar.getMinute(),
            solar.getSecond(),
        )
        delta_minutes = (true_solar - at).total_seconds() / 60.0
        if best is None or abs(delta_minutes) < abs(best.minutes_away):
            best = SolarTermMark(name=name, at=at, minutes_away=round(delta_minutes, 1))

    if best is None or abs(best.minutes_away) > SOLAR_TERM_ALERT_MINUTES:
        return None
    return best


def _tai_yuan(month_pillar: Pillar) -> str:
    """胎元：月柱天干进一位、地支进三位。"""
    stem = STEMS[(STEMS.index(month_pillar.stem) + 1) % 10]
    branch = BRANCHES[(BRANCHES.index(month_pillar.branch) + 3) % 12]
    return f"{stem}{branch}"


def _build_da_yun(ec, profile: BirthProfile) -> tuple[str | None, list[DaYun]]:
    """排大运 —— 起运时间与每步大运的干支、年龄、年份区间。

    Raises:
        MissingGenderError: ``profile.gender`` 为空。守在这里而不是
            ``compute_bazi`` 开头，是因为**这里才是用到它的地方** ——
            在调用点判断，将来多一条通往大运的路径也不会绕过它。
    """
    if profile.gender is None:
        raise MissingGenderError("排八字需要性别（大运顺逆由性别决定），但出生信息中没有提供。请向用户确认性别。")
    # lunar-python 约定：1 = 男，0 = 女
    gender_flag = 1 if profile.gender is Gender.MALE else 0
    yun = ec.getYun(gender_flag)

    qi_yun_desc = (
        f"出生后 {yun.getStartYear()} 年 {yun.getStartMonth()} 个月 {yun.getStartDay()} 天起运，"
        f"即 {yun.getStartSolar().toYmd()} 交入大运"
    )

    da_yun: list[DaYun] = []
    for dy in yun.getDaYun():
        gan_zhi = dy.getGanZhi()
        if not gan_zhi:  # 起运前的那一段没有干支
            continue
        da_yun.append(
            DaYun(
                index=dy.getIndex(),
                gan_zhi=gan_zhi,
                start_age=dy.getStartAge(),
                start_year=dy.getStartYear(),
                end_year=dy.getEndYear(),
            )
        )
    return qi_yun_desc, da_yun


def compute_bazi(profile: BirthProfile, *, school: School = School.ZIPING) -> BaziChart:
    """排八字命盘。

    Args:
        profile: 出生信息（含出生地经纬度与时间精度）
        school: 命理流派，决定旺衰评分的权重取法

    Returns:
        经校验的 ``BaziChart``

    Raises:
        NonexistentLocalTimeError: 出生时间落在夏令时缺口内。
        InvalidLunarDateError: 农历日期不存在（闰月不成立、月份超范围）。
        MissingGenderError: ``profile.gender`` 为空 —— 大运顺逆需要它。
    """
    warnings: list[str] = []
    has_time = profile.time_accuracy is not TimeAccuracy.UNKNOWN

    # --- 1. 归一为公历时刻 -------------------------------------------------
    reported_clock = _civil_datetime(profile)
    civil = reported_clock

    # --- 2. 精度降级 -------------------------------------------------------
    if not has_time:
        warnings.append(
            f"出生时辰未知：时柱缺失，解读不得涉及时柱十神、子女宫与晚年运；"
            f"日柱以当日为准（占位 {_UNKNOWN_TIME_PLACEHOLDER_HOUR}:00 推算），"
            "若生于 23:00 后，日柱亦可能相差一日"
        )
        civil = civil.replace(hour=_UNKNOWN_TIME_PLACEHOLDER_HOUR, minute=0, second=0, microsecond=0)

    # --- 3. 真太阳时校正 ---------------------------------------------------
    tst = to_true_solar_time(civil, profile.longitude)
    warnings.extend(tst.notes)

    # --- 4. 两个时钟域 -----------------------------------------------------
    # 年柱、月柱由**节气**决定，而节气是全球性的天文时刻 —— 同一瞬间出生的人，
    # 无论身在何处，年月二柱必须相同。故取绝对时刻（换算回北京标准时）。
    # 日柱、时柱由**当地真太阳时**决定 —— 这才是出生地经度真正起作用的地方。
    # 二者若混用一个时钟，靠近节气出生者的月柱会随经度漂移，是静默错误。
    local_moment = tst.true_solar if has_time else civil
    beijing_moment = civil - timedelta(hours=tst.utc_offset_hours) + timedelta(hours=8)

    ec_astro = _eight_char(beijing_moment)  # 年、月 + 大运
    ec_local = _eight_char(local_moment)  # 日、时

    # --- 5. 四柱 -----------------------------------------------------------
    day_master = ec_local.getDayGan()
    year_pillar = _build_pillar(ec_astro, "Year", day_master)
    month_pillar = _build_pillar(ec_astro, "Month", day_master)
    day_pillar = _build_pillar(ec_local, "Day", day_master)
    time_pillar = _build_pillar(ec_local, "Time", day_master) if has_time else None

    # --- 6. 边界质量标记 ---------------------------------------------------
    if profile.time_accuracy is TimeAccuracy.HOUR_KNOWN:
        # 距时辰中点超过 30 分钟即视为贴近边界
        minutes_into_branch = (local_moment.hour % 2) * 60 + local_moment.minute
        if minutes_into_branch < 30 or minutes_into_branch > 90:
            warnings.append("出生时辰贴近时辰交界，真太阳时校正可能使时柱落在相邻时辰，建议核对准确出生时间")

    # 节气告警必须与判定月柱所用的时钟一致 —— 即绝对时刻，而非真太阳时。
    lunar_for_term = Solar.fromYmdHms(
        beijing_moment.year,
        beijing_moment.month,
        beijing_moment.day,
        beijing_moment.hour,
        beijing_moment.minute,
        beijing_moment.second,
    ).getLunar()
    nearby = _solar_term_mark(lunar_for_term, beijing_moment)
    if nearby is not None:
        warnings.append(
            f"出生时刻距节气「{nearby.name}」仅 {abs(nearby.minutes_away):.0f} 分钟，"
            "月柱处于交接点附近，报时误差可能导致月柱不同"
        )

    # 大运由月柱推得，起运时间取决于出生到节气的距离 —— 同属绝对时刻域。
    qi_yun_desc, da_yun = _build_da_yun(ec_astro, profile)

    # --- 7. 神煞与旺衰 -----------------------------------------------------
    # 时辰未知时不产出时柱，神煞与旺衰都只看已排出的柱 —— 宁缺勿造。
    named_pillars: list[tuple[str, Pillar]] = [("年", year_pillar), ("月", month_pillar), ("日", day_pillar)]
    if time_pillar is not None:
        named_pillars.append(("时", time_pillar))

    shen_sha = find_shensha(day_master, named_pillars)
    strength = analyze_strength(day_master, named_pillars, school)

    return BaziChart(
        profile=profile,
        clock_time=reported_clock,
        true_solar_time=local_moment,
        utc_offset_hours=tst.utc_offset_hours,
        equation_of_time_minutes=round(tst.equation_of_time_minutes, 3),
        day_rolled=tst.day_rolled,
        year_pillar=year_pillar,
        month_pillar=month_pillar,
        day_pillar=day_pillar,
        time_pillar=time_pillar,
        day_master=day_master,
        # 胎元由**月柱**推得（干进一位、支进三位），自算以保证与上面的月柱自洽；
        # 命宫/身宫/胎息依赖时辰，取自本地盘。
        tai_yuan=_tai_yuan(month_pillar),
        ming_gong=ec_local.getMingGong(),
        shen_gong=ec_local.getShenGong(),
        tai_xi=ec_local.getTaiXi(),
        qi_yun_desc=qi_yun_desc,
        da_yun=da_yun,
        school=school,
        shen_sha=shen_sha,
        strength=strength,
        warnings=warnings,
        nearby_solar_term=nearby,
        engine_version=ENGINE_VERSION,
    )


__all__ = [
    "ENGINE_VERSION",
    "InvalidLunarDateError",
    "MissingGenderError",
    "NonexistentLocalTimeError",
    "compute_bazi",
]
