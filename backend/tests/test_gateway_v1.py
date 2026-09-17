"""/api/v1 契约：信封错误、202 + 事件流、重放、分页、延期清单。

这些断言的作用是把 v1 的形状**钉住**。没有它们，v1 会在几个月里被顺手改
成"更合理的样子"，而每一次改动都不会让任何东西报错 —— 只会让某个还没写的
客户端在集成时才发现。

两处特别值得看：

- ``test_unknown_v1_path_is_an_envelope``：FastAPI 自己的 404 不经过用户
  异常处理器。不注册 ``StarletteHTTPException`` 的话，"统一错误响应"对最常
  撞上的那一类错误根本不成立。
- ``test_capabilities_and_deferred_routes_agree``：能力清单说 ``false`` 的
  每一项都必须能真的验到。清单与实现对不上时，说谎的是清单。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from metaphys.runtime import RunService

from app.application.views import CAPABILITIES, decode_cursor, encode_cursor
from app.domain.models import Conversation
from app.gateway.main import COOKIE, create_app
from app.infrastructure.memory import SessionStore

#: 能力清单里 ``false`` 的项，凡是有端点的，用这个探针去验。
DEFERRED_PROBES: dict[str, tuple[str, str]] = {
    "cancel": ("POST", "/api/v1/conversations/c1/runs/r1/cancel"),
    "profiles": ("GET", "/api/v1/profiles"),
    "charts": ("GET", "/api/v1/charts/c1"),
    "deletions": ("GET", "/api/v1/deletions/d1"),
}

#: 没有 v1 端点可探的能力。它们的 ``false`` 由**别的东西**兑现，逐条说明：
#: - ``multi_worker`` 属部署约束，阶段 A 只是单进程，无端点可拒
#: - ``stream_gap``   阶段 A 的日志不裁剪，缺口永不出现，因此永不该被触发
#: - ``idempotency``  由请求头开关，不是路径；见 test_idempotency_key_is_refused
#: - ``artifacts``    指的是 v1 的制品契约（元数据、授权、删除补偿，属阶段 D）。
#:   旧接口的 ``/api/conversations/{id}/chart.svg`` 仍在服务，所以这里的
#:   ``false`` 不是"没有制品"，而是"还没有 v1 的制品接口"
NON_ENDPOINT_CAPABILITIES = {"multi_worker", "stream_gap", "idempotency", "artifacts"}


class Scripted:
    """脚本化的执行器替身。事件形状与 harness 一致，但不依赖模型与网络。"""

    _graph = None

    def __init__(self, *nodes: str, reply: str = "命盘已排出。", charts: dict | None = None):
        self.nodes = nodes or ("model",)
        self.reply = reply
        self.charts = charts if charts is not None else {}

    async def astream_run(self, message, *, thread_id=None, run_id=None):
        for node in self.nodes:
            yield {"event": "update", "node": node, "data": {"status": "running"}}
        yield {"event": "final", "data": {"reply": self.reply, "charts": self.charts}}


def client_for(service, **kwargs):
    return TestClient(create_app(service=service, **kwargs), base_url="http://localhost")


def bootstrap(client) -> dict[str, str]:
    return {"x-csrf-token": client.get("/api/v1/session").json()["csrf_token"]}


def new_conversation(client, headers) -> str:
    response = client.post("/api/v1/conversations", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def sse(response) -> list[dict]:
    """解析 SSE 文本成 ``{id, event, data}`` 列表。心跳块没有 data，跳过。"""
    parsed = []
    for block in response.text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            name, _, value = line.partition(": ")
            if name in {"id", "event", "data"}:
                fields[name] = value
        if "data" in fields:
            fields["data"] = json.loads(fields["data"])
            parsed.append(fields)
    return parsed


def run_to_completion(client, headers, cid: str, message: str = "排盘") -> tuple[str, list[dict]]:
    """发起一轮并订阅到结束，返回 ``(run_id, 帧列表)``。"""
    accepted = client.post(f"/api/v1/conversations/{cid}/runs", headers=headers, json={"message": message})
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run_id"]
    stream = client.get(f"/api/v1/conversations/{cid}/runs/{run_id}/events", headers=headers)
    assert stream.status_code == 200, stream.text
    return run_id, sse(stream)


# --------------------------------------------------------------------------- #
# 会话与能力
# --------------------------------------------------------------------------- #


def test_session_carries_csrf_and_capabilities():
    with client_for(Scripted()) as client:
        response = client.get("/api/v1/session")
        assert response.status_code == 200
        body = response.json()
        assert body["csrf_token"]
        assert body["capabilities"] == CAPABILITIES
        # 引导响应不夹带会话列表 —— 那是 /api/v1/conversations 的事。
        assert "conversations" not in body
        assert "HttpOnly" in response.headers["set-cookie"]


def test_capabilities_and_deferred_routes_agree():
    """清单说 ``false`` 的每一项，要么能验到 501，要么在非端点集合里。

    这条测试存在的理由：``capabilities`` 是给客户端的**承诺**。它与实现
    脱节时不会有任何报错，只会让客户端按一个假前提去写。
    """
    claimed_false = {key for key, value in CAPABILITIES.items() if value is False}
    assert claimed_false == set(DEFERRED_PROBES) | NON_ENDPOINT_CAPABILITIES

    with client_for(Scripted()) as client:
        for capability, (method, path) in DEFERRED_PROBES.items():
            response = client.request(method, path)
            assert response.status_code == 501, (
                f"{capability} 声称未实现，{method} {path} 却回了 {response.status_code}"
            )
            assert response.json()["error"]["code"] == "not_implemented"


def test_deferred_error_method_is_405_not_a_fake_501():
    """延期路由按文档中的方法注册，因此错误方法得到的是 405。

    两者含义不同：405 说"这个方法用错了"，501 说"这个能力还没有"。
    把 DELETE 注册成 GET，客户端会以为能力存在而方法不对。
    """
    with client_for(Scripted()) as client:
        response = client.delete("/api/v1/profiles")
        assert response.status_code == 405
        assert "allow" in {key.lower() for key in response.headers}


# --------------------------------------------------------------------------- #
# 会话生命周期与分页
# --------------------------------------------------------------------------- #


def test_conversation_lifecycle():
    with client_for(Scripted()) as client:
        headers = bootstrap(client)
        cid = new_conversation(client, headers)

        detail = client.get(f"/api/v1/conversations/{cid}", headers=headers).json()
        assert detail["id"] == cid and detail["status"] == "active"
        assert detail["created_at"] and detail["updated_at"]
        assert "messages" not in detail  # 历史走 /messages

        page = client.get(f"/api/v1/conversations/{cid}/messages", headers=headers).json()
        assert page == {"messages": [], "next_cursor": None, "total": 0}

        assert client.delete(f"/api/v1/conversations/{cid}", headers=headers).status_code == 204
        assert client.get(f"/api/v1/conversations/{cid}", headers=headers).status_code == 404


def test_cursor_round_trips_without_losing_precision():
    """游标必须能**精确**还原 ``updated_at``。

    这里曾经用 ``f"{ts:.6f}"`` 编码，它会把小数第六位之后四舍五入。向上
    舍入时，还原出来的时间戳严格大于真实值，而下一页的过滤是
    ``(updated_at, id) < 游标`` —— 于是时间戳相同的会话全部满足条件，
    已经翻过去的又被翻回来一次。只有在同一时刻创建多个会话时才复现，
    所以它表现为"偶尔翻页重复"。
    """
    conversation = Conversation()
    conversation.updated_at = 1789230773.1169267

    restored, restored_id = decode_cursor(encode_cursor(conversation))
    assert restored == conversation.updated_at, "游标往返丢了精度，分页会重复或漏项"
    assert restored_id == conversation.id


def test_pagination_has_no_gaps_and_no_repeats():
    """游标分页必须稳定。

    三个会话的 ``updated_at`` **被强制设成同一个值** —— 那正是上面那条
    精度问题暴露出来的条件，而真实并发下它也真的会发生。排序键带上 ``id``
    才能在这种时刻仍然给出全序。
    """
    store = SessionStore()
    app = create_app(service=Scripted(), store=store)
    with TestClient(app, base_url="http://localhost") as client:
        headers = bootstrap(client)
        created = [new_conversation(client, headers) for _ in range(3)]

        session = store.find(client.cookies.get(COOKIE))
        for conversation in session.conversations.values():
            conversation.updated_at = 1789230773.1169267

        seen: list[str] = []
        cursor = None
        for _ in range(5):  # 上限只是防止游标不前进时死循环
            query = f"?limit=2&cursor={cursor}" if cursor else "?limit=2"
            body = client.get(f"/api/v1/conversations{query}", headers=headers).json()
            seen.extend(item["id"] for item in body["conversations"])
            cursor = body["next_cursor"]
            if cursor is None:
                break

        assert cursor is None, "游标没有走到终点"
        assert sorted(seen) == sorted(created)
        assert len(seen) == len(set(seen)), "分页出现了重复"


def test_bad_pagination_cursor_is_400_not_a_silent_restart():
    with client_for(Scripted()) as client:
        headers = bootstrap(client)
        response = client.get("/api/v1/conversations?cursor=垃圾游标", headers=headers)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_input"


# --------------------------------------------------------------------------- #
# 运行
# --------------------------------------------------------------------------- #


def test_run_is_accepted_with_202_and_reaches_succeeded():
    with client_for(Scripted("model", "tools")) as client:
        headers = bootstrap(client)
        cid = new_conversation(client, headers)

        accepted = client.post(f"/api/v1/conversations/{cid}/runs", headers=headers, json={"message": "排盘"})
        assert accepted.status_code == 202, accepted.text
        body = accepted.json()
        run_id = body["run_id"]
        assert body["status"] == "queued"
        assert body["events_url"] == f"/api/v1/conversations/{cid}/runs/{run_id}/events"
        # 受理即返回：此刻结果还没有。
        assert "result" not in body

        frames = sse(client.get(body["events_url"], headers=headers))
        assert [frame["event"] for frame in frames] == [
            "run.accepted",
            "run.started",
            "run.progress",
            "run.progress",
            "run.final",
            "run.end",
        ]
        assert [frame["data"]["seq"] for frame in frames] == [1, 2, 3, 4, 5, 6]

        detail = client.get(f"/api/v1/conversations/{cid}/runs/{run_id}", headers=headers).json()
        assert detail["status"] == "succeeded"
        assert detail["result"]["reply"] == "命盘已排出。"
        assert detail["error_code"] is None
        assert detail["finished_at"]


def test_idempotency_key_is_refused_rather_than_ignored():
    """出现幂等键即 501。

    静默接受比拒绝更糟：调用方以为重试是安全的，于是真的会重试，而这里
    没有任何去重机制 —— 结果是同一句话被解读两遍。
    """
    with client_for(Scripted()) as client:
        headers = bootstrap(client)
        cid = new_conversation(client, headers)
        response = client.post(
            f"/api/v1/conversations/{cid}/runs",
            headers={**headers, "Idempotency-Key": "abc"},
            json={"message": "排盘"},
        )
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "not_implemented"
        # 被拒绝的运行不能留下任何痕迹。
        assert client.get(f"/api/v1/conversations/{cid}/messages", headers=headers).json()["total"] == 0


def test_replay_from_last_event_id_does_not_repeat():
    with client_for(Scripted("model")) as client:
        headers = bootstrap(client)
        cid = new_conversation(client, headers)
        run_id, frames = run_to_completion(client, headers, cid)
        assert len(frames) >= 3

        third = frames[2]
        replay = sse(
            client.get(
                f"/api/v1/conversations/{cid}/runs/{run_id}/events",
                headers={**headers, "Last-Event-ID": third["id"]},
            )
        )
        assert [frame["id"] for frame in replay] == [frame["id"] for frame in frames[3:]]
        # 重放**不会**重跑：事件里的 run_id 与结果都与原轮一致。
        assert all(frame["data"]["run_id"] == run_id for frame in replay)


def test_replay_of_a_finished_run_reaches_eof():
    """终态 run 的重放要能结束。挂住的话，客户端会以为这一轮还在跑。"""
    with client_for(Scripted()) as client:
        headers = bootstrap(client)
        cid = new_conversation(client, headers)
        run_id, frames = run_to_completion(client, headers, cid)

        replay = sse(client.get(f"/api/v1/conversations/{cid}/runs/{run_id}/events", headers=headers))
        assert replay[-1]["event"] == "run.end"
        assert len(replay) == len(frames)


@pytest.mark.parametrize("cursor", ["garbage", "other-run:3", ":3", "run-without-seq:", "3"])
def test_bad_or_foreign_cursors_are_400(cursor):
    """跨 run 的游标直接拒绝。

    忽略它会让客户端静默跳过或重放错的事件，而客户端无从发现 —— 它以为
    自己在续读。
    """
    with client_for(Scripted()) as client:
        headers = bootstrap(client)
        cid = new_conversation(client, headers)
        run_id, _ = run_to_completion(client, headers, cid)

        response = client.get(
            f"/api/v1/conversations/{cid}/runs/{run_id}/events",
            headers={**headers, "Last-Event-ID": cursor},
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "invalid_input"


def test_non_ascii_cursor_via_query_is_400():
    """非 ASCII 的游标只能走 ``?cursor=`` —— HTTP 头是 ASCII 的。

    两种写法都要能拒绝：只支持其中一条通道的校验，另一条就成了绕过它的路。
    """
    with client_for(Scripted()) as client:
        headers = bootstrap(client)
        cid = new_conversation(client, headers)
        run_id, _ = run_to_completion(client, headers, cid)

        response = client.get(f"/api/v1/conversations/{cid}/runs/{run_id}/events?cursor=垃圾游标", headers=headers)
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "invalid_input"


def test_unknown_run_is_404_not_an_empty_stream():
    """校验必须发生在返回流之前。

    写在异步生成器里的话，404 会在响应头已经发出 200 之后才抛出来 ——
    客户端拿到的是一条"成功打开然后莫名中断"的流。
    """
    with client_for(Scripted()) as client:
        headers = bootstrap(client)
        cid = new_conversation(client, headers)
        response = client.get(f"/api/v1/conversations/{cid}/runs/no-such-run/events", headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- #
# 错误信封
# --------------------------------------------------------------------------- #


def test_unauthenticated_v1_uses_the_envelope():
    with client_for(Scripted()) as client:
        response = client.get("/api/v1/conversations")
        assert response.status_code == 401
        error = response.json()["error"]
        assert error["code"] == "unauthorized"
        assert error["retryable"] is False
        assert error["request_id"]
        assert response.headers["x-request-id"] == error["request_id"]


def test_unknown_v1_path_is_an_envelope():
    """FastAPI 自己的 404 也要走信封。

    不注册 ``StarletteHTTPException`` 处理器的话，这一类（客户端最常撞上的
    那一类）会退回 ``{"detail": …}``，"统一错误响应"就只对主动抛出的错误成立。
    """
    with client_for(Scripted()) as client:
        response = client.get("/api/v1/nope")
        assert response.status_code == 404
        assert set(response.json()) == {"error"}
        assert response.json()["error"]["code"] == "not_found"


def test_unknown_legacy_path_keeps_the_plain_detail():
    """旧路径不受 v1 信封影响 —— 前端读的是 ``detail``。"""
    with client_for(Scripted()) as client:
        response = client.get("/api/nope")
        assert response.status_code == 404
        assert "detail" in response.json()
        assert "error" not in response.json()


def test_request_id_is_echoed_and_generated_per_request():
    with client_for(Scripted()) as client:
        first = client.get("/api/v1/session")
        second = client.get("/api/v1/session")
        assert first.headers["x-request-id"] != second.headers["x-request-id"]

        supplied = client.get("/api/v1/session", headers={"X-Request-ID": "trace-abc"})
        assert supplied.headers["x-request-id"] == "trace-abc"


# --------------------------------------------------------------------------- #
# 两套接口是同一套执行逻辑
# --------------------------------------------------------------------------- #


def test_legacy_and_v1_agree_on_the_same_scripted_graph(build_scripted_graph, chart_tool_call):
    """同一段脚本，分别走旧接口与 v1，结果与消息历史必须一致。

    这是"v1 不是第二套执行逻辑"的**行为**证明。结构上它们共用 ``RunsService``，
    但结构保证不了有人日后在 v1 路由里加一句"顺手也做点什么" —— 而那种改动
    不会让任何测试变红，只会让两套接口慢慢分叉。

    两个应用各配一个独立的图：checkpointer 随图走，共用一个就分不清
    "两边结果一致"与"第二遍读到了第一遍留下的历史"。
    """

    def script():
        return [AIMessage("", tool_calls=[chart_tool_call]), AIMessage("命盘已排出。")]

    legacy_app = create_app(service=RunService(graph=build_scripted_graph(script())))
    with TestClient(legacy_app, base_url="http://localhost") as client:
        headers = {"x-csrf-token": client.get("/api/session").json()["csrf_token"]}
        cid = client.post("/api/conversations", headers=headers).json()["id"]
        legacy_frames = sse(client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "排盘"}))
        legacy_saved = client.get(f"/api/conversations/{cid}").json()

    v1_app = create_app(service=RunService(graph=build_scripted_graph(script())))
    with TestClient(v1_app, base_url="http://localhost") as client:
        headers = bootstrap(client)
        cid = new_conversation(client, headers)
        _, v1_frames = run_to_completion(client, headers, cid)
        v1_saved = client.get(f"/api/v1/conversations/{cid}/messages", headers=headers).json()

    assert legacy_frames[-1]["event"] == "final"
    # 旧帧把 v1 事件整个塞进 ``data:`` 行，所以结果在它的 ``data`` 里。
    legacy_final = legacy_frames[-1]["data"]["data"]
    v1_final = next(frame["data"]["data"] for frame in v1_frames if frame["data"]["type"] == "run.final")
    assert legacy_final == v1_final

    assert [message["role"] for message in legacy_saved["messages"]] == [
        message["role"] for message in v1_saved["messages"]
    ]
    assert legacy_saved["result"] == v1_final
