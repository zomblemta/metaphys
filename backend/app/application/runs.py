"""运行服务 —— 准入、生命周期、事件与订阅。

这是应用层的核心，也是原来 ``gateway/main.py`` 里 ``execute()`` 那段逻辑的
归宿。搬过来不只是换个位置，而是修掉三个原来没地方放的问题：

1. **run_id 由应用生成**。原来 harness 内部生成一个、应用看不见，于是
   "这条日志属于哪一轮"无从回答，事件也没有稳定主语。现在 id 在这里生成，
   传进 harness，写进每一个事件。
2. **事件先落日志再通知**。执行与订阅因此解耦：浏览器断开只停止订阅，
   运行照常完成并落库，重连可以按序号续读。
3. **结果只有一个写入点**。``_commit`` 是唯一同时写 run、conversation 与
   事件的函数，不会再出现两套可变历史各自追加。

## 事件顺序是有意义的

``run.final`` 在结果落库**之后**追加，``run.end`` 更后。于是"收到 final
就能立刻 ``GET runs/{id}`` 看到 succeeded"是可断言的事实，而不是约定。

## 阶段 A 的两处克制

- 不实现取消：取消与完成的竞争、以及半写 checkpoint 的归属，属阶段 C
  （文档 §5.3 / §5.2）。这里只在**进程关闭**时把在途运行结算为 cancelled。
- 失败后冻结会话：文档 §5.2 明确允许第一版保留这个限制，因为"失败 checkpoint
  不进入后续轮次"需要 per-run 的执行隔离，那是阶段 B。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.application import messages
from app.domain.errors import Conflict, InvalidInput, NotFound, RunCapacity, Unavailable
from app.domain.models import (
    Conversation,
    Run,
    RunEvent,
    RunStatus,
    Session,
    parse_cursor,
    transition,
)
from app.settings import Settings

logger = logging.getLogger(__name__)


class MissingFinalError(RuntimeError):
    """流结束了却没收到 final —— 不能把它当成一轮成功。"""


def has_active_run(conversation: Conversation, runs) -> bool:
    """该会话是否已有活跃运行。

    **两个方向都读**，不是冗余：权威状态是 ``Run``，但 ``busy`` 是阶段 A 的
    兼容投影，前端契约与既有测试会直接置位它。只信其中一边，就会在另一边
    为真时放行一个本该拒绝的请求 —— 那种错误的表现是"同一会话跑了两轮，
    上下文互相污染"，而用户看不出哪里不对。
    """
    return conversation.busy or runs.active_for(conversation.id) is not None


class RunsService:
    """一轮运行从准入到落库的全过程。"""

    def __init__(self, *, sessions, runs, events, executor, settings: Settings) -> None:
        self._sessions = sessions
        self._runs = runs
        self._events = events
        self._executor = executor
        self._settings = settings
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    # ----------------------------------------------------------------- #
    # 准入
    # ----------------------------------------------------------------- #

    async def start(self, session: Session, conversation_id: str, message: str) -> Run:
        """校验并受理一轮运行，返回已入队的 Run。

        校验顺序与 M4 一致，不能调换：``busy`` 先于 ``failed`` 先于轮次上限
        先于全局容量。顺序换了，用户在不同边界下收到的提示就变了。
        """
        if self._closing:
            raise Unavailable("服务正在关闭，请稍后重试。")
        conversation = self._sessions.owned(session, conversation_id)

        if has_active_run(conversation, self._runs):
            raise Conflict(messages.BUSY_RUN)
        if conversation.failed:
            raise Conflict(messages.GENERIC_RUN_ERROR)
        if len(conversation.messages) >= self._settings.max_messages:
            raise Conflict(messages.ROUND_LIMIT)
        if len(self._tasks) >= self._settings.max_concurrent_runs:
            raise RunCapacity("当前解读任务较多，请稍候再试。")

        run = Run(conversation_id=conversation.id, input=message)
        self._runs.add(run)

        # 阶段 A 兼容投影与权威状态在这里同时置位，且只在这里。
        conversation.busy = True
        conversation.active_run_id = run.id
        conversation.latest_run_id = run.id
        if not conversation.messages:
            conversation.title = message[:28]
        conversation.messages.append({"role": "user", "content": message})
        conversation.touch()

        self._events.append(run.id, "run.accepted", {"status": "queued"})

        task = asyncio.create_task(self._execute(run, conversation, message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    # ----------------------------------------------------------------- #
    # 执行
    # ----------------------------------------------------------------- #

    async def _execute(self, run: Run, conversation: Conversation, message: str) -> None:
        try:
            transition(run, RunStatus.RUNNING)
            self._events.append(run.id, "run.started", {"status": "running"})

            final: dict | None = None
            async with asyncio.timeout(self._settings.run_timeout):
                # 必须把生成器**跑到底**，不能在 final 处 break：harness 在
                # yield final 之后还有收尾（脚本模型替身靠它发完成信号），
                # 提前跳出会让那段收尾永远不执行。
                async for event in self._executor.execute(run_id=run.id, thread_id=conversation.id, message=message):
                    if event.get("event") == "final":
                        final = event.get("data") or {}
                        continue
                    self._events.append(run.id, "run.progress", {"status": "running", "node": event.get("node", "")})

            if final is None:
                raise MissingFinalError("事件流结束但没有 final")
            self._commit(run, conversation, final)
        except TimeoutError:
            logger.warning("运行超时：run_id=%s", run.id)
            self._fail(run, conversation, "run_timeout")
        except asyncio.CancelledError:
            # 关闭流程取消在途运行。必须先把日志终结再抛出去，否则订阅者
            # 收不到 run.end 会一直挂到进程结束。
            self._cancel(run, conversation)
            raise
        except Exception as exc:  # noqa: BLE001 —— 任何异常都不能让会话卡在 busy
            logger.warning("运行失败：run_id=%s type=%s", run.id, type(exc).__name__)
            self._fail(run, conversation, "run_failed")

    def _commit(self, run: Run, conversation: Conversation, result: dict) -> None:
        """一轮成功结束的唯一写入点。

        结果、公开消息、会话指针、事件按同一顺序落定。分处写入正是"两套可变
        历史各自追加"的来源。
        """
        run.result = result
        transition(run, RunStatus.SUCCEEDED)
        conversation.result = result
        conversation.messages.append({"role": "assistant", "content": result.get("reply", "")})
        conversation.busy = False
        conversation.active_run_id = None
        conversation.touch()
        self._events.append(run.id, "run.final", result)
        self._finish(run)

    def _fail(self, run: Run, conversation: Conversation, error_code: str) -> None:
        if run.is_terminal:
            return
        run.error_code = error_code
        transition(run, RunStatus.TIMED_OUT if error_code == "run_timeout" else RunStatus.FAILED)
        conversation.busy = False
        conversation.active_run_id = None
        conversation.failed = True
        conversation.messages.append({"role": "error", "content": messages.GENERIC_RUN_ERROR})
        conversation.touch()
        self._events.append(
            run.id,
            "run.failed",
            {"error_code": error_code, "retryable": True, "message": messages.GENERIC_RUN_ERROR},
        )
        self._finish(run)

    def _cancel(self, run: Run, conversation: Conversation | None = None) -> None:
        """进程关闭时的结算。**不**置 failed：这不是用户的会话出了问题。

        ``conversation`` 可缺省：关停兜底路径上，运行所属的会话可能已经被
        回收，这时仍然必须把运行与事件日志结算掉 —— 留下一个永远是
        ``queued`` 的运行，比丢掉它更难排查。
        """
        if run.is_terminal:
            return
        transition(run, RunStatus.CANCELLED)
        if conversation is not None:
            conversation.busy = False
            conversation.active_run_id = None
        self._events.append(run.id, "run.cancelled", {"status": "cancelled"})
        self._finish(run)

    def _finish(self, run: Run) -> None:
        """终态收尾：追加 run.end 并终结日志。"""
        self._events.append(run.id, "run.end", {"status": str(run.status)})
        self._events.close(run.id)

    # ----------------------------------------------------------------- #
    # 查询与订阅
    # ----------------------------------------------------------------- #

    def get(self, session: Session, conversation_id: str, run_id: str) -> Run:
        conversation = self._sessions.owned(session, conversation_id)
        run = self._runs.get(run_id)
        if run is None or run.conversation_id != conversation.id:
            raise NotFound("运行不存在。")
        return run

    def prepare_subscription(self, session: Session, conversation_id: str, run_id: str, cursor: str | None) -> int:
        """校验订阅并返回起始序号。

        **必须在返回流之前调用。** 校验写在异步生成器里的话，404 与 400 会
        在响应头已经发出 200 之后才抛出来 —— 客户端拿到的是一条"成功打开
        然后莫名中断"的流，而不是一个能据以分支的错误码。

        游标必须是**这一轮**的：拿另一轮的序号来续读，会静默跳过或重放错的
        事件，而客户端无从发现。所以跨 run 的游标直接拒绝，不是忽略。
        """
        self.get(session, conversation_id, run_id)
        if not cursor:
            return 0
        try:
            cursor_run, seq = parse_cursor(cursor)
        except ValueError as exc:
            raise InvalidInput(str(exc)) from exc
        if cursor_run != run_id:
            raise InvalidInput("事件游标不属于本轮运行。")
        return seq

    async def subscribe(self, run_id: str, start: int) -> AsyncIterator[RunEvent]:
        """从序号 ``start`` 之后开始产出事件，直到日志终结。

        低层原语：**不做归属校验**，调用方必须先走 :meth:`prepare_subscription`。
        把两者分开是因为"拒绝"和"持续产出"必须发生在不同的时刻（见上）。
        """
        while True:
            event = await self._events.next_event(run_id, start)
            if event is None:
                return
            start = event.seq
            yield event

    # ----------------------------------------------------------------- #
    # 关闭
    # ----------------------------------------------------------------- #

    async def shutdown(self, timeout: float) -> None:
        """停止准入并在截止时间内结算在途运行。

        到点即取消，不逐个无限等待 —— 关不掉的进程比丢掉一轮运行更糟。

        取消之后还有一次**兜底结算**：任务如果在跑起来之前就被取消，
        ``_execute`` 里那个 ``except CancelledError`` 根本不会执行，运行会
        永远停在 ``queued``、日志永远不关 —— 而订阅者等的是 ``run.end``。
        关停返回后"没有遗留的活跃运行"因此是这条路径的**性质**，不是运气。
        """
        self._closing = True
        tasks = list(self._tasks)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        for run in self._runs.active():
            self._cancel(run)

    @property
    def active_tasks(self) -> int:
        return len(self._tasks)


__all__ = ["MissingFinalError", "RunsService", "has_active_run"]
