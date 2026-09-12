"""工具侧的共享类型。

`ToolRuntime` 的两个类型参数是 ``[ContextT, StateT]`` —— **context 在前、
state 在后**，与直觉相反；写反了不会立刻报错，只会在取
``runtime.state["charts"]`` 时才暴露。故在此固定成别名，全项目只写一次。

- ``dict[str, Any]``：调用方传入的上下文（M2 无实际内容，M4 网关会塞请求级信息）
- :class:`ThreadState`：图状态，``charts`` / ``birth_profile`` 都在里面
"""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime

from metaphys.agents.thread_state import ThreadState

Runtime = ToolRuntime[dict[str, Any], ThreadState]

__all__ = ["Runtime"]
