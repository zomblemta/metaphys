"""M5 确定性安全底线：明确高风险请求绕过模型，危险断言在输出前替换。

这是有限句式规则，不是完整语义分类器或法律合规认证。普通讨论健康、财运及规则本身
不被裸词拦截；范围与反面样本见 test_safety.py。规则不推断用户身份或诊断。
"""

import re
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from metaphys.agents.messages import message_text

RESPONSES = {
    "self_harm": (
        "听起来你现在很难受。命盘不能决定你的价值或未来，我们先把你的安全放在第一位。"
        "如果你可能马上伤害自己，请立即联系当地急救服务，或请一位信任的人现在来陪你，"
        "尽量远离可能伤害自己的物品或地点。你此刻安全吗？"
    ),
    "medical": "命盘不能诊断疾病、判断怀孕结果或决定用药。请依据实际症状与检查，向合格的医疗专业人员咨询；我们可以继续聊不涉及诊疗决定的自我探索。",
    "financial": "我不能依据命盘推荐具体买卖、下注、借贷或保证收益。这类决定需要现实财务信息与风险评估；我们可以讨论你的目标和风险承受能力，而不预测输赢。",
    "fatalism": "我不会根据命盘断言寿命、死亡时间或必然发生的灾祸，也不会用恐惧劝你付费化解。命盘不能决定一个人的命运；我们可以改为讨论你能采取的现实行动。",
    "legal": "命盘不能判断案件胜负或替代法律意见。请根据事实和证据咨询适用地区的专业法律人士，我们可以先整理你希望厘清的问题。",
}
_INPUT = {
    "self_harm": r"(?:我(?:真的|现在|已经)?(?:想|要|准备|打算)(?:自杀|自残|结束生命|跳楼)|我不想活(?:了|下去|着|[。！？!?，,]|$)|I (?:want to|am going to) (?:kill myself|end my life))",
    "medical": r"(?:(?:命盘|八字|星盘|算命).{0,24}(?:得了什么病|有没有癌症|会不会得癌|是否怀孕|能否怀孕|胎儿是否健康|能不能停药|要不要停药)|(?:根据|按).{0,12}(?:八字|星盘).{0,20}(?:诊断|用药|治疗))",
    "financial": r"(?:(?:八字|星盘|命盘|算命).{0,30}(?:买哪只股票|买什么股票|该买哪|该不该买|什么时候买入|什么时候卖出|下注|借贷|贷款投资|稳赚)|(?:算|预测).{0,10}(?:彩票号码|股票涨跌|赌博输赢))",
    "fatalism": r"(?:(?:算|预测|告诉我).{0,16}(?:什么时候死|哪年死|寿命|死亡时间)|(?:付费|花钱|买符).{0,12}(?:化解灾祸|消灾|改命))",
    "legal": r"(?:八字|星盘|命盘|算命).{0,24}(?:官司输赢|会不会坐牢|能否胜诉|判几年)",
}
_OUTPUT = {
    "medical": r"(?:你(?:肯定|一定|已经)?患有(?:癌症|肿瘤|抑郁症)|(?:你应该|建议你|你可以)(?:停止服药|停药|不用看医生))",
    "financial": r"(?:(?:建议你|你应该|你必须).{0,12}(?:买入|满仓|加杠杆|下注)|(?:保证|必定|一定)(?:获利|盈利|赚钱|翻倍))",
    "fatalism": r"(?:你.{0,12}(?:将在|会在).{0,12}(?:死亡|死去)|你.{0,8}(?:克夫|克妻|注定有灾)|(?:必须|一定要).{0,12}(?:付费|花钱|买符).{0,12}(?:化解|消灾|改命))",
    "legal": r"(?:你(?:肯定|一定|必定)(?:会胜诉|会坐牢)|保证.{0,8}胜诉)",
}


def category(text: str, *, output: bool = False) -> str | None:
    for name, pattern in (_OUTPUT if output else _INPUT).items():
        for clause in re.split(r"[，。！？；\n]|但是|但请", text):
            for match in re.finditer(pattern, clause, re.IGNORECASE):
                # 否定只作用于当前匹配的紧邻前缀，不让前一句“不建议”放行后一句指令。
                prefix = clause[: match.start()]
                if name != "self_harm" and re.search(r"(?:不要|不能|不应|禁止|不会)(?:说|断言|声称)?\s*$", prefix):
                    continue
                return name
    return None


def redirect(name: str, message: AIMessage | None = None) -> AIMessage:
    if message is None:
        return AIMessage(RESPONSES[name], additional_kwargs={"safety_categories": [name]})
    return message.model_copy(
        update={"content": RESPONSES[name], "tool_calls": [], "additional_kwargs": {"safety_categories": [name]}}
    )


class SafetyMiddleware(AgentMiddleware):
    """wrap_model 阶段控制用户可见输出；after_model 只把安全事件追加到审计。"""

    @staticmethod
    def _input(request) -> AIMessage | None:
        for message in reversed(request.messages):
            if isinstance(message, HumanMessage):
                found = category(message_text(message))
                return redirect(found) if found else None
        return None

    @staticmethod
    def _output(response):
        messages = response.result if isinstance(response, ModelResponse) else [response]
        checked = []
        for message in messages:
            found = category(message_text(message), output=True) if isinstance(message, AIMessage) else None
            checked.append(redirect(found, message) if found else message)
        return replace(response, result=checked) if isinstance(response, ModelResponse) else checked[0]

    def wrap_model_call(self, request, handler):
        return self._input(request) or self._output(handler(request))

    async def awrap_model_call(self, request, handler):
        blocked = self._input(request)
        return blocked if blocked is not None else self._output(await handler(request))

    def after_model(self, state: dict[str, Any], runtime: Any):
        for message in reversed(state.get("messages") or []):
            if isinstance(message, AIMessage):
                flags = message.additional_kwargs.get("safety_categories", [])
                return {"safety_flags": flags} if flags else None
        return None

    async def aafter_model(self, state: dict[str, Any], runtime: Any):
        return self.after_model(state, runtime)
