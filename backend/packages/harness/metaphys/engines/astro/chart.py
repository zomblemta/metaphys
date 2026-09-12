"""星盘引擎 —— 纯确定性计算，零 LLM 依赖。

与八字引擎的分工完全对称：**盘只能由这里产出**，LLM 只消费。

链路::

    BirthProfile ─┬→ 钟表时间 → 固定偏移时区（engines.china_time）→ UTC 瞬间
                  └→ 出生地经纬度 ─────────────────────────────┐
                                                                 ↓
                                       AstrologicalSubjectFactory → 点/宫/相位
                                                                 ↓
                                        AstroChart（模型读的那一份）

**为什么时区不走 ``Asia/Shanghai``。** 那是"让时区库去查历史"的做法，而
本机 tzdata 对 1919/1940 报 UTC+9、对 1900 报 +8:06（LMT）、对夏令时结束的
重复小时直接抛 ``AmbiguousTimeError``。八字引擎不查 tzdata（它自己维护
1986–1991 的夏令时表），于是同一份出生信息在两条链路上会得到**不同的瞬间**
—— 星盘会平移最多 1 小时，即上升点偏约 15°、宫位错开一整宫，而且没有任何
东西会报错。所以两边都必须从 :mod:`metaphys.engines.china_time` 取偏移。

**为什么不采用 kerykeion 自带的 ``to_context()``。** 它输出的 AI Context XML
看起来正合用，但那样同一张盘就有了**两份序列化**：模型读到的是一份，我们
核验用的是另一份。两份一旦不同步，核验会把**正确**的内容报成编造 —— 一种
朝着"更严格"方向失效的错，比漏报更难查。前提（宫位制/黄道制）我们本来就写进
``AstroChart`` 自己的字段，不需要第二份。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from xml.sax.saxutils import escape

from kerykeion import AspectsFactory, AstrologicalSubjectFactory

from metaphys.engines.astro.points import (
    ACTIVE_ASPECTS,
    ACTIVE_POINTS,
    ASPECT_NAMES,
    HOUSE_NAMES,
    MAJOR_ASPECTS,
    PLANET_NAMES,
    SIGN_NAMES,
)
from metaphys.engines.china_time import (
    NonexistentLocalTimeError,
    in_dst_fold,
    tz_str_for,
    utc_offset_hours,
)
from metaphys.schemas.chart import (
    AspectRef,
    AstroChart,
    AstroPoint,
    BirthProfile,
    HouseCusp,
    HouseSystem,
    ZodiacType,
)

ENGINE_VERSION = "m3"

#: 地理库覆盖范围（中国大陆 3332 条）。超出即说明出生地不在数据集中，
#: 而此时"中国用 UTC+8/+9"这个前提本身就未必成立。
_GEO_LNG_RANGE = (73.0, 136.0)
_GEO_LAT_RANGE = (3.0, 54.0)

#: 1949 年前中国分五个时区，本引擎一律按标准时推算 —— 与八字引擎一致。
_PRE_1949_YEAR = 1949

#: 新疆等地民间作息偏用 UTC+6，与钟表显示的 UTC+8 有 2 小时出入。
#: 2 小时足以让上升点完全错位，故单列一条告警。
_WEST_LNG_THRESHOLD = 90.0

#: 用户没给称呼时，SVG 标题里用的中性称呼。
_DEFAULT_SUBJECT_NAME = "命主"

#: kerykeion 的属性名是英文名的小写蛇形，但 ``Medium_Coeli`` 是 ``medium_coeli``
#: 而非 ``medium_coeli`` 的机械小写（它本来就是），这里显式列出以免将来
#: 上游改名时静默取到错误属性。
_VENDOR_ATTRS: dict[str, str] = {
    "Sun": "sun",
    "Moon": "moon",
    "Mercury": "mercury",
    "Venus": "venus",
    "Mars": "mars",
    "Jupiter": "jupiter",
    "Saturn": "saturn",
    "Uranus": "uranus",
    "Neptune": "neptune",
    "Pluto": "pluto",
    "Ascendant": "ascendant",
    "Medium_Coeli": "medium_coeli",
}


class AstroError(ValueError):
    """星盘无法推算。

    kerykeion 对非法宫位制、非法恒星黄道模式、缺参数等一律抛 ``KerykeionException``。
    直接漏出去会让调用方无法区分"用户输入有误"与"引擎内部崩溃" —— 而前者
    应当触发追问，后者不该。故在此收窄，照
    :class:`~metaphys.engines.bazi.InvalidLunarDateError` 的先例。
    """


@dataclass(frozen=True)
class AstroResult:
    """一次排盘的全部产物。

    ``subject`` 是 kerykeion 的 ``AstrologicalSubjectModel``，**只给渲染用**。
    它不进 state、不进 JSON、不交给模型 —— 那两个出口都走 ``chart``。

    这样安排是为了避开一个静默的坑：``ChartDrawer`` 要的是 vendor 的
    ``SingleChartDataModel``；若 ``render_svg`` 从我们的 ``AstroChart`` 反向
    重建它，就要手工组装 12 个点、12 个宫头与全部相位，其中任何一个字段
    转置了，产出的都是一张**空白或错位、但不会报错**的图。让 vendor 对象
    原样走完"算 → 画"这一程，就不存在这个重建步骤。
    """

    chart: AstroChart
    subject: Any


def _to_astro_point(point: Any) -> AstroPoint:
    """kerykeion 的点模型 → 我们的契约模型。

    ``house`` 在 vendor 里是 ``"Eleventh_House"`` 这样的名字，缺失时是 ``None``
    （轴点常常没有）。转成 ``int`` 之后，核验才能把「月亮在第七宫」与
    「月亮在 7 宫」同等对待。
    """
    sign_abbr = str(point.sign)
    raw_name = str(point.name)
    return AstroPoint(
        key=raw_name.lower(),
        name=PLANET_NAMES.get(raw_name, raw_name),
        sign=SIGN_NAMES.get(sign_abbr, sign_abbr),
        sign_index=int(point.sign_num),
        position=float(point.position),
        abs_pos=float(point.abs_pos),
        house=HOUSE_NAMES.get(str(point.house)) if point.house else None,
        retrograde=bool(point.retrograde),
    )


def _to_house_cusp(index: int, cusp: Any) -> HouseCusp:
    sign_abbr = str(cusp.sign)
    return HouseCusp(
        index=index,
        sign=SIGN_NAMES.get(sign_abbr, sign_abbr),
        position=float(cusp.position),
        abs_pos=float(cusp.abs_pos),
    )


def _major_aspects(subject: Any) -> list[AspectRef]:
    """取五大主相位。

    kerykeion 默认还会给 quintile / semi-sextile 等次要相位（实测 27 条里
    有一条 quintile）。过滤掉的理由不是"它们不算数"，而是本产品要讲的层次
    用不到，而每多一条就多一个模型可以说错的断言。

    ``active_aspects`` 必须显式传，且与渲染侧同一个常量 —— 否则算出来 26 条、
    图上画 27 条（或者反过来），而两者都对不上时用户只能看着图猜哪边是真的。
    见 :data:`~metaphys.engines.astro.points.ACTIVE_ASPECTS`。
    """
    raw = AspectsFactory.natal_aspects(subject, active_aspects=list(ACTIVE_ASPECTS))
    result: list[AspectRef] = []
    for item in raw.aspects:
        aspect_key = str(item.aspect)
        if aspect_key not in MAJOR_ASPECTS:
            continue
        p1, p2 = str(item.p1_name), str(item.p2_name)
        if p1 not in PLANET_NAMES or p2 not in PLANET_NAMES:
            continue  # 被收窄掉的点不该出现在相位里；真出现说明上游变了
        result.append(
            AspectRef(
                p1=PLANET_NAMES[p1],
                p2=PLANET_NAMES[p2],
                aspect=ASPECT_NAMES[aspect_key],
                orbit=round(float(item.orbit), 2),
            )
        )
    return result


def _collect_warnings(profile: BirthProfile) -> list[str]:
    """排盘前的输入风险提示。

    这些都是**盘照出、但可信度下降**的情形。一律只警告不阻断：用户已经报了
    出生信息，直接拒绝会让他无处可去；带着告警出盘、由解读层降级表述，
    与八字的 ``warnings`` 是同一套做法。
    """
    moment = profile.birth_datetime
    notes: list[str] = []

    if moment.year < _PRE_1949_YEAR:
        notes.append(
            f"出生于 {_PRE_1949_YEAR} 年前。当时中国分五个时区，本引擎一律按 UTC+8 推算，"
            "上升点与宫位可能存在偏差；若原始记录用的是地方时，请以记录为准换算。"
        )

    if in_dst_fold(moment):
        notes.append(
            "出生时刻落在夏令时结束的重复区间（钟表上 01:00–02:00 出现两次），"
            "本盘按夏令时(UTC+9)解释；若实际为标准时，上升点约差 15°、宫位可能整体错开一宫。"
        )

    lng, lat = profile.longitude, profile.latitude
    in_coverage = _GEO_LNG_RANGE[0] <= lng <= _GEO_LNG_RANGE[1] and _GEO_LAT_RANGE[0] <= lat <= _GEO_LAT_RANGE[1]
    if not in_coverage:
        notes.append(
            f"出生地坐标（{lat:.2f}, {lng:.2f}）超出内置地理库的覆盖范围。"
            "本引擎按中国的 UTC+8/+9 推算时区，境外出生地可能不适用，请核对。"
        )
    elif lng < _WEST_LNG_THRESHOLD:
        notes.append(
            f"出生地经度 {lng:.2f}°E 位于新疆一带，当地民间作息有时按 UTC+6 计。"
            "本盘按钟表时间（UTC+8/+9）推算；若报的是当地作息时间，上升点与宫位会有较大偏差。"
        )

    return notes


def compute_astro(
    profile: BirthProfile,
    *,
    house_system: HouseSystem = HouseSystem.PLACIDUS,
    zodiac_type: ZodiacType = ZodiacType.TROPICAL,
    sidereal_mode: str | None = None,
) -> AstroResult:
    """排星盘。

    Args:
        profile: 出生信息（经纬度必需）
        house_system: 宫位制
        zodiac_type: 黄道制
        sidereal_mode: 恒星黄道模式，``zodiac_type=SIDEREAL`` 时必需

    Returns:
        :class:`AstroResult` —— ``.chart`` 进 state，``.subject`` 供渲染。

    Raises:
        NonexistentLocalTimeError: 出生时间落在夏令时缺口内。
        AstroError: 参数不被 kerykeion 接受（非法宫位制、非法恒星模式等）。
    """
    moment = profile.birth_datetime
    notes = _collect_warnings(profile)

    # 时区从共享的偏移表来 —— 与八字引擎同一个真源。这一步同时会把夏令时
    # 缺口内的时刻拦下来（与 compute_bazi 抛同一个异常类型）。
    offset = utc_offset_hours(moment)
    kwargs: dict[str, Any] = {
        # 姓名会原样成为 SVG 的标题（实测渲染成「张三 - 出生图」），所以与
        # city 同样必须先转义 —— kerykeion 对这两个字段都不做任何处理。
        # 留空时给中性称呼：否则标题会渲染成「 - 出生图」，前面挂一个孤零零的
        # 连字符，看起来像坏掉的图。
        "name": escape(profile.name.strip()) or _DEFAULT_SUBJECT_NAME,
        "year": moment.year,
        "month": moment.month,
        "day": moment.day,
        "hour": moment.hour,
        "minute": moment.minute,
        # 注意是复数 seconds，且是**仅关键字**参数 —— 写成 second 会 TypeError，
        # 而不是被静默忽略。
        "seconds": moment.second,
        # XML 转义后才交给 vendor。实测 kerykeion **不做**任何转义：把
        # ``<script>alert(1)</script>`` 当作出生地传进去，它会原样出现在 SVG
        # 的文本节点里 —— 而 SVG 是要由网关伺服给浏览器的（M4），这就是一个
        # 存储型 XSS。转义而非剔除：用户看到的仍是自己填的地名，XML 也是合法的。
        # 只影响**渲染**用的 vendor 对象；``AstroChart.profile`` 里保留原值。
        "city": escape(profile.place),
        "nation": "CN",
        "lng": profile.longitude,
        "lat": profile.latitude,
        "tz_str": tz_str_for(moment),
        # online=False 是硬要求：它为真时 kerykeion 会去打 geonames 查地名。
        # 显式传 lng/lat/tz_str 之后它完全不联网（实测无 HTTP 调用）。
        "online": False,
        "houses_system_identifier": house_system.value,
        "zodiac_type": zodiac_type.value,
        "active_points": list(ACTIVE_POINTS),
    }
    if zodiac_type is ZodiacType.SIDEREAL:
        # kerykeion 自带校验与明确的报错信息，不在我们这边重复一份合法值表 ——
        # 复制一份迟早会与上游漂移。
        kwargs["sidereal_mode"] = sidereal_mode

    try:
        subject = AstrologicalSubjectFactory.from_birth_data(**kwargs)
    except NonexistentLocalTimeError:
        raise  # 引擎自己的异常，语义明确，原样交给上层触发追问
    except Exception as exc:  # noqa: BLE001 —— 统一收窄，见 AstroError 的 docstring
        raise AstroError(f"星盘无法推算：{exc}") from exc

    points = [_to_astro_point(getattr(subject, _VENDOR_ATTRS[name])) for name in ACTIVE_POINTS]
    houses = [
        _to_house_cusp(index, getattr(subject, house_name.lower()))
        for house_name, index in sorted(HOUSE_NAMES.items(), key=lambda item: item[1])
    ]

    chart = AstroChart(
        profile=profile,
        house_system=house_system,
        zodiac_type=zodiac_type,
        sidereal_mode=sidereal_mode if zodiac_type is ZodiacType.SIDEREAL else None,
        utc_datetime=moment.replace(tzinfo=None) - timedelta(hours=offset),
        utc_offset_hours=offset,
        points=points,
        houses=houses,
        aspects=_major_aspects(subject),
        warnings=notes,
        engine_version=ENGINE_VERSION,
    )
    return AstroResult(chart=chart, subject=subject)


__all__ = ["ENGINE_VERSION", "AstroError", "AstroResult", "compute_astro"]
