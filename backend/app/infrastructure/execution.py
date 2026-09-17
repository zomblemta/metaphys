"""harness 适配器 —— 应用层与 metaphys RunService 之间唯一的接触面。

## 为什么单独一个模块

原来的路由直接写 ``runner._graph.checkpointer.adelete_thread(...)``：一个 HTTP
处理器伸手穿过两层抽象去改图内部字段。后果不是"代码不好看"，而是**换了
checkpointer 实现就得改路由**，而且路由从此可以顺手做任何事。

所以这里有一条机械可查的规则（由 ``tests/test_app_layers.py`` 强制）：
``_graph`` 与 ``checkpointer`` 这两个名字只允许出现在本模块。

## 鸭子类型，不是 isinstance

集成测试注入的是替身：``SimpleNamespace(_graph=None)``、只实现
``astream_run`` 的裸类。它们没有继承任何东西，因此这里一律用 ``getattr``
探测能力，不做类型判断 —— 那段探测同时也就是"图是否已构造"的判断。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)


class HarnessAgentExecutor:
    """把一个 RunService（或它的替身）适配成 ``AgentExecutor``。"""

    def __init__(self, service: Any) -> None:
        self._service = service

    def warm(self) -> None:
        """启动时强制构造图，让配置/模型错误在启动阶段暴露而不是第一轮请求。

        ``getattr`` 而不是直接访问，是因为替身可能只有 ``astream_run``。
        """
        if getattr(self._service, "_graph", None) is None:
            getattr(self._service, "graph", None)

    async def execute(self, *, run_id: str, thread_id: str, message: str) -> AsyncIterator[dict[str, Any]]:
        """跑一轮并把 harness 的原始事件逐个交出去。

        原始事件**不直接转发给客户端**：映射成公开事件是应用层的事
        （``application/runs.py``）。这里只顺带校验一件事 —— harness 报的
        ``run_id`` 与应用生成的是否一致。两层各生成一个 id 时，日志、审计与
        事件会指向三个不同的轮次，排查时无从下手，所以不一致要留下痕迹。
        """
        async for event in self._service.astream_run(message, thread_id=thread_id, run_id=run_id):
            reported = event.get("run_id")
            if reported is not None and reported != run_id:
                logger.warning("harness 回报的 run_id 与应用不一致，以应用为准：应用=%s harness=%s", run_id, reported)
            yield event

    async def delete_execution(self, thread_id: str) -> None:
        """删除该线程的 checkpoint。

        图未构造时直接返回：为了删一个 checkpoint 去把图和模型建起来，
        代价远大于收益，而且删除本来就该是幂等的。
        """
        if getattr(self._service, "_graph", None) is None:
            return
        checkpointer = getattr(getattr(self._service, "graph", None), "checkpointer", None)
        if checkpointer is not None:
            await checkpointer.adelete_thread(thread_id)


__all__ = ["HarnessAgentExecutor"]


async def migrate_executions(dsn: str) -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()


@asynccontextmanager
async def persistent_executor(dsn: str):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from metaphys.agents.lead_agent.agent import make_lead_agent
    from metaphys.runtime import RunService

    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        yield HarnessAgentExecutor(RunService(graph=make_lead_agent(checkpointer=saver)))
