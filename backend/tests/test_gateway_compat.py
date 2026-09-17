"""旧 /api/* 的兼容契约：帧的翻译、错误形状、请求 id 与关停结算。

``tests/test_gateway.py`` 是这层的**基线**（逐字节不改，16 条）。这里补的是
它没覆盖、但本次改动**可能**碰坏的部分：

- 帧的翻译规则变了（旧接口现在是 v1 事件的一种呈现），于是"旧帧数量 ==
  harness 的 update 数"和"终态帧之后立即 EOF"需要重新钉一次。
- 错误码来源变了（领域错误经一张表映射），于是旧客户端读的 ``detail``
  必须在四个不同的状态码上仍然存在。
- 关停语义变了（原来是把所有任务 cancel 掉就完事），于是在途运行的结算
  需要被验：取消**不是**失败，会话不能因此被冻结。
"""

from __future__ import annotations

import asyncio
import json
import threading

from fastapi.testclient import TestClient

from app.domain.models import RunStatus
from app.gateway.main import create_app
from app.infrastructure.memory import SessionStore


class Scripted:
    """按脚本吐事件的执行器替身。``_graph = None`` 是替身的既有约定。"""

    _graph = None

    def __init__(self, *nodes: str, reply: str = "命盘已排出。"):
        self.nodes = nodes or ("model",)
        self.reply = reply

    async def astream_run(self, message, **kwargs):
        for node in self.nodes:
            yield {"event": "update", "node": node, "data": {"status": "running"}}
        yield {"event": "final", "data": {"reply": self.reply, "charts": {}}}


def client_for(service, **kwargs):
    return TestClient(create_app(service=service, **kwargs), base_url="http://localhost")


def bootstrap(client) -> dict[str, str]:
    return {"x-csrf-token": client.get("/api/session").json()["csrf_token"]}


def conversation(client, headers) -> str:
    response = client.post("/api/conversations", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def frames(response) -> list[dict]:
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


# --------------------------------------------------------------------------- #
# 帧的翻译
# --------------------------------------------------------------------------- #


def test_legacy_frame_count_equals_harness_update_count():
    """旧帧数 == harness 的 ``update`` 数，一个不多一个不少。

    v1 事件比旧帧多四种（accepted / started / cancelled / end）。任何一个
    漏成旧帧，``tests/test_gateway.py`` 里"除最后一帧外 data 恰为
    ``{"status":"running"}``"那条就会红 —— 但红在别处，看不出是谁。
    这里直接对着数量断言，坏了能一眼定位。
    """
    with client_for(Scripted("model", "tools", "model")) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        result = frames(client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "排盘"}))

        assert len(result) == 4  # 3 个 update + 1 个 final
        assert [frame["event"] for frame in result] == ["update", "update", "update", "final"]
        assert all(frame["data"] == {"status": "running"} for frame in result[:-1])


def test_nothing_follows_the_terminal_frame():
    """终态帧之后必须立即 EOF —— 旧客户端靠"流结束"判断这一轮完了."""
    with client_for(Scripted("model")) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        result = frames(client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "排盘"}))
        assert result[-1]["event"] == "final"

        # v1 里终态之后还有 run.end；旧接口不该看到它。
        assert not any(frame["event"] == "end" for frame in result)
        assert all(frame["event"] in {"update", "final"} for frame in result)


def test_legacy_frames_now_carry_a_run_id():
    """旧帧增量带上 ``run_id``：只增不改，旧读取方不受影响。"""
    with client_for(Scripted()) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        result = frames(client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "排盘"}))
        run_ids = {frame.get("run_id") for frame in result}
        assert len(run_ids) == 1 and run_ids != {None}


