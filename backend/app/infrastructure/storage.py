"""受控文件存储。阶段 A 只有星盘 SVG。

## 路径不由用户拼接

文件名是 ``svg_filename()`` 算出来的 sha256 摘要（出生资料 + 排盘配置），
用户可控的姓名等文本从不进入路径。存储层再做一次边界检查，是因为"上游已经
校验过了"这种假设会在重构里悄悄失效，而越界读文件的代价不对称。

## 目录按调用解析，不是构造时缓存

两个原因：配置可以重载（``svg_dir`` 是配置项），以及集成测试需要在
应用构造**之后**替换目录。缓存下来会让这两件事其中之一失效，而且失效方式
是"读到了错误的目录"，不是报错。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from metaphys.schemas.chart import AstroChart
from metaphys.tools.builtins.astro_chart import svg_filename


class SvgArtifactStore:
    """按命盘内容定位受控 SVG 文件。"""

    def __init__(self, directory: Callable[[], Path]) -> None:
        self._directory = directory

    def svg_directory(self) -> Path:
        return Path(self._directory()).resolve()

    def locate(self, chart: dict[str, Any]) -> Path | None:
        """定位该命盘对应的 SVG；文件不存在或落在目录之外时返回 ``None``。

        命盘数据不合法时会抛 —— 那是服务端 bug（数据来自我们自己的引擎），
        返回 ``None`` 会把它伪装成"用户没有星盘图"，掩盖真正的问题。
        """
        parsed = AstroChart.model_validate(chart)
        directory = self.svg_directory()
        # resolve() 会解开符号链接，因此指向目录外的链接在这里就会被判越界。
        path = (directory / svg_filename(parsed.profile, parsed)).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            return None
        return path


__all__ = ["SvgArtifactStore"]
