"""lead agent —— 装配（:mod:`.agent`）与提示词（:mod:`.prompt`）。"""

from metaphys.agents.lead_agent.agent import (
    AGENT_NAME,
    build_middlewares,
    make_lead_agent,
    validate_middleware_order,
)
from metaphys.agents.lead_agent.prompt import SYSTEM_PROMPT, apply_prompt_template

__all__ = [
    "AGENT_NAME",
    "SYSTEM_PROMPT",
    "apply_prompt_template",
    "build_middlewares",
    "make_lead_agent",
    "validate_middleware_order",
]
