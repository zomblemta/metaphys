"""地名解析的全名规则 —— 以及它修掉的那个死循环。

修复前，用户写「北京市朝阳区」（**表单占位符自己给的例子**）会走进一个出不来的
循环：

    工具：匹配到 3 个不同城市，请选：1. 北京市朝阳区  2. 辽宁省朝阳市  3. 吉林省长春市朝阳区
    用户：北京市朝阳区
    工具：（同一句话，再问一遍）

判据是"短名跨几个城市"，而「北京市朝阳区」经 ``lookup_relaxed`` 逐字去前缀后落到
短名「朝阳」上，于是和光秃秃的「朝阳区」毫无区别 —— 用户已经把地方指死了，却还被
要求再指一次，且**没有任何字符串能表达出他的答案**：照抄候选名无效，报编号更无效。
真实会话里模型卡在这里，最后回了一句英文求助。

所以本文件钉两个方向，缺一条都不算修好：

- 全名**必须**唯一解析（用户答过了，不该再问）；
- 短名**仍然**要问（「朝阳」真的跨三个城市，替他挑一个就是另一张命盘）。

第二条不是陪衬。把判据放宽成"能查到一个候选就不问"，第一条会绿，而这个文件会红。
"""

from __future__ import annotations

import json

import pytest
from metaphys.engines import geo as geo_engine
from metaphys.engines.geo import GeoPoint, lookup_candidates
from metaphys.tools.builtins.birth import resolve_birth_place
from metaphys.tools.builtins.geo import lookup_birthplace_tool, representatives, resolve_place

#: 真正跨城市的**短名** —— 用户给这些时必须追问。
AMBIGUOUS_SHORT_NAMES = ["朝阳", "朝阳区", "西湖区", "城关区"]

#: 完整行政区划名。每个的短名都跨城市，因此修复前无一例外都会陷入循环。
FULL_NAMES = ["北京市朝阳区", "吉林省长春市朝阳区", "辽宁省朝阳市双塔区", "北京市东城区"]


def _dataset_points() -> list[GeoPoint]:
    """直接从数据文件取全部条目 —— 不经被测的索引，自检才有意义。"""
    raw = json.loads(geo_engine._DATA_FILE.read_text(encoding="utf-8"))
    return [GeoPoint(province, city, area, lat, lng) for province, city, area, lat, lng in raw["places"]]


def _cities(candidates: list[GeoPoint]) -> set[tuple[str, str]]:
    return {(point.province, point.city) for point in candidates}


# --------------------------------------------------------------------------- #
# 全名：唯一解析
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("place", FULL_NAMES)
def test_a_full_name_resolves_uniquely(place: str):
    """写全名就是已经指死了地方，不该再回头问用户是哪一处。"""
    resolution = resolve_place(place)

    assert resolution.point is not None, f"{place!r} 被判成歧义了，用户会陷入追问循环"
    assert resolution.point.display_name == place
    assert resolution.candidates == [], "唯一解析时不该同时给出候选"


def test_lookup_candidates_prefers_the_full_name_over_the_short_one():
    """索引层：全名先于短名命中，这是上一条得以成立的机制。"""
    assert [point.display_name for point in lookup_candidates("北京市朝阳区")] == ["北京市朝阳区"]
    # 同一个地名，短名形式仍然是多候选 —— 差别只在于用户写了多少。
    assert len(lookup_candidates("朝阳区")) > 1


def test_a_full_name_does_not_claim_to_have_been_relaxed():
    """没有发生放宽匹配，就不该交代说放宽了。

    修复前「北京市东城区」是靠去前缀落到「东城区」才解析出来的，于是回复里会多一句
    "已将 '北京市东城区' 按 '东城区' 解析为 北京市东城区" —— 用户给的是最具体的
    形式，这句话读起来像是我们改了他的输入。
    """
    for place in FULL_NAMES:
        assert resolve_place(place).note == ""


# --------------------------------------------------------------------------- #
# 短名：仍然要问 —— 修全名不能把该问的也吞掉
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("place", AMBIGUOUS_SHORT_NAMES)
def test_a_short_name_still_asks(place: str):
    """跨城市的短名必须继续追问：选错城市就是另一个时辰的命盘。"""
    resolution = resolve_place(place)

    assert resolution.point is None, f"{place!r} 被擅自解析成了 {resolution.point} —— 这是替用户挑城市"
    assert len(_cities(representatives(resolution.candidates))) > 1


@pytest.mark.parametrize("place", AMBIGUOUS_SHORT_NAMES)
def test_every_offered_choice_is_answerable(place: str):
    """追问列出的候选，用户照抄任何一个都要能收敛。

    这是死循环的直接断言：修复前 ``resolve_place(候选.display_name)`` 会再次返回
    同一句追问，用户无论如何都答不上来。
    """
    offered = representatives(resolve_place(place).candidates)

    for candidate in offered:
        answered = resolve_place(candidate.display_name)
        assert answered.point == candidate, (
            f"用户照抄追问里的 {candidate.display_name!r}，却{'又收到了追问' if answered.point is None else '解析到了别处'}"
        )


# --------------------------------------------------------------------------- #
# 既有行为不回退
# --------------------------------------------------------------------------- #


def test_a_city_name_still_uses_its_center():
    """「北京」匹配到 17 个区县但同属一城 —— 按市级中心取值，不该问用户。

    这条防的是"修全名时顺手把宽泛名字也变成精确要求"。
    """
    assert resolve_place("北京").point.display_name == "北京市"
    assert resolve_place("北京市").point.display_name == "北京市"


def test_an_unknown_full_name_is_still_reported_as_missing():
    resolution = resolve_place("临冬城临冬区")

    assert resolution.point is None
    assert "未收录" in resolution.note
    assert resolution.candidates == []


# --------------------------------------------------------------------------- #
# 数据前提：全名唯一
# --------------------------------------------------------------------------- #


def test_full_names_are_unique_in_the_dataset():
    """``by_full`` 以 display_name 作键，它必须唯一，否则条目会被静默覆盖。

    数据扩充时这条先红，而不是等到某个地名悄悄解析到另一个城市。
    """
    names = [point.display_name for point in _dataset_points()]
    duplicated = sorted({name for name in names if names.count(name) > 1})

    assert not duplicated, f"这些全名在数据集里重复，by_full 会丢条目：{duplicated}"


# --------------------------------------------------------------------------- #
# 两个真实入口
# --------------------------------------------------------------------------- #


def test_the_lookup_tool_resolves_a_full_name():
    """模型实际调用的是这个工具，不是 ``resolve_place``。"""
    content = lookup_birthplace_tool.invoke({"place": "北京市朝阳区"})

    assert "唯一匹配：北京市朝阳区" in content
    assert "必须让用户选择" not in content


def test_the_chart_entry_point_gets_coordinates_for_a_full_name():
    """排盘引擎走的是 ``resolve_birth_place``，坐标要能直接落下去。"""
    point = resolve_place("北京市朝阳区").point
    coordinates, name, candidates, note = resolve_birth_place("北京市朝阳区", None)

    assert coordinates == (point.latitude, point.longitude)
    assert (name, candidates, note) == ("北京市朝阳区", [], "")
