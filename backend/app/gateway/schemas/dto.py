"""请求 DTO —— 只描述**进来**的形状。

响应形状不在这里，在 ``application/views.py``：响应是契约的一部分，会被
两个入口（旧 ``/api/*`` 与 ``/api/v1/*``）共用，放在应用层才只有一份。
请求 DTO 没有这个问题，它天然贴着路由。

``extra="forbid"`` 是有意的：拼错的字段名（``messages`` 而非 ``message``）
如果被静默忽略，用户得到的是一轮空对话，而不是一个 422。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunInput(BaseModel):
    """发起一轮解读。"""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入消息。")
        return value.strip()


__all__ = ["RunInput"]
