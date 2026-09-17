"""出生信息处理 —— ``bazi_chart`` 与 ``astro_chart`` 共用的那一半。

两个工具吃的是同一份输入（出生时间 + 出生地 + 称呼），失败方式也同一批
（时间说不清、地名有歧义、地名查不到）。各写一份的话，两边对"什么叫有歧义"
迟早会给出不同答案 —— 用户先排八字被追问一次、再排星盘又被追问一次，
问法还不一样。

所以判据只在这里写一遍，两个工具都调它。:mod:`metaphys.tools.builtins.geo`
里的 :func:`~metaphys.tools.builtins.geo.resolve_place` 出于同一个理由存在。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command

from metaphys.agents.messages import message_text
from metaphys.tools.builtins.geo import resolve_place
from metaphys.tools.types import Runtime


def needs_clarification(
    reason: str,
    *,
    suggested_question: str,
    tool_call_id: str,
    missing_fields: list[str] | None = None,
    must_not_produce: str,
) -> Command:
    """构造"未出盘 + 请去追问"的消息。

    刻意**不**用 ``goto=END``：提问由模型经 ``ask_clarification`` 发出，这样
    它能把若干缺失项合并成一句自然的话，也能用用户的母语追问。

    Args:
        reason: 为什么排不出来，会直接给模型看。
        suggested_question: 建议模型去问的问题。
        tool_call_id: 对应的工具调用。
        missing_fields: 缺失字段名，供前端做结构化提示。
        must_not_produce: 在拿到完整信息前**禁止**产出的内容，如
            「四柱、五行或神煞」。必须逐工具指定 —— 若写成一句通用的
            "不要编造"，模型对"什么算编造"的理解会随上下文漂移。
    """
    content = f"未出盘：{reason}\n\n建议向用户提问：{suggested_question}\n请调用 ask_clarification 提出该问题。"
    if must_not_produce:
        content += f"**在拿到完整信息之前，不要给出任何{must_not_produce}内容。**"

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=tool_call_id,
                    artifact={
                        "status": "needs_clarification",
                        "suggested_question": suggested_question,
                        "missing_fields": list(missing_fields or []),
                    },
                )
            ]
        }
    )


def parse_birth_datetime(raw: str) -> tuple[datetime | None, bool]:
    """解析 ISO 8601 出生时间，返回 ``(时刻, 是否含时分)``。

    只给日期（``1990-06-15``）时不含时分，调用方据此决定降级还是拒绝 ——
    否则会拿 00:00 当真实出生时刻，凭空造出一个时柱或一个上升点。
    """
    text = raw.strip()
    if not text:
        return None, False
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    return moment, ("T" in text or ":" in text)


def confirms_birth_time(runtime: Runtime | None, moment: datetime) -> bool:
    """只接受当前用户消息中的明确确认，不采信模型参数或历史确认。

    提供可复制的确认句，避免对“是/不是/大概”等自然语言作不可靠的猜测。
    """
    state = getattr(runtime, "state", None) or {}
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            text = message_text(message).strip().replace("：", ":")
            prefix = "确认出生时间:"
            if not text.startswith(prefix):
                return False
            confirmed, has_time = parse_birth_datetime(text[len(prefix) :].strip())
            return has_time and confirmed == moment and confirmed.tzinfo is None
    return False


def prior_birth_profile(runtime: Runtime | None) -> dict[str, Any] | None:
    """取上一次排盘的 birth_profile —— 支撑"接着上次的盘继续聊"。"""
    state = getattr(runtime, "state", None) or {}
    profile = state.get("birth_profile") if isinstance(state, dict) else None
    return profile if isinstance(profile, dict) else None


def resolve_birth_place(place: str, runtime: Runtime | None) -> tuple[tuple[float, float] | None, str, list[Any], str]:
    """把地名解析成唯一坐标。

    地名判据全部委托给 :func:`~metaphys.tools.builtins.geo.resolve_place` ——
    与 ``lookup_birthplace`` 工具共用同一套规则，两处不会对"是否有歧义"给出
    不同答案。

    Returns:
        ``(坐标, 规范地名, 待消歧的候选, 说明)``。坐标为空即未解析成功，
        此时 ``说明`` 就是要转述给用户的理由。
    """
    if not place.strip():
        # 多轮对话里用户不会反复报出生地，沿用上一次排盘的结果。
        prior = prior_birth_profile(runtime)
        if prior and prior.get("place") and "latitude" in prior and "longitude" in prior:
            return (
                (float(prior["latitude"]), float(prior["longitude"])),
                str(prior["place"]),
                [],
                "",
            )

    resolution = resolve_place(place)
    if resolution.point is not None:
        point = resolution.point
        return (point.latitude, point.longitude), point.display_name, [], resolution.note
    return None, "", resolution.candidates, resolution.note


__all__ = [
    "needs_clarification",
    "parse_birth_datetime",
    "prior_birth_profile",
    "resolve_birth_place",
]
