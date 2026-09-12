"""星盘引擎测试。

三组：

1. **跨引擎同一瞬间** —— 八字与星盘必须对同一份出生信息得出同一个 UTC 瞬间。
   这是本轮最重要的一条：时区算错会让整张盘平移而**不报任何错**，是这类系统
   最隐蔽的失效模式。样本刻意覆盖夏令时内/外、1949 前、以及夏令时首末日。
2. **kerykeion 的坑** —— 默认城市 Greenwich、SVG 写进主目录、繁简混排，
   每一条都钉一个断言。
3. **词表防漂移** —— 简体名集合必须与 schema 双向相等。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import pytest
from metaphys.engines.astro import (
    ACTIVE_POINTS,
    ENGINE_VERSION,
    PLANET_NAMES,
    SIGN_NAMES,
    AstroError,
    compute_astro,
    render_svg,
)
from metaphys.engines.astro.points import (
    ACTIVE_ASPECTS,
    ASPECT_NAMES,
    HOUSE_NAMES,
    HOUSE_ORDINALS,
    MAJOR_ASPECTS,
    TRADITIONAL_ONLY,
    language_pack,
)
from metaphys.engines.bazi import MissingGenderError, compute_bazi
from metaphys.engines.china_time import NonexistentLocalTimeError
from metaphys.schemas.chart import (
    AstroChart,
    AstroPoint,
    BirthProfile,
    Gender,
    HouseCusp,
    HouseSystem,
    TimeAccuracy,
    ZodiacType,
)

BEIJING = (39.9042, 116.4074)
URUMQI = (43.8256, 87.6168)
LONDON = (51.5074, -0.1278)

#: 一个具体到分钟的出生信息，用于固定盘面。
_BIRTH = datetime(1990, 6, 15, 10, 30)


def make_profile(
    dt: datetime,
    place_coords: tuple[float, float] = BEIJING,
    *,
    place: str = "北京市",
    name: str = "",
    gender: Gender | None = Gender.MALE,
    accuracy: TimeAccuracy = TimeAccuracy.EXACT,
) -> BirthProfile:
    lat, lng = place_coords
    return BirthProfile(
        name=name,
        gender=gender,
        birth_datetime=dt,
        place=place,
        latitude=lat,
        longitude=lng,
        time_accuracy=accuracy,
    )


def visible_text(svg: str) -> list[str]:
    """SVG 里**人能看见**的文本。

    只取标签之间的内容，不做别的加工 —— 判据是"用户看得到什么"，所以字形
    引用（``xlink:href='#Gem'``）不在其中。这一点很重要：早先一版设计以为
    星座缩写是可见文字，据此写的测试会把"图上有星座"误判成"图上有英文"。
    """
    return [t.strip() for t in re.findall(r">([^<>]+)<", svg) if t.strip()]


def traditional_chars(svg: str) -> set[str]:
    return {ch for text in visible_text(svg) for ch in text if ch in TRADITIONAL_ONLY}


class TestCrossEngineSameInstant:
    """两个引擎必须落在同一个瞬间上。

    八字用自己硬编码的夏令时表，星盘用 ``engines.china_time`` 的共享偏移表。
    若两边漂移，同一份出生信息会排出"两个不同的时刻"，而**两边都不会报错**：
    上升点偏约 15°、宫位错开一整宫，用户看到的是一张自洽但错误的图。
    """

    @pytest.mark.parametrize(
        ("dt", "why"),
        [
            (datetime(1990, 6, 15, 10, 30), "夏令时内 —— 偏移应为 9"),
            (datetime(1988, 7, 1, 3, 30), "夏令时内且跨日 —— 换算要退到前一天"),
            (datetime(1990, 1, 15, 10, 30), "夏令时外 —— 偏移应为 8"),
            (datetime(2024, 2, 4, 17, 30), "立春当天 —— 节气与时刻无关"),
            (datetime(1930, 5, 20, 10, 0), "1949 前 —— 一律按标准时"),
            (datetime(1986, 5, 4, 3, 0), "夏令时首日，已过 02:00 切换点"),
            (datetime(1991, 9, 15, 0, 30), "夏令时末日，尚未到 02:00 切换点"),
        ],
    )
    def test_astro_and_bazi_agree_on_the_instant(self, dt: datetime, why: str) -> None:
        profile = make_profile(dt)
        bazi = compute_bazi(profile)
        astro = compute_astro(profile).chart

        # 八字没有直接给 UTC，用「钟表时间 − 偏移」还原 —— 偏移本身是它自己的
        # 结论，不是我们喂进去的。
        bazi_utc = bazi.clock_time - timedelta(hours=bazi.utc_offset_hours)

        assert astro.utc_offset_hours == bazi.utc_offset_hours, f"{why}：偏移不一致"
        assert astro.utc_datetime == bazi_utc, f"{why}：UTC 瞬间不一致"

    def test_dst_inside_and_outside_differ_by_one_hour(self) -> None:
        """夏令时内外必须真的差一小时 —— 否则上面那条测试可能在"两边都错"时也绿。"""
        inside = compute_astro(make_profile(datetime(1990, 6, 15, 10, 30))).chart
        outside = compute_astro(make_profile(datetime(1990, 1, 15, 10, 30))).chart
        assert (inside.utc_offset_hours, outside.utc_offset_hours) == (9, 8)

        # 换算必须是「钟表时间 − 偏移」，且**只有**这一项。两个样本的日期不同，
        # 直接比 UTC 会被日期差淹没（实测差 151 天），所以先扣掉钟表时间本身的
        # 差，剩下的必须恰好等于偏移之差。
        clock_delta = outside.profile.birth_datetime - inside.profile.birth_datetime
        utc_delta = outside.utc_datetime - inside.utc_datetime
        offset_delta = timedelta(hours=outside.utc_offset_hours - inside.utc_offset_hours)
        assert utc_delta == clock_delta - offset_delta
        assert offset_delta == timedelta(hours=-1)

    def test_dst_gap_is_refused_by_both_engines(self) -> None:
        """夏令时缺口内的时刻，两个引擎都要拒绝，而不是各自猜一个偏移。

        缺口是 **[02:00, 03:00)**：1990-04-15 时钟从 02:00 直接跳到 03:00，
        所以 02:30 不存在，而 01:30 是存在的（标准时）。取错小时这条测试会
        变成空转 —— 它"通过"是因为根本没有缺口。
        """
        for hour, minute, exists in [(1, 30, True), (2, 0, False), (2, 30, False), (3, 0, True)]:
            profile = make_profile(datetime(1990, 4, 15, hour, minute))
            for engine in (compute_bazi, compute_astro):
                if exists:
                    engine(profile)  # 不抛
                else:
                    with pytest.raises(NonexistentLocalTimeError):
                        engine(profile)


class TestChartContents:
    def test_engine_version_is_pinned(self) -> None:
        assert compute_astro(make_profile(_BIRTH)).chart.engine_version == ENGINE_VERSION

    def test_exactly_the_twelve_active_points(self) -> None:
        """收窄到 12 个点必须生效 —— 多出来的小行星是模型可以张冠李戴的对象。"""
        chart = compute_astro(make_profile(_BIRTH)).chart
        assert [p.key for p in chart.points] == [n.lower() for n in ACTIVE_POINTS]
        assert len(chart.points) == 12

    def test_houses_are_ordered_and_complete(self) -> None:
        chart = compute_astro(make_profile(_BIRTH)).chart
        assert [h.index for h in chart.houses] == list(range(1, 13))

    def test_only_major_aspects_survive(self) -> None:
        chart = compute_astro(make_profile(_BIRTH)).chart
        assert chart.aspects, "样本盘应当有相位，否则这条测试是空转的"
        assert {a.aspect for a in chart.aspects} <= set(ASPECT_NAMES.values())

    def test_aspect_list_matches_what_the_svg_draws(self) -> None:
        """模型读到的相位表与图上画的线必须一致。

        实测：算相位那一路与画图那一路各有各的默认容许度，且默认都含 quintile。
        不显式对齐就会算 26 条、画 27 条 —— 用户按图索骥时无从判断谁对。
        """
        from kerykeion import AspectsFactory

        result = compute_astro(make_profile(_BIRTH))
        drawn = AspectsFactory.natal_aspects(result.subject, active_aspects=list(ACTIVE_ASPECTS))
        assert len(drawn.aspects) == len(result.chart.aspects)
        assert "quintile" not in {str(a.aspect) for a in drawn.aspects}

    def test_determinism(self) -> None:
        """同一输入两次必须完全一致 —— 否则"可复核"无从谈起。"""
        profile = make_profile(_BIRTH)
        first = compute_astro(profile).chart.model_dump(mode="json")
        second = compute_astro(profile).chart.model_dump(mode="json")
        assert first == second

    def test_sidereal_shifts_the_signs(self) -> None:
        """恒星黄道必须真的改变星座 —— 否则这个开关是坏的，而它看起来是好的。"""
        profile = make_profile(_BIRTH)
        tropical = compute_astro(profile).chart
        sidereal = compute_astro(profile, zodiac_type=ZodiacType.SIDEREAL, sidereal_mode="LAHIRI").chart

        assert tropical.zodiac_type is ZodiacType.TROPICAL
        assert sidereal.zodiac_type is ZodiacType.SIDEREAL
        assert sidereal.sidereal_mode == "LAHIRI"
        # 岁差约 24°，一般正好差一个星座
        delta = (tropical.point("sun").sign_index - sidereal.point("sun").sign_index) % 12
        assert delta == 1

    def test_sidereal_mode_is_not_recorded_for_tropical(self) -> None:
        """回归黄道下不该留下一个孤零零的恒星模式 —— 那是自相矛盾的前提声明。"""
        chart = compute_astro(make_profile(_BIRTH), sidereal_mode="LAHIRI").chart
        assert chart.sidereal_mode is None

    def test_house_system_is_recorded_and_effective(self) -> None:
        """宫位制是前提声明，必须进盘；且换制要真的改变宫头。"""
        profile = make_profile(_BIRTH)
        placidus = compute_astro(profile).chart
        equal = compute_astro(profile, house_system=HouseSystem.EQUAL).chart

        assert placidus.house_system is HouseSystem.PLACIDUS
        assert equal.house_system is HouseSystem.EQUAL
        assert [h.abs_pos for h in placidus.houses] != [h.abs_pos for h in equal.houses]

    def test_bad_sidereal_mode_becomes_astro_error(self) -> None:
        """kerykeion 的异常要收窄成 AstroError，调用方才能区分"输入有误"与"引擎崩了"。"""
        with pytest.raises(AstroError):
            compute_astro(
                make_profile(_BIRTH),
                zodiac_type=ZodiacType.SIDEREAL,
                sidereal_mode="NOT_A_MODE",
            )

    def test_unknown_time_is_accepted_by_the_engine(self) -> None:
        """引擎本身收 ``unknown`` —— 拒绝是**工具层**的职责（追问比报错对用户更好）。

        这条测试把职责边界钉住：若哪天有人在引擎里加一道"非 exact 就抛"，工具层
        的追问逻辑就变成死代码，而用户看到的是报错。
        """
        profile = make_profile(datetime(1990, 6, 15, 0, 0), accuracy=TimeAccuracy.UNKNOWN)
        assert compute_astro(profile).chart.profile.time_accuracy is TimeAccuracy.UNKNOWN


class TestWarnings:
    def test_pre_1949_is_flagged(self) -> None:
        chart = compute_astro(make_profile(datetime(1930, 5, 20, 10, 0))).chart
        assert any("1949" in w for w in chart.warnings)

    def test_modern_birth_has_no_pre_1949_warning(self) -> None:
        chart = compute_astro(make_profile(_BIRTH)).chart
        assert not any("1949" in w for w in chart.warnings)

    def test_dst_fold_is_flagged(self) -> None:
        """夏令时结束的重复小时：盘照出，但必须说明用的是哪一种解释。"""
        chart = compute_astro(make_profile(datetime(1990, 9, 16, 1, 30))).chart
        assert any("重复" in w for w in chart.warnings)

    def test_xinjiang_longitude_is_flagged(self) -> None:
        chart = compute_astro(make_profile(_BIRTH, URUMQI)).chart
        assert any("新疆" in w for w in chart.warnings)

    def test_out_of_coverage_place_is_flagged(self) -> None:
        """境外出生地：时区前提（中国用 UTC+8/+9）本身就不成立。"""
        chart = compute_astro(make_profile(_BIRTH, LONDON, place="伦敦")).chart
        assert any("覆盖范围" in w for w in chart.warnings)

    def test_clean_input_has_no_warnings(self) -> None:
        """反面样本：全都正常的输入不该有任何告警，否则告警会被用户无视。"""
        assert compute_astro(make_profile(_BIRTH)).chart.warnings == []


class TestKerykeionPitfalls:
    """M0 记下的三个坑，逐个钉住。"""

    def test_city_is_not_silently_greenwich(self) -> None:
        """不传 city/nation 会默认 Greenwich/GB —— 一张坐标正确、地名错误的盘。"""
        result = compute_astro(make_profile(_BIRTH))
        assert result.subject.city == "北京市"
        assert "Greenwich" not in str(result.subject.city)

    def test_svg_is_a_string_and_writes_nothing(self, tmp_path, monkeypatch) -> None:
        """``render_svg`` 是纯函数：不落盘，也不该碰用户主目录。

        kerykeion 默认把 SVG 写进 ``Path.home()``；只要渲染走的是
        ``generate_svg_string()`` 而不是 ``save_svg()``，就碰不到文件系统。
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        svg = render_svg(compute_astro(make_profile(_BIRTH)))
        # 开头是一段 kerykeion 的版权注释，不是 <?xml ?>，所以只断言"含 <svg"。
        assert "<svg" in svg
        assert list(tmp_path.rglob("*.svg")) == []

    def test_svg_has_no_traditional_characters(self) -> None:
        """kerykeion 的 CN 包繁简混排，覆盖语言包之后必须**一个繁体字都不剩**。

        这条是语言包覆盖的验收线：迭代到它绿为止，而不是靠人工核对。
        """
        leftover = traditional_chars(render_svg(compute_astro(make_profile(_BIRTH))))
        assert leftover == set(), f"SVG 里仍有繁体字：{sorted(leftover)}"

    def test_language_pack_wrong_shape_is_detectable(self) -> None:
        """**反面样本**：多包一层 ``{"CN": ...}`` 不报错也不生效。

        这条测试存在的意义是证明上面那条正面测试**有鉴别力** —— 若传错形态也
        能绿，那条测试就什么都没测到。实测错误形态的产物与完全不给语言包
        **逐字相同**，这正是它的危险之处：错法比正法安静。
        """
        from kerykeion import ChartDataFactory, ChartDrawer

        result = compute_astro(make_profile(_BIRTH))
        data = ChartDataFactory.create_natal_chart_data(result.subject)

        def leftovers(pack: object) -> set[str]:
            svg = ChartDrawer(data, chart_language="CN", language_pack=pack).generate_svg_string()
            return traditional_chars(svg)

        wrong = leftovers({"CN": language_pack()})
        none_at_all = leftovers(None)
        assert wrong, "错误形态若也全简体，这条反向测试就失去意义"
        assert wrong == none_at_all, "错误形态应与『完全不给语言包』等价 —— 即静默无效"

    def test_signs_are_glyph_references_not_text(self) -> None:
        """星座在图上是**字形引用**，不是可见文字。

        早先一版设计打算把可见文本里的 ``Gem`` 替换成 ``双子``。那条路会改坏
        ``xlink:href='#Gem'``，而文档里没有 ``id='双子'`` 的字形 —— 整圈星座
        图标会变成空白，且**不会报错**。这条测试把这个认识钉住。
        """
        svg = render_svg(compute_astro(make_profile(_BIRTH)))
        text = " ".join(visible_text(svg))

        for abbr in SIGN_NAMES:
            assert not re.search(rf"(?<![A-Za-z]){abbr}(?![A-Za-z])", text), f"{abbr} 不应出现在可见文本里"
            assert f"id='{abbr}'" in svg or f'id="{abbr}"' in svg, f"{abbr} 的字形定义应当存在"

    def test_svg_is_well_formed_xml(self) -> None:
        """能被 XML 解析 —— M4 要把它伺服给浏览器，解析失败就是一张白图。"""
        ElementTree.fromstring(render_svg(compute_astro(make_profile(_BIRTH))))


