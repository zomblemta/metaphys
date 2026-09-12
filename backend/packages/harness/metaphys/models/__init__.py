"""模型层 —— 按 ``config.yaml`` 反射构造聊天模型，换 provider 不改代码。"""

from metaphys.models.factory import create_chat_model

__all__ = ["create_chat_model"]
