"""星盘引擎 —— 纯确定性计算，零 LLM 依赖。

照 :mod:`metaphys.engines.bazi` 的写法做 shim，但**是惰性的**，理由如下。

**为什么要惰性。** ``points`` 的词表要供 :mod:`metaphys.middlewares.grounding`
做落地核验，而中间件按架构不变量不得具备排盘能力。词表只是字符串，本身不需要
星历表 —— 但 Python 在 import 任何子模块之前**一定会先执行父包的** ``__init__``，
所以只要这里的顶层 import 了 ``chart``/``svg``，那么连
``from metaphys.engines.astro.points import PLANET_NAMES`` 这样一句都会把
kerykeion 拉进依赖图。实测过：那样写之后 ``metaphys.engines.astro.points``
会连带导入 ``kerykeion, swisseph``。

于是词表与星历表分家的意义就归零了。用 PEP 562 的模块级 ``__getattr__``
把需要 kerykeion 的名字推迟到**真正被取用**时才加载，这条不变量才是真的。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# 顶层只导入不依赖 kerykeion 的词表 —— 这是本模块唯一可以安全立即执行的导入。
from metaphys.engines.astro.points import (
    ACTIVE_ASPECTS,
    ACTIVE_POINTS,
    ASPECT_NAMES,
    HOUSE_NAMES,
    HOUSE_ORDINALS,
    MAJOR_ASPECTS,
    PLANET_NAMES,
    SIGN_NAMES,
    SIGN_ORDER,
    language_pack,
)

if TYPE_CHECKING:  # 仅供类型检查器；运行时不会执行，因此不会拉入 kerykeion
    from metaphys.engines.astro.chart import (
        ENGINE_VERSION,
        AstroError,
        AstroResult,
        compute_astro,
    )
    from metaphys.engines.astro.svg import build_drawer, render_svg

#: 需要 kerykeion 的名字 → 它们所在的模块。
_LAZY: dict[str, str] = {
    "ENGINE_VERSION": "chart",
    "AstroError": "chart",
    "AstroResult": "chart",
    "compute_astro": "chart",
    "build_drawer": "svg",
    "render_svg": "svg",
}


def __getattr__(name: str) -> Any:
    """按需加载需要 kerykeion 的名字（PEP 562）。

    加载后写回 ``globals()``，所以 ``__getattr__`` 对同一个名字只会跑一次。
    """
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})


__all__ = [
    "ACTIVE_ASPECTS",
    "ACTIVE_POINTS",
    "ASPECT_NAMES",
    "ENGINE_VERSION",
    "HOUSE_NAMES",
    "HOUSE_ORDINALS",
    "MAJOR_ASPECTS",
    "PLANET_NAMES",
    "SIGN_NAMES",
    "SIGN_ORDER",
    "AstroError",
    "AstroResult",
    "build_drawer",
    "compute_astro",
    "language_pack",
    "render_svg",
]
