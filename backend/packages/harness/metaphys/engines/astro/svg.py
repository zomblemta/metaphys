"""星盘渲染 —— 产出 SVG 字符串。

**纯函数，不落盘。** 落盘是工具层的事（``tools/builtins/astro_chart.py``）：
"写到哪个目录、叫什么文件名"是策略，"画出什么内容"是渲染。两者混在一起，
测试就没法在不碰文件系统的前提下验证渲染结果 —— 而本项目要测的恰恰是渲染
结果（繁体残字、语言包传参形态、注入）。

两处刻意的设计，写在下面各自的注释里：

1. 入参是 :class:`~metaphys.engines.astro.chart.AstroResult` 而**不是** ``AstroChart``；
2. **不**对星座缩写做文本替换 —— 图上的星座是字形引用，不是文字。
"""

from __future__ import annotations

from kerykeion import ChartDataFactory, ChartDrawer

from metaphys.engines.astro.chart import AstroResult
from metaphys.engines.astro.points import ACTIVE_ASPECTS, language_pack


def build_drawer(result: AstroResult, *, theme: str = "classic") -> ChartDrawer:
    """装配 ``ChartDrawer``。

    Args:
        result: :func:`~metaphys.engines.astro.chart.compute_astro` 的产物。
            **要的是 ``result`` 整体而非 ``result.chart``** —— 见 ``AstroResult``
            的 docstring：``ChartDrawer`` 吃的是 vendor 的 ``SingleChartDataModel``，
            从我们的 ``AstroChart`` 反向重建它需要手工组装 12 个点、12 个宫头与
            全部相位，任何一个字段转置都会产出一张**空白或错位、却不报错**的图。
        theme: 配色主题，交给 kerykeion 校验。

    Returns:
        已装配但尚未渲染的 drawer。
    """
    data = ChartDataFactory.create_natal_chart_data(
        result.subject,
        # 与 chart.py 里算相位的那一路同一个常量 —— 否则模型读到的相位表与图上
        # 画出来的线会不一致（实测差一条 quintile）。
        active_aspects=list(ACTIVE_ASPECTS),
    )
    return ChartDrawer(
        data,
        theme=theme,  # type: ignore[arg-type]  # kerykeion 的 Literal，由它自己校验
        chart_language="CN",
        # ⚠️ 传参形态是**该语言那一层**的 dict，不是 {"CN": {...}}。
        # 源码是 overrides = {self.chart_language: dict(language_pack)}。
        # 多包一层不报错、也不生效 —— 见 language_pack() 的 docstring。
        language_pack=language_pack(),
    )


def render_svg(result: AstroResult, *, theme: str = "classic") -> str:
    """渲染成 SVG 字符串。

    调用方负责落盘。这个函数**不**写任何文件，也不读任何文件。

    **不做星座缩写替换。** 初版设计打算把图里的 ``Gem`` 换成 ``双子``。实测
    这是错的：星座在图上是 **SVG 字形引用**（``xlink:href='#Gem'`` / ``id='Gem'``
    / ``kr:sign='Gem'``），从不作为可见文字出现 —— 剥掉全部标签后，可见文本里
    一个星座缩写都没有。替换会把这些引用改成 ``#双子``，而文档里没有
    ``id='双子'`` 的字形，于是一整圈星座图标全部变成空白。这正是"用文本手段
    去改图形文档"的典型失效：图不会报错，只会变丑。
    """
    return build_drawer(result, theme=theme).generate_svg_string()


__all__ = ["build_drawer", "render_svg"]