def test_failure_frame_is_generic_and_has_no_run_id_leak():
    class Broken:
        _graph = None

        async def astream_run(self, *args, **kwargs):
            raise RuntimeError("PRIVATE_KEY_OR_BIRTH_DATA")
            yield

    with client_for(Broken()) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        response = client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "你好"})
        result = frames(response)

        assert result[-1]["event"] == "error"
        assert set(result[-1]["data"]) == {"message"}
        assert "PRIVATE_KEY" not in response.text


# --------------------------------------------------------------------------- #
# 错误形状
# --------------------------------------------------------------------------- #


def test_legacy_errors_keep_the_detail_field():
    """四个状态码的响应体都还要有 ``detail`` —— 前端 ``lib/api.ts`` 读它。

    ``422`` 走的是 ``RequestValidationError``，``409`` 走领域错误，
    ``403`` 走中间件，``413`` 也走中间件。四条来源不同，所以四条都要验：
    改了一处而另一处没改，就只有一个端点上的报错会变成空白提示。
    """
    from app.settings import Settings

    store = SessionStore()
    with client_for(Scripted(), store=store) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)

        # 403：缺 CSRF 头（中间件在业务之前拦下）
        forbidden = client.post("/api/conversations")
        assert forbidden.status_code == 403 and "detail" in forbidden.json()

        # 422：消息不合法（Pydantic → RequestValidationError）
        invalid = client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": ""})
        assert invalid.status_code == 422 and "detail" in invalid.json()

        # 413：正文超过字节预算（中间件，不读正文）
        oversized = client.post(
            f"/api/conversations/{cid}/runs",
            headers=headers,
            content=b"x" * (Settings().max_body_bytes + 1),
        )
        assert oversized.status_code == 413 and "detail" in oversized.json()

        # 409：会话忙
        session = store.find(client.cookies.get("metaphys_session"))
        session.conversations[cid].busy = True
        busy = client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "你好"})
        assert busy.status_code == 409 and "detail" in busy.json()
        session.conversations[cid].busy = False


def test_request_id_is_echoed_on_the_legacy_path_too():
    with client_for(Scripted()) as client:
        response = client.get("/api/session", headers={"X-Request-ID": "trace-legacy"})
        assert response.headers["x-request-id"] == "trace-legacy"


# --------------------------------------------------------------------------- #
# 关停
# --------------------------------------------------------------------------- #


def test_shutdown_settles_an_inflight_run_without_freezing_the_conversation():
    """关停把在途运行结算为 ``cancelled``。

    取消**不是失败**：置 ``failed`` 会把会话冻住，用户下次打开只能新建 ——
    而这是服务端自己关停造成的，不该由用户承担。

    用 v1 的受理端点发起（它立即返回，不占着一条流），这样 ``with`` 退出时
    触发 lifespan 关闭，运行还悬在半路。
    """
    started = threading.Event()

    class Hanging:
        _graph = None

        async def astream_run(self, message, **kwargs):
            started.set()
            yield {"event": "update", "node": "model", "data": {"status": "running"}}
            await asyncio.sleep(30)

    from dataclasses import replace

    from app.settings import Settings

    app = create_app(service=Hanging(), settings=replace(Settings(), shutdown_timeout=0.05))
    container = app.state.container

    with TestClient(app, base_url="http://localhost") as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        accepted = client.post(f"/api/v1/conversations/{cid}/runs", headers=headers, json={"message": "你好"})
        assert accepted.status_code == 202, accepted.text
        run_id = accepted.json()["run_id"]
        assert started.wait(2), "在途运行没有真正开始，这条测试就没有验到东西"

    run = container.runs.get(run_id)
    session = next(iter(container.sessions.sessions.values()))
    conversation_ = session.conversations[cid]

    assert run.status is RunStatus.CANCELLED
    assert conversation_.busy is False
    assert conversation_.failed is False
    assert conversation_.active_run_id is None

    # 日志必须关闭：否则订阅者等不到 run.end，会一直挂到进程结束。
    assert [event.type for event in container.events.after(run_id)][-2:] == ["run.cancelled", "run.end"]
    assert container.runs.active() == []
