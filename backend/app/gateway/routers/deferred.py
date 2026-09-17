"""显式延期清单。

文档 §6 的接口并非都要在阶段 A 落地。**不做**和**没做但假装做了**是两件事：
前者调用方一次就知道该改方案，后者会在上线后以"偶发丢数据"的形式暴露出来。
所以这里把每一项按文档中的方法注册成 501，而不是让它落进"未知路径 404"。

按**文档中的方法**注册是刻意的：DELETE 注册成 GET 的话，``DELETE /profiles``
会先撞上方法不匹配，客户端拿到 405 —— 那是在说"这个方法用错了"，而事实是
"这个能力还没有"。405 与 501 的区别正是这个文件存在的意义。

这个文件的 docstring 就是延期清单本身。``application/views.py`` 的
``CAPABILITIES`` 是它的对偶，``tests/test_gateway_v1.py`` 交叉校验两者。

## 清单

- 幂等：``Idempotency-Key`` 头（在 runs 路由里直接拒绝，不是端点）
- 取消运行：阶段 C（取消与完成的竞争、半写 checkpoint 的归属）
- 出生资料：``profiles``，阶段 D
- 命盘制品：``charts/{id}``、``charts/{id}/versions``，阶段 D
- 异步删除：``deletions/{id}``，阶段 D
- 事件流缺口：``stream.gap`` —— 阶段 A 的日志不裁剪，因此永不合法触发，
  没有对应的端点可注册
"""

from __future__ import annotations

from fastapi import APIRouter

from app.domain.errors import NotImplementedYet

router = APIRouter(tags=["deferred"])

_PROFILES = "出生资料接口将在阶段 D 提供。"
_CHARTS = "命盘制品接口将在阶段 D 提供。"
_DELETIONS = "异步删除接口将在阶段 D 提供。"
_CANCEL = "取消运行将在阶段 C 提供；当前请等待本轮结束或关闭页面。"


def _defer(path: str, message: str, *methods: str) -> None:
    """注册若干个方法到同一路径，全部返回 501。"""
    for method in methods:
        router.add_api_route(
            path,
            _stub(message),
            methods=[method],
            status_code=501,
            name=f"deferred:{method.lower()}:{path}",
        )


def _stub(message: str):
    async def handler() -> None:
        raise NotImplementedYet(message)

    return handler


# --- 出生资料（§7，阶段 D） ------------------------------------------------- #
_defer("/api/v1/profiles", _PROFILES, "GET", "POST")
_defer("/api/v1/profiles/{profile_id}", _PROFILES, "GET", "PATCH", "DELETE")
_defer("/api/v1/profiles/{profile_id}/confirm", _PROFILES, "POST")

# --- 命盘制品（§6，阶段 D） ------------------------------------------------- #
_defer("/api/v1/charts/{chart_id}", _CHARTS, "GET")
_defer("/api/v1/charts/{chart_id}/versions", _CHARTS, "GET")

# --- 异步删除（§6，阶段 D） ------------------------------------------------- #
_defer("/api/v1/deletions/{deletion_id}", _DELETIONS, "GET")

# --- 取消运行（§5.3，阶段 C） ----------------------------------------------- #
_defer("/api/v1/conversations/{conversation_id}/runs/{run_id}/cancel", _CANCEL, "POST")


__all__ = ["router"]
