"""会话的增删查。旧 ``/api/*`` 与 ``/api/v1/*`` **写在同一个模块里**。

两个前缀的函数体几乎一样，唯一的差别是 v1 有游标分页。这不是重复：它们
调用的是同一个服务方法，任何一处改了业务规则，另一处自动跟着变。分到两个
文件反而会让"两套逻辑"在半年后真的长出来。

``DELETE`` 返回 **204**，不是文档 §6 的 202 + ``deletions/{id}``。202 的
前提是删除是异步作业（需要持久化的重试与补偿），阶段 A 的删除是同步且
必然成功的，回 202 就是让客户端去轮询一个永远不会出现的资源。这是**记录
在案的偏离**，阶段 D 落地异步删除时在这里改回 202。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response

from app.application.views import conversation_public, conversation_view
from app.domain.errors import InvalidInput
from app.gateway.dependencies import OwnerDep, WriterDep, container

router = APIRouter(tags=["conversations"])


# --------------------------------------------------------------------------- #
# 旧 /api/* —— M4 契约，形状与状态码逐字节保持
# --------------------------------------------------------------------------- #


@router.post("/api/conversations", status_code=201)
async def new_conversation(request: Request, session: WriterDep) -> dict:
    return conversation_public(container(request).conversation_service.create(session))


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request, session: OwnerDep) -> dict:
    return conversation_public(container(request).conversation_service.get(session, conversation_id))


@router.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request, session: WriterDep) -> Response:
    await container(request).conversation_service.delete(session, conversation_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# /api/v1/*
# --------------------------------------------------------------------------- #


@router.get("/api/v1/conversations")
async def list_conversations(
    request: Request,
    session: OwnerDep,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """游标分页。

    游标非法时 400 而不是静默从头开始：客户端拿着一个坏游标继续翻页，会
    看到重复的会话，而这看起来像服务端在重复创建。
    """
    try:
        items, next_cursor = container(request).conversation_service.page(session, cursor=cursor, limit=limit)
    except ValueError as exc:
        raise InvalidInput(str(exc)) from exc
    return {"conversations": items, "next_cursor": next_cursor}


@router.post("/api/v1/conversations", status_code=201)
async def new_conversation_v1(request: Request, session: WriterDep) -> dict:
    return conversation_view(container(request).conversation_service.create(session))


@router.get("/api/v1/conversations/{conversation_id}")
async def get_conversation_v1(conversation_id: str, request: Request, session: OwnerDep) -> dict:
    return conversation_view(container(request).conversation_service.get(session, conversation_id))


@router.get("/api/v1/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    request: Request,
    session: OwnerDep,
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    return container(request).conversation_service.messages(session, conversation_id, cursor=cursor, limit=limit)


@router.delete("/api/v1/conversations/{conversation_id}", status_code=204)
async def delete_conversation_v1(conversation_id: str, request: Request, session: WriterDep) -> Response:
    await container(request).conversation_service.delete(session, conversation_id)
    return Response(status_code=204)


__all__ = ["router"]
