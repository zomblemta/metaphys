"""消息文本提取。

看起来只是"把 content 转成 str"，但**必须只有一处实现**：落地核验扫的文本与
展示给用户的文本若是用两段不同的代码取出来的，就可能一个是拼接结果、另一个
只取首块 —— 于是核验放过了用户实际看到的那段话。这种漂移不会报错，只会让
防线悄悄出现缺口。
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage


def message_text(message: BaseMessage) -> str:
    """取消息正文的纯文本。

    ``content`` 可能是 ``str``，也可能是分块列表（provider 返回多模态内容时的
    形态）；后者只取 ``type == "text"`` 的块，其余（图片、工具结果等）不参与。
    """
    content = message.content

    if isinstance(content, str):
        return content

    parts: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


__all__ = ["message_text"]
