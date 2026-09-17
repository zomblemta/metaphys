"""路由依赖：取容器、认身份、验写权。

原来这三件事定义在 ``create_app`` 的闭包里，因此只能在那样构造出来的应用里
存在 —— 想在测试里单独用一次"取当前会话"，只能把整个应用起起来。提到模块
级之后它们有了名字，也就能被单独读、单独改。

## 身份与写权是两件事

``owner`` 只回答"你是谁"，``writer`` 在它之上再回答"这一步你被允许做吗"。
读路径（看会话、取图）只要前者 —— 把两者合成一个依赖，会让读操作也要求
CSRF 头，而 GET 本来就不该带写凭证。
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request

from app.application import messages
from app.domain.errors import Forbidden, Unauthorized
from app.domain.models import Session

if TYPE_CHECKING:  # 只为标注；运行时不引入装配模块。
    from app.bootstrap import Container

#: 写操作携带的双提交 CSRF 头。
CSRF_HEADER = "x-csrf-token"


def container(request: Request) -> Container:
    """取本次应用实例的容器。"""
    return request.app.state.container


def owner(request: Request) -> Session:
    """当前会话。没有或已过期时 401。"""
    state = container(request)
    session = state.sessions.find(request.cookies.get(state.cookie_name))
    if session is None:
        raise Unauthorized(messages.SESSION_EXPIRED)
    return session


def writer(request: Request, session: Annotated[Session, Depends(owner)]) -> Session:
    """当前会话，且已通过双提交 CSRF 校验。

    用 ``compare_digest`` 而非 ``==``：比的是密钥，比较耗时不该泄露前缀匹配
    的长度信息。
    """
    if not secrets.compare_digest(request.headers.get(CSRF_HEADER, ""), session.csrf):
        raise Forbidden(messages.CSRF_FAILED)
    return session


OwnerDep = Annotated[Session, Depends(owner)]
WriterDep = Annotated[Session, Depends(writer)]


__all__ = ["CSRF_HEADER", "OwnerDep", "WriterDep", "container", "owner", "writer"]
