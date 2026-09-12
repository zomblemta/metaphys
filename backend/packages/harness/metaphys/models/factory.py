"""模型工厂 —— 按 ``config.yaml`` 反射构造聊天模型。

换 provider 只改配置里的 ``use:`` 一行，代码不动。这与工具注册是同一套机制
（:mod:`metaphys.reflection`），失败信息也因此一致：路径写错会被明确指出。

**为什么默认不是 ``deepseek-reasoner``：** 开启 thinking 后，DeepSeek 要求每条
assistant 消息都回填 ``reasoning_content``；上游 ``ChatDeepSeek`` 把它存进
``additional_kwargs`` 却在重建请求时丢弃，多轮工具调用会直接报错。M2 用非
thinking 的 ``deepseek-chat`` 绕开这个坑；若将来要换，必须同时移植
deer-flow 的 ``PatchedChatDeepSeek``（覆写 ``_get_request_payload``）。
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from metaphys.config import AppConfig, ModelConfig, get_app_config
from metaphys.reflection import resolve_class


def create_chat_model(name: str | None = None, *, app_config: AppConfig | None = None) -> BaseChatModel:
    """构造聊天模型。``name`` 为 None 时用配置里的 ``default_model``。

    Raises:
        ImportError: ``use:`` 路径无法解析。
        TypeError: 解析出的不是 ``BaseChatModel`` 子类。
        ValueError: ``name`` 不在配置中，或构造参数被 provider 拒绝。
    """
    config = app_config or get_app_config()
    model_config: ModelConfig = config.get_model_config(name)

    model_class = resolve_class(model_config.use, BaseChatModel)
    try:
        return model_class(**model_config.constructor_kwargs())
    except TypeError as err:
        # 注意：**拼错的键不会走到这里** —— langchain 会把不认识的参数挪进
        # `model_kwargs` 并打一条 UserWarning。能落到这里的通常是 provider 自身
        # 的构造缺陷，所以说的是"构造失败"而不是"配置写错"。
        raise ValueError(f"构造模型 {model_config.name!r}（{model_config.use}）失败：{err}") from err


__all__ = ["create_chat_model"]