class TestSvgInjection:
    """SVG 会被 M4 伺服给浏览器，所以用户输入进入 SVG 的每一条路径都要转义。

    实测 kerykeion **不做**任何转义：把 ``<script>`` 当出生地传进去，它会原样
    出现在文本节点里。这是一个存储型 XSS。
    """

    @pytest.mark.parametrize(
        "hostile",
        [
            "<script>alert(1)</script>",
            '"><script>alert(2)</script>',
            "A&B",
            "]]><script>alert(3)</script>",
        ],
    )
    def test_place_never_injects_markup(self, hostile: str) -> None:
        svg = render_svg(compute_astro(make_profile(_BIRTH, place=hostile)))
        assert "<script>" not in svg
        # 转义而非剔除：用户应当仍能看见自己填的地名，XML 也仍然合法。
        # 断言的是**转义后**的形态 —— 对 "A&B" 这种没有标签的载荷，"原样出现"
        # 与"被吃掉"是分不出来的。
        #
        # 双引号归一为单引号：kerykeion 写文本节点时会把 `"` 换成 `'`（实测），
        # 所以两边都做同样的归一化再比，否则测试会因为一个与安全无关的排版
        # 细节而红，掩盖掉它真正该守的东西。
        assert escape(hostile).replace('"', "'") in svg, f"{hostile!r} 应当以转义形态出现，而不是被丢弃"
        ElementTree.fromstring(svg)

    def test_the_vendor_object_gets_the_escaped_place(self) -> None:
        """vendor 对象里的地名必须是转义过的 —— 这是防注入的第一道，也是最直接的一道。"""
        payload = "<script>alert(1)</script>"
        result = compute_astro(make_profile(_BIRTH, place=payload))
        assert "<script>" not in result.subject.city
        assert "&lt;script&gt;" in result.subject.city

    def test_name_never_injects_markup(self) -> None:
        """姓名同样会进 SVG 标题。"""
        svg = render_svg(compute_astro(make_profile(_BIRTH, name="<script>alert(4)</script>")))
        assert "<script>" not in svg
        ElementTree.fromstring(svg)

    def test_blank_name_does_not_leave_a_dangling_dash(self) -> None:
        """没给姓名时，标题不该渲染成「 - 出生图」。"""
        titles = [t for t in visible_text(render_svg(compute_astro(make_profile(_BIRTH)))) if "出生图" in t]
        assert titles, "图上应当有标题"
        assert not any(t.startswith("-") for t in titles), titles

    def test_chart_data_keeps_the_raw_place(self) -> None:
        """转义只作用于**渲染**用的 vendor 对象；进 state 的那份必须是原值。

        否则 JSON 里会存着 ``&lt;script&gt;``，前端展示与后续追问都会拿到脏数据。
        """
        chart = compute_astro(make_profile(_BIRTH, place="A&B")).chart
        assert chart.profile.place == "A&B"


