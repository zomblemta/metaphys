"""Agent 中间件 —— 架构不变量在这里从"约定"变成"机制"。

三个中间件各管一件事，**顺序即语义**（见
:mod:`metaphys.agents.lead_agent.agent` 里的断言）：

1. :class:`ToolErrorHandlingMiddleware` —— 兜底未预期异常；必须在最外层才能拦住
   其它中间件抛出的异常
2. :class:`GroundingMiddleware` —— 核验模型输出里的命盘数据是否都来自工具
3. :class:`ClarificationMiddleware` —— 追问时结束本轮、并丢弃同轮兄弟工具调用；
   **必须永远最后**
"""

from metaphys.middlewares.clarification import ClarificationMiddleware
from metaphys.middlewares.error_handling import ToolErrorHandlingMiddleware
from metaphys.middlewares.grounding import (
    ALL_GANZHI,
    GroundingMiddleware,
    chart_vocabulary,
    find_astro_claims,
    find_astro_fabrications,
    find_fabrications,
)

__all__ = [
    "ALL_GANZHI",
    "ClarificationMiddleware",
    "GroundingMiddleware",
    "ToolErrorHandlingMiddleware",
    "chart_vocabulary",
    "find_astro_claims",
    "find_astro_fabrications",
    "find_fabrications",
]
