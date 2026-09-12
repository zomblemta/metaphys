"""工具注册 —— 按 ``config.yaml`` 反射装配工具集。

加一个工具只改配置，不改代码：写一行 ``use: 模块:对象`` 即可。解析失败会抛出
指明"哪个 use: 写错了"的 ``ImportError``（见 :mod:`metaphys.reflection.resolvers`），
而不是等到模型第一次调用时才炸。

``ask_clarification`` **不走配置、无条件挂载**：它与
:class:`~metaphys.middlewares.clarification.ClarificationMiddleware` 是一对，
少了任何一个，"信息不足时先追问"这条防线就整体失效。这种成对约束不该留给
配置去维护 —— 配置漏写不会报错，只会让模型重新开始猜。
"""

from __future__ import annotations

import logging

from langchain_core.tools import BaseTool

from metaphys.config import AppConfig, get_app_config
from metaphys.reflection import resolve_variable
from metaphys.tools.builtins.clarification import ask_clarification_tool

logger = logging.getLogger(__name__)


def load_tools(app_config: AppConfig | None = None) -> list[BaseTool]:
    """解析配置中的工具，并附上框架必需的澄清工具。

    Raises:
        ImportError: 某条 ``use:`` 路径无法解析。
        TypeError: 解析出的对象不是 ``BaseTool``。
        ValueError: 工具重名 —— 模型会看到两个同名 tool，调用结果不可预期。
    """
    config = app_config or get_app_config()

    tools: list[BaseTool] = []
    for tool_config in config.get_tool_configs():
        tool = resolve_variable(tool_config.use, BaseTool)
        if tool.name != tool_config.name:
            # 不报错，只告警：name 只是配置侧的标识，真正生效的是工具自身的名字。
            # 但两者不一致通常意味着配置改过而工具没跟着改，值得留痕。
            logger.warning(
                "config.yaml 中工具名为 %r，但 %s 实际注册为 %r —— 以实际名字为准",
                tool_config.name,
                tool_config.use,
                tool.name,
            )
        tools.append(tool)

    tools.append(ask_clarification_tool)

    names = [tool.name for tool in tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"工具重名：{duplicates} —— 模型会看到两个同名工具，调用结果不可预期")

    return tools


__all__ = ["load_tools"]
