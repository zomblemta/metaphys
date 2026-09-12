"""出生地 → 经纬度。

真太阳时（八字）与星盘都依赖出生地经度，因此这是两个排盘引擎的共同前置。

坐标系说明
----------
内置数据为 GCJ-02（火星坐标系），与 WGS-84 在中国境内存在约 0.006° 的一致偏移。
换算为真太阳时约 1.4–1.8 秒，而时辰边界相隔 7200 秒，对排盘无实质影响；
对星盘上升点的影响在角秒级。数据来源与许可见 ``data/china_places.json`` 的 ``_meta``。

本模块刻意保持为纯查表，不含任何 LLM 依赖，可独立测试。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

_DATA_FILE = Path(__file__).parent / "data" / "china_places.json"

# 行政区划后缀，用于归一化匹配。按长度降序，避免"自治州"被"州"抢先截断。
_SUFFIXES = (
    "维吾尔自治区",
    "壮族自治区",
    "回族自治区",
    "特别行政区",
    "自治区",
    "自治州",
    "自治县",
    "自治旗",
    "地区",
    "盟",
    "省",
    "市",
    "区",
    "县",
    "旗",
)


class GeoPoint(NamedTuple):
    """一个已解析的出生地。"""

    province: str
    city: str
    area: str
    latitude: float
    longitude: float

    @property
    def display_name(self) -> str:
        """用于展示与写入 LLM 上下文的规范地名 —— 不含空字段。"""
        parts = [p for p in (self.province, self.city, self.area) if p and p != "市辖区"]
        return "".join(parts)


def normalize(name: str) -> str:
    """去掉行政区划后缀，得到用于匹配的短名。"""
    s = name.strip()
    for suffix in _SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, GeoPoint], dict[str, list[GeoPoint]], dict[str, GeoPoint]]:
    """构建查找索引。首次调用时加载数据，之后缓存。

    Returns:
        (按市全名, 按短名到候选列表, 按省短名到省会/首条)
    """
    raw = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    by_city: dict[str, GeoPoint] = {}
    by_short: dict[str, list[GeoPoint]] = {}
    by_province: dict[str, GeoPoint] = {}

    def _is_center(area: str) -> bool:
        """市级中心条目：直辖市为空串，普通地级市为"市辖区"。"""
        return area in ("", "市辖区")

    for province, city, area, lat, lng in raw["places"]:
        point = GeoPoint(province, city, area, lat, lng)

        # 直辖市的数据形如 province="北京市" city="市辖区"。
        # 若直接按 city 建索引，四个直辖市会全部挤在 "市辖区" 这个键上互相覆盖
        # （最后写入者胜出），且各自的市名反而缺失键。故直辖市改用省名作市名。
        city_key = province if city == "市辖区" else city

        existing = by_city.get(city_key)
        if existing is None or (_is_center(area) and not _is_center(existing.area)):
            by_city[city_key] = point

        keys = {normalize(city_key)}
        if area and not _is_center(area):
            keys.add(normalize(area))
        for key in keys:
            if key:
                by_short.setdefault(key, []).append(point)

        by_province.setdefault(normalize(province), point)

    return by_city, by_short, by_province


def lookup_candidates(place: str) -> list[GeoPoint]:
    """返回所有匹配的候选地点。歧义（如"朝阳区"）时返回多条。"""
    by_city, by_short, by_province = _index()
    s = place.strip()
    if not s:
        return []

    if s in by_city:
        return [by_city[s]]

    hits = by_short.get(normalize(s))
    if hits:
        # 去重后返回，保持数据顺序稳定
        seen: set[tuple] = set()
        unique: list[GeoPoint] = []
        for p in hits:
            key = (p.province, p.city, p.area)
            if key not in seen:
                seen.add(key)
                unique.append(p)
        return unique

    prov = by_province.get(normalize(s))
    return [prov] if prov else []


def lookup(place: str) -> GeoPoint | None:
    """解析出生地。歧义时返回首个候选 —— 调用方应先用 ``lookup_candidates`` 消歧。"""
    candidates = lookup_candidates(place)
    return candidates[0] if candidates else None
