"""装配与生命周期 —— 全应用唯一认识具体基础设施的地方。

依赖方向是单向的：``gateway → application → domain``，``infrastructure``
实现 ``application`` 定义的端口。这个模块是唯一把它们接起来的胶水；别处
一旦直接 new 一个仓储，那条方向就破了，而破坏的方式通常不报错 —— 只是
某天换实现时要改的地方从一处变成十处。

## 启动与关闭

启动：校验配置 → 构造图（惰性图的唯一一次提前触发）→ ready。
关闭：停止准入 → 在截止时间内结算在途运行 → 取消剩余。**不逐个无限等待**：
关不掉的进程比丢掉一轮运行更糟。

## 阶段 A 的边界

单进程。文档 §5.3 要求"启动配置拒绝多 worker"，但拒绝多 worker 需要部署
配置与租约机制，属阶段 C —— 这里只是事实上的单进程，没有任何强制。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from metaphys.config import get_app_config
from metaphys.runtime import RunService

from app.application.artifacts import ArtifactService
from app.application.conversations import ConversationService
from app.application.runs import RunsService
from app.application.sessions import SessionService
from app.infrastructure.execution import HarnessAgentExecutor
from app.infrastructure.memory import RunEventLog, RunRepository, SessionStore
from app.infrastructure.storage import SvgArtifactStore
from app.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class Container:
    """一次应用实例持有的全部资源与用例服务。"""

    settings: Settings
    cookie_name: str

    sessions: SessionStore
    runs: RunRepository
    events: RunEventLog
    executor: HarnessAgentExecutor
    artifacts: SvgArtifactStore

    session_service: SessionService
    conversation_service: ConversationService
    runs_service: RunsService
    artifact_service: ArtifactService

    #: 前端构建目录。**按调用解析**：集成测试在应用构造之后替换它。
    frontend_dir: Callable[[], Path]

    #: 注入的 service 为 None 时才需要在启动阶段碰配置与模型。
    service_injected: bool = False

    #: 就绪标志。恢复未完成运行属阶段 C，因此目前只表示"启动校验通过"。
    ready: bool = False

    _background: set = field(default_factory=set)

    async def startup(self) -> None:
        """启动校验。

        只在没有注入 service 时读配置并构造图 —— 注入脚本模型的集成测试正是
        为了不需要模型配置，这里如果无条件读配置，那些测试会全部失败在启动处。
        """
        if not self.service_injected:
            get_app_config()
            self.executor.warm()
        self.ready = True

    async def shutdown(self) -> None:
        """停止准入并结算在途运行。"""
        self.ready = False
        await self.runs_service.shutdown(self.settings.shutdown_timeout)


def build_container(
    *,
    service: object | None = None,
    store: SessionStore | None = None,
    settings: Settings | None = None,
    cookie_name: str = "metaphys_session",
    frontend_dir: Callable[[], Path],
    svg_dir: Callable[[], Path],
) -> Container:
    """把配置、仓储、执行器与用例服务接到一起。

    ``service`` / ``store`` 用 ``is None`` 判断而不是 ``or``：注入的替身是
    鸭子类型的裸对象，其中一些可能定义了 ``__len__`` 而为假值，``or`` 会把
    它们悄悄换成真实实现 —— 表现为测试"通过了"但跑的是另一套代码。
    """
    settings = settings or Settings()
    sessions = SessionStore() if store is None else store
    runs = RunRepository()
    events = RunEventLog()
    executor = HarnessAgentExecutor(RunService() if service is None else service)
    artifacts = SvgArtifactStore(svg_dir)

    return Container(
        settings=settings,
        cookie_name=cookie_name,
        sessions=sessions,
        runs=runs,
        events=events,
        executor=executor,
        artifacts=artifacts,
        session_service=SessionService(sessions=sessions, runs=runs, events=events, executor=executor),
        conversation_service=ConversationService(sessions=sessions, runs=runs, events=events, executor=executor),
        runs_service=RunsService(sessions=sessions, runs=runs, events=events, executor=executor, settings=settings),
        artifact_service=ArtifactService(sessions=sessions, store=artifacts),
        frontend_dir=frontend_dir,
        service_injected=service is not None,
    )


__all__ = ["Container", "build_container"]