class TestVocabularyConsistency:
    """词表防漂移 —— 名字改了一处而没改另一处，核验会静默失准。"""

    def test_chart_names_all_come_from_the_vocabulary(self) -> None:
        """盘上的每个名字都必须在词表里 —— 否则核验认不出模型说的词。"""
        chart = compute_astro(make_profile(_BIRTH)).chart
        for point in chart.points:
            assert point.name in PLANET_NAMES.values(), point.name
            assert point.sign in SIGN_NAMES.values(), point.sign
        for aspect in chart.aspects:
            assert aspect.aspect in ASPECT_NAMES.values(), aspect.aspect
            assert aspect.p1 in PLANET_NAMES.values(), aspect.p1
            assert aspect.p2 in PLANET_NAMES.values(), aspect.p2

    def test_vocabulary_and_schema_have_the_same_shape(self) -> None:
        """双向相等：词表多一个键或少一个键都要失败。"""
        assert set(PLANET_NAMES) == set(ACTIVE_POINTS)
        assert len(SIGN_NAMES) == 12
        assert set(MAJOR_ASPECTS) == set(ASPECT_NAMES)
        assert set(HOUSE_NAMES.values()) == set(range(1, 13))

        # schema 的字段范围必须容得下引擎产出的值 —— 收紧 schema 时立刻失败，
        # 而不是等到某天某个坐标越界才在运行时炸。
        chart = compute_astro(make_profile(_BIRTH)).chart
        for point in chart.points:
            AstroPoint.model_validate(point.model_dump())
        for house in chart.houses:
            HouseCusp.model_validate(house.model_dump())

    def test_house_ordinals_cover_all_twelve(self) -> None:
        assert len(HOUSE_ORDINALS) == 12
        assert len(set(HOUSE_ORDINALS)) == 12


