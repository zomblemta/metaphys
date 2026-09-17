"""``RunsService`` 的行为契约：准入矩阵、提交顺序、事件日志与关闭。

这些性质**只能在服务层验**。从 HTTP 上看，它们全都表现为"界面偶尔怪一下"：
准入顺序错了，用户在不同边界下收到的是另一句提示；提交顺序错了，
"收到 final 之后立刻查询"会拿到 running；日志没关，订阅者会一直挂到进程死 ——
而最后这一条在真实浏览器里根本看不出来（谁会盯着一个不动的标签页）。

所以这里直接对着服务验，用真实的内存仓储（它们同时也是被测对象）。

异步一律走 ``asyncio.run``：本仓库不装 pytest-asyncio，既有测试也是这个写法
（见 :mod:`tests.test_run_service`）。为一个测试文件引入一个插件，代价是
给整个测试套件加了一条新的、看不见的运行时规则。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.application import messages
from app.application.conversations import ConversationService
from app.application.runs import RunsService, has_active_run
from app.domain.errors import Conflict, IllegalTransition, InvalidInput, NotFound, RunCapacity
from app.domain.models import Run, RunStatus, transition
from app.infrastructure.memory import RunEventLog, RunRepository, SessionStore
from app.settings import Settings


class FakeExecutor:
    """按脚本吐事件的执行器替身，签名与 ``HarnessAgentExecutor`` 一致。

    用完整的关键字签名而不是 ``**kwargs``：签名对不上时这里会立刻炸，
    而不是等到某天线上才发现端口和实现说的不是一回事。
    """

    def __init__(self, frames: list[dict] | None = None, *, exc: Exception | None = None, hang: bool = False):
        self.frames = frames if frames is not None else [_final("你好")]
        self.exc = exc
        self.hang = hang
        self.seen: list[dict] = []
        self.deleted: list[str] = []

    async def execute(self, *, run_id: str, thread_id: str, message: str):
        self.seen.append({"run_id": run_id, "thread_id": thread_id, "message": message})
        if self.exc is not None:
            raise self.exc
        for frame in self.frames:
            yield frame
        if self.hang:
            await asyncio.sleep(30)

    async def delete_execution(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


def _final(reply: str) -> dict:
    return {"event": "final", "data": {"reply": reply, "charts": {}}}


def _update(node: str = "model") -> dict:
    return {"event": "update", "node": node, "data": {"status": "running"}}


@pytest.fixture
def env():
    """一整套真实的内存仓储 + 一个可控的执行器。"""
    sessions = SessionStore()
    runs = RunRepository()
    events = RunEventLog()
    executor = FakeExecutor()
    service = RunsService(sessions=sessions, runs=runs, events=events, executor=executor, settings=Settings())
    conversations = ConversationService(sessions=sessions, runs=runs, events=events, executor=executor)
    session = sessions.create()
    return SimpleNamespace(
        sessions=sessions,
        runs=runs,
        events=events,
        executor=executor,
        service=service,
        conversations=conversations,
        session=session,
        conversation=sessions.add_conversation(session),
    )


async def drain(service: RunsService, run_id: str) -> list:
    """跑完并收齐事件。

    用事件流而不是 ``await task``：日志只在**终态**才关闭，所以"流结束"
    等价于"运行已落定"。轮询状态会有竞态，而这里没有。
    """
    return [event async for event in service.subscribe(run_id, 0)]


async def _next_after(service: RunsService, run_id: str, seq: int):
    async for event in service.subscribe(run_id, seq):
        return event
    return None


# --------------------------------------------------------------------------- #
# 状态机
# --------------------------------------------------------------------------- #


def test_state_machine_rejects_illegal_moves():
    """终态不可再迁移 —— "取消成功后不得再发布成功 final"靠的就是它。"""
    run = Run()
    transition(run, RunStatus.RUNNING)
    transition(run, RunStatus.CANCELLED)
    assert run.is_terminal
    with pytest.raises(IllegalTransition):
        transition(run, RunStatus.SUCCEEDED)


def test_state_machine_stamps_timestamps():
    run = Run()
    assert run.started_at is None and run.finished_at is None
    transition(run, RunStatus.RUNNING)
    assert run.started_at is not None
    transition(run, RunStatus.SUCCEEDED)
    assert run.finished_at is not None


# --------------------------------------------------------------------------- #
# 准入矩阵
# --------------------------------------------------------------------------- #


def test_start_marks_the_run_and_the_projection_together(env):
    async def scenario():
        run = await env.service.start(env.session, env.conversation.id, "排盘")
        assert run.status is RunStatus.QUEUED
        # 权威状态与阶段 A 兼容投影同时置位 —— 分两处写就会有一天只写一边。
        assert env.conversation.busy is True
        assert env.conversation.active_run_id == run.id
        assert env.conversation.latest_run_id == run.id
        assert env.conversation.title == "排盘"
        assert env.conversation.messages == [{"role": "user", "content": "排盘"}]
        await drain(env.service, run.id)

    asyncio.run(scenario())


def test_busy_projection_alone_blocks_a_new_run(env):
    """``busy=True`` 但没有任何 Run 时也必须拒绝。

    既有测试直接对 ``busy`` 赋值，所以这个方向是真的会被走到的。只信
    ``RunRepository`` 的话，这里会放行并让同一会话跑起两轮，上下文互相污染。
    """

    async def scenario():
        env.conversation.busy = True
        assert has_active_run(env.conversation, env.runs) is True
        with pytest.raises(Conflict) as exc:
            await env.service.start(env.session, env.conversation.id, "再来一轮")
        assert str(exc.value) == messages.BUSY_RUN

    asyncio.run(scenario())


def test_failed_conversation_is_frozen(env):
    async def scenario():
        env.conversation.failed = True
        with pytest.raises(Conflict) as exc:
            await env.service.start(env.session, env.conversation.id, "重试")
        assert str(exc.value) == messages.GENERIC_RUN_ERROR

    asyncio.run(scenario())


def test_round_limit_is_reported_with_its_own_message(env):
    async def scenario():
        env.conversation.messages = [{"role": "user", "content": "x"}] * 80
        with pytest.raises(Conflict) as exc:
            await env.service.start(env.session, env.conversation.id, "再来")
        assert str(exc.value) == messages.ROUND_LIMIT

    asyncio.run(scenario())


def test_global_capacity_is_capacity_not_conflict(env):
    """容量满与"这个会话忙"是两回事：前者稍后重试即可，后者要等这一轮结束。"""

    async def scenario():
        env.service._settings = replace(Settings(), max_concurrent_runs=1)
        env.executor.hang = True
        first = await env.service.start(env.session, env.conversation.id, "第一轮")
        await asyncio.sleep(0.01)  # 让任务真正开始，占用名额

        other = env.sessions.add_conversation(env.session)
        with pytest.raises(RunCapacity) as exc:
            await env.service.start(env.session, other.id, "第二轮")
        assert exc.value.retryable is True

        await env.service.shutdown(0.05)
        assert first.is_terminal

    asyncio.run(scenario())


def test_unknown_conversation_is_not_found(env):
    async def scenario():
        with pytest.raises(NotFound):
            await env.service.start(env.session, "no-such-conversation", "你好")

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 提交顺序与事件日志
# --------------------------------------------------------------------------- #


def test_result_is_visible_before_the_final_event(env):
    """收到 ``run.final`` 的**那一刻**，会话已经可以查到结果。

    这是把文档 §5.1.6 的顺序变成可断言的事实：顺序反过来的话，前端拿到
    final 后立刻查详情会看到 running，然后要么轮询要么显示空白。
    """

    async def scenario():
        env.executor.frames = [_update(), _final("命盘已排出。")]
        run = await env.service.start(env.session, env.conversation.id, "排盘")

        observed = None
        async for event in env.service.subscribe(run.id, 0):
            if event.type == "run.final":
                observed = (
                    run.status,
                    env.conversation.result,
                    env.conversation.busy,
                    env.conversation.messages[-1],
                )

        assert observed is not None, "没有收到 run.final"
        status, result, busy, last_message = observed
        assert status is RunStatus.SUCCEEDED
        assert result == {"reply": "命盘已排出。", "charts": {}}
        assert busy is False
        assert last_message == {"role": "assistant", "content": "命盘已排出。"}

    asyncio.run(scenario())


def test_events_are_contiguous_and_replayable(env):
    async def scenario():
        env.executor.frames = [_update("a"), _update("b"), _final("好了")]
        run = await env.service.start(env.session, env.conversation.id, "排盘")
        events = await drain(env.service, run.id)

        assert [event.seq for event in events] == list(range(1, len(events) + 1))
        assert events[0].type == "run.accepted"
        assert events[1].type == "run.started"
        assert events[-2].type == "run.final"
        assert events[-1].type == "run.end"
        assert all(event.run_id == run.id for event in events)

        # 中途游标重放：从第 2 条之后续读，不重复也不漏。
        replay = [event async for event in env.service.subscribe(run.id, 2)]
        assert [event.seq for event in replay] == [event.seq for event in events[2:]]

    asyncio.run(scenario())


def test_prepare_subscription_rejects_a_foreign_cursor(env):
    async def scenario():
        run = await env.service.start(env.session, env.conversation.id, "你好")
        await drain(env.service, run.id)

        assert env.service.prepare_subscription(env.session, env.conversation.id, run.id, None) == 0
        assert env.service.prepare_subscription(env.session, env.conversation.id, run.id, f"{run.id}:3") == 3
        with pytest.raises(InvalidInput):
            env.service.prepare_subscription(env.session, env.conversation.id, run.id, "other-run:3")
        with pytest.raises(InvalidInput):
            env.service.prepare_subscription(env.session, env.conversation.id, run.id, "不是游标")

    asyncio.run(scenario())


def test_subscribing_to_a_finished_run_ends_instead_of_hanging(env):
    async def scenario():
        run = await env.service.start(env.session, env.conversation.id, "你好")
        await drain(env.service, run.id)
        # 终态之后日志已关闭：再订一次要立刻 EOF，而不是一直等新事件。
        again = await asyncio.wait_for(drain(env.service, run.id), timeout=1)
        assert again[-1].type == "run.end"

    asyncio.run(scenario())


def test_two_subscribers_both_see_each_event(env):
    """每个订阅者一个唤醒事件。

    共用一个 ``asyncio.Event`` 的话，先醒来的订阅者 ``clear()`` 会让另一个
    永远等下去 —— 症状是"同时开两个标签页，只有一个会动"。
    """

    async def scenario():
        env.executor.hang = True
        run = await env.service.start(env.session, env.conversation.id, "你好")
        await asyncio.sleep(0)

        a = asyncio.create_task(_next_after(env.service, run.id, 0))
        b = asyncio.create_task(_next_after(env.service, run.id, 0))
        await asyncio.sleep(0.02)

        got = await asyncio.wait_for(asyncio.gather(a, b), timeout=1)
        assert [event.type for event in got] == ["run.accepted", "run.accepted"]

        await env.service.shutdown(0.05)

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 失败、超时与关闭
# --------------------------------------------------------------------------- #


def test_missing_final_fails_the_run_rather_than_reporting_success(env):
    async def scenario():
        env.executor.frames = [_update()]  # 没有 final
        run = await env.service.start(env.session, env.conversation.id, "你好")
        events = await drain(env.service, run.id)

        assert run.status is RunStatus.FAILED
        assert run.error_code == "run_failed"
        assert events[-1].type == "run.end"
        assert events[-2].data["message"] == messages.GENERIC_RUN_ERROR
        assert env.conversation.failed is True and env.conversation.busy is False

    asyncio.run(scenario())


def test_executor_exception_never_leaks_its_text(env):
    async def scenario():
        env.executor.exc = RuntimeError("PRIVATE_KEY_OR_BIRTH_DATA")
        run = await env.service.start(env.session, env.conversation.id, "你好")
        events = await drain(env.service, run.id)

        assert all("PRIVATE_KEY" not in str(event.data) for event in events)
        assert events[-1].data == {"status": "failed"}

    asyncio.run(scenario())


def test_timeout_gets_its_own_error_code(env):
    async def scenario():
        env.service._settings = replace(Settings(), run_timeout=0.01)
        env.executor.hang = True
        run = await env.service.start(env.session, env.conversation.id, "你好")
        events = await drain(env.service, run.id)

        assert run.status is RunStatus.TIMED_OUT
        assert run.error_code == "run_timeout"
        assert events[-2].data["error_code"] == "run_timeout"

    asyncio.run(scenario())


def test_shutdown_settles_inflight_runs_as_cancelled(env):
    """关闭时在途运行结算为 cancelled，且**日志必须关闭**。

    不关日志的话，订阅者收不到 ``run.end``，会一直挂到进程结束 —— 而那时
    客户端看到的是一条永远不结束的流，不是一次干净的断开。
    """

    async def scenario():
        env.executor.hang = True
        run = await env.service.start(env.session, env.conversation.id, "你好")
        await asyncio.sleep(0.01)

        await env.service.shutdown(0.05)

        assert run.status is RunStatus.CANCELLED
        assert env.conversation.busy is False
        # 取消不是用户的会话出了问题：不能冻结它。
        assert env.conversation.failed is False
        assert env.conversation.active_run_id is None

        events = await drain(env.service, run.id)
        assert events[-2].type == "run.cancelled"
        assert events[-1].type == "run.end"

    asyncio.run(scenario())


def test_shutdown_leaves_no_active_run_even_with_no_grace_period(env):
    """``shutdown`` 返回后不存在活跃运行 —— 这是它的**性质**，不是运气。

    任务如果在跑起来之前就被取消，``_execute`` 里那个 ``except
    CancelledError`` 根本不会执行：运行会永远停在 ``queued``，日志永远不关，
    而订阅者等的是 ``run.end``。``timeout=0`` 把那个窗口放到最大。
    """

    async def scenario():
        env.executor.hang = True
        run = await env.service.start(env.session, env.conversation.id, "你好")

        await env.service.shutdown(0)

        assert env.runs.active() == []
        assert run.is_terminal, f"关停后运行仍停在 {run.status}"
        assert env.conversation.busy is False
        assert env.conversation.failed is False
        # 日志已终结：再订阅要立刻 EOF，而不是挂住。
        events = await asyncio.wait_for(drain(env.service, run.id), timeout=1)
        assert events[-1].type == "run.end"

    asyncio.run(scenario())


def test_run_id_is_handed_to_the_executor(env):
    """应用生成的 run_id 必须原样传下去 —— 否则两层各有一个 id，无从对照。"""

    async def scenario():
        run = await env.service.start(env.session, env.conversation.id, "排盘")
        await drain(env.service, run.id)

        assert env.executor.seen == [{"run_id": run.id, "thread_id": env.conversation.id, "message": "排盘"}]

    asyncio.run(scenario())


def test_delete_refuses_while_a_run_is_active_and_clears_everything_after(env):
    """删除的判定顺序：所有权 → 活跃运行 → 清理。

    反过来先清 checkpoint 再判活跃运行，删到一半发现删不了，会话就处在
    既没删掉也没保住的状态。
    """

    async def scenario():
        env.executor.hang = True
        run = await env.service.start(env.session, env.conversation.id, "你好")
        await asyncio.sleep(0.01)

        with pytest.raises(Conflict) as exc:
            await env.conversations.delete(env.session, env.conversation.id)
        assert str(exc.value) == messages.BUSY_DELETE

        await env.service.shutdown(0.05)
        await env.conversations.delete(env.session, env.conversation.id)

        assert env.conversation.id not in env.session.conversations
        assert env.runs.get(run.id) is None
        assert env.events.after(run.id) == []
        assert env.executor.deleted == [env.conversation.id]

    asyncio.run(scenario())


def test_shutdown_closes_admission_before_waiting(env):
    async def scenario():
        from app.domain.errors import Unavailable

        env.executor.hang = True
        run = await env.service.start(env.session, env.conversation.id, "第一轮")
        other = env.sessions.add_conversation(env.session)
        shutdown = asyncio.create_task(env.service.shutdown(0.01))
        await asyncio.sleep(0)
        with pytest.raises(Unavailable):
            await env.service.start(env.session, other.id, "不能进入")
        assert other.messages == []
        assert env.runs.ids_for(other.id) == []
        await shutdown
        assert run.is_terminal
        with pytest.raises(Unavailable):
            await env.service.start(env.session, other.id, "关闭后仍拒绝")

    asyncio.run(scenario())