class TestGenderIsOptionalForAstro:
    """``BirthProfile.gender`` 对星盘毫无意义，缺失不该妨碍排盘。"""

    def test_astro_works_without_gender(self) -> None:
        chart = compute_astro(make_profile(_BIRTH, gender=None)).chart
        assert isinstance(chart, AstroChart)
        assert chart.profile.gender is None

    def test_astro_ignores_gender_entirely(self) -> None:
        """男女的星盘必须逐字相同 —— 性别不是星盘的输入。"""
        male = compute_astro(make_profile(_BIRTH, gender=Gender.MALE)).chart.model_dump(mode="json")
        female = compute_astro(make_profile(_BIRTH, gender=Gender.FEMALE)).chart.model_dump(mode="json")
        male["profile"].pop("gender")
        female["profile"].pop("gender")
        assert male == female

    def test_bazi_never_silently_treats_missing_gender_as_female(self) -> None:
        """八字缺性别必须**报错**，不能默认。

        危险之处在于 ``None is Gender.MALE`` 求值为假 —— 若不报错，男命会被
        静默排成女命，大运整个反向，而用户从盘上完全看不出来。
        """
        with pytest.raises(MissingGenderError):
            compute_bazi(make_profile(_BIRTH, gender=None))

    def test_male_and_female_really_do_differ_in_bazi(self) -> None:
        """反向证明上面那条守的是真东西：性别确实会改变八字结论。"""
        male = compute_bazi(make_profile(_BIRTH, gender=Gender.MALE))
        female = compute_bazi(make_profile(_BIRTH, gender=Gender.FEMALE))
        assert [d.gan_zhi for d in male.da_yun] != [d.gan_zhi for d in female.da_yun]
