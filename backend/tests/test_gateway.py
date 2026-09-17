"""真实 HTTP + 脚本模型：会话所有权、SSE、文件边界与失败恢复。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from metaphys.runtime import RunService

from app.gateway.main import COOKIE, create_app
from app.gateway.store import SessionStore


@pytest.fixture(autouse=True)
def frontend_build_fixture(tmp_path, monkeypatch):
    """HTTP 单测不依赖 Node 构建；真实 Next 构建另做浏览器验收。"""
    directory = tmp_path / "web"
    (directory / "_next/static").mkdir(parents=True)
    (directory / "index.html").write_text("<html>知时<script>window.test=1</script></html>")
    (directory / "_next/static/app.js").write_text("/* test build */")
    monkeypatch.setattr("app.gateway.main.FRONTEND", directory)


def client_for(service, **kwargs):
    return TestClient(create_app(service=service, **kwargs), base_url="http://localhost")


def bootstrap(client):
    data = client.get("/api/session").json()
    return {"x-csrf-token": data["csrf_token"]}


def conversation(client, headers):
    response = client.post("/api/conversations", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


def test_real_graph_sse_refresh_delete_and_checkpoint(build_scripted_graph, chart_tool_call):
    graph = build_scripted_graph([AIMessage("", tool_calls=[chart_tool_call]), AIMessage("命盘已排出。")])
    with client_for(RunService(graph=graph)) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        result = events(client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "排盘"}))
        assert result[-1]["event"] == "final"
        assert result[-1]["data"]["charts"]["bazi"]["day_master"]
        assert all(event["data"] == {"status": "running"} for event in result[:-1])
        saved = client.get(f"/api/conversations/{cid}").json()
        assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
        assert saved["result"]["reply"] == "命盘已排出。"
        assert not saved["busy"]
        assert graph.get_state({"configurable": {"thread_id": cid}}).values
        assert client.delete(f"/api/conversations/{cid}", headers=headers).status_code == 204
        assert client.get(f"/api/conversations/{cid}").status_code == 404
        assert not graph.get_state({"configurable": {"thread_id": cid}}).values


def test_other_browser_cannot_read_write_or_delete(build_scripted_graph):
    app = create_app(service=RunService(graph=build_scripted_graph([AIMessage("你好")])))
    with TestClient(app, base_url="http://localhost") as a, TestClient(app, base_url="http://localhost") as b:
        ah, bh = bootstrap(a), bootstrap(b)
        cid = conversation(a, ah)
        assert b.get(f"/api/conversations/{cid}").status_code == 404
        assert b.get(f"/api/conversations/{cid}/chart.svg").status_code == 404
        assert b.post(f"/api/conversations/{cid}/runs", headers=bh, json={"message": "偷看"}).status_code == 404
        assert b.delete(f"/api/conversations/{cid}", headers=bh).status_code == 404
        assert b.get("/api/session").json()["conversations"] == []


def test_cookie_csrf_host_and_static_boundaries(build_scripted_graph):
    with client_for(RunService(graph=build_scripted_graph([AIMessage("你好")]))) as client:
        assert client.get("/api/conversations/nope").status_code == 401
        response = client.get("/api/session")
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        headers = {"x-csrf-token": response.json()["csrf_token"]}
        assert client.post("/api/conversations").status_code == 403
        assert (
            client.post("/api/conversations", headers={**headers, "origin": "https://evil.example"}).status_code == 403
        )
        assert client.get("/", headers={"host": "evil.example"}).status_code == 400
        assert client.get("/_next/static/app.js").status_code == 200
        assert client.get("/_next/config.yaml").status_code == 404
        assert client.get("/var/charts/").status_code == 404
        page = client.get("/")
        assert "知时" in page.text
        assert page.headers["cache-control"] == "no-store"
        assert "script-src 'self'" in page.headers["content-security-policy"]
        assert "sha256-" in page.headers["content-security-policy"]
        assert "unsafe-inline" not in page.headers["content-security-policy"]
        assert client.cookies.get(COOKIE)


@pytest.mark.parametrize("message", ["", "   ", "字" * 4001, 42])
def test_invalid_message_never_reaches_model(build_scripted_graph, message):
    with client_for(RunService(graph=build_scripted_graph([AIMessage("不应执行")]))) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        response = client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": message})
        assert response.status_code == 422
        assert client.get(f"/api/conversations/{cid}").json()["messages"] == []


def test_svg_requires_current_owned_chart_and_blocks_symlink(
    build_scripted_graph, astro_tool_call, tmp_path, monkeypatch
):
    monkeypatch.setattr("metaphys.tools.builtins.astro_chart.resolve_svg_dir", lambda: tmp_path)
    monkeypatch.setattr("app.gateway.main.resolve_svg_dir", lambda: tmp_path)
    graph = build_scripted_graph([AIMessage("", tool_calls=[astro_tool_call]), AIMessage("星盘已排出。")])
    with client_for(RunService(graph=graph)) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        assert client.get(f"/api/conversations/{cid}/chart.svg").status_code == 404
        events(client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "排星盘"}))
        response = client.get(f"/api/conversations/{cid}/chart.svg")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert "sandbox" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"
        path = next(tmp_path.glob("astro-*.svg"))
        path.unlink()
        outside = tmp_path.parent / "secret.svg"
        outside.write_text("secret")
        path.symlink_to(outside)
        assert client.get(f"/api/conversations/{cid}/chart.svg").status_code == 404


def test_model_failure_is_generic_and_does_not_leave_busy_state():
    class Broken:
        _graph = None

        async def astream_run(self, *args, **kwargs):
            raise RuntimeError("PRIVATE_KEY_OR_BIRTH_DATA")
            yield

    with client_for(Broken()) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        response = client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "你好"})
        assert events(response)[-1]["event"] == "error"
        assert "PRIVATE_KEY" not in response.text
        saved = client.get(f"/api/conversations/{cid}").json()
        assert saved["failed"] and not saved["busy"]
        assert (
            client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "重试"}).status_code == 409
        )
        assert client.delete(f"/api/conversations/{cid}", headers=headers).status_code == 204


def test_timeout_is_reported_and_releases_conversation():
    class Slow:
        _graph = None

        async def astream_run(self, *args, **kwargs):
            await asyncio.sleep(1)
            yield {}

    with client_for(Slow(), run_timeout=0.01) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        assert (
            events(client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "你好"}))[-1][
                "event"
            ]
            == "error"
        )
        assert not client.get(f"/api/conversations/{cid}").json()["busy"]


def test_busy_conversation_rejects_overlapping_runs_and_delete():
    store = SessionStore()
    with client_for(SimpleNamespace(_graph=None), store=store) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        session = store.find(client.cookies.get(COOKIE))
        session.conversations[cid].busy = True
        assert (
            client.post(f"/api/conversations/{cid}/runs", headers=headers, json={"message": "你好"}).status_code == 409
        )
        assert client.delete(f"/api/conversations/{cid}", headers=headers).status_code == 409
        session.conversations[cid].busy = False


def test_session_expiry_and_capacity():
    store = SessionStore(ttl=1, max_sessions=1, max_conversations=1)
    with client_for(SimpleNamespace(_graph=None), store=store) as client:
        headers = bootstrap(client)
        cid = conversation(client, headers)
        assert client.post("/api/conversations", headers=headers).status_code == 409
        session = store.find(client.cookies.get(COOKIE))
        session.touched -= 2
        assert client.get(f"/api/conversations/{cid}").status_code == 401
        renewed = client.get("/api/session")
        assert renewed.status_code == 200
        assert renewed.json()["conversations"] == []
        assert len(store.sessions) == 1


def test_disconnected_browser_does_not_cancel_run():
    """使用真实 TCP 断开 SSE 订阅；后台任务完成，刷新可恢复结果。"""
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    finished = threading.Event()

    class SlowReply:
        _graph = None

        async def astream_run(self, *args, **kwargs):
            yield {"event": "update", "data": {"status": "running"}}
            await asyncio.sleep(0.15)
            yield {"event": "final", "data": {"reply": "断线后完成", "charts": {}}}
            finished.set()

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(service=SlowReply()), log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
            headers = bootstrap(client)
            cid = conversation(client, headers)
            with client.stream(
                "POST", f"/api/conversations/{cid}/runs", headers=headers, json={"message": "你好"}
            ) as response:
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        break
            assert finished.wait(2), "断开浏览器不应取消模型运行"
            saved = client.get(f"/api/conversations/{cid}").json()
            assert saved["result"]["reply"] == "断线后完成"
            assert saved["messages"][-1]["content"] == "断线后完成"
            assert not saved["busy"]
    finally:
        server.should_exit = True
        thread.join(timeout=3)
        sock.close()


def test_next_dev_origin_is_opt_in(build_scripted_graph, monkeypatch):
    with client_for(RunService(graph=build_scripted_graph([AIMessage("你好")]))) as client:
        headers = {**bootstrap(client), "origin": "http://127.0.0.1:3000"}
        assert client.post("/api/conversations", headers=headers).status_code == 403
        monkeypatch.setenv("METAPHYS_NEXT_DEV", "1")
        assert client.post("/api/conversations", headers=headers).status_code == 201
        assert (
            client.post("/api/conversations", headers={**headers, "origin": "http://evil.example:3000"}).status_code
            == 403
        )


def test_missing_next_build_has_actionable_response(build_scripted_graph, tmp_path, monkeypatch):
    monkeypatch.setattr("app.gateway.main.FRONTEND", tmp_path / "not-built")
    with client_for(RunService(graph=build_scripted_graph([AIMessage("你好")]))) as client:
        response = client.get("/")
        assert response.status_code == 503
        assert "npm run build" in response.json()["detail"]


def test_next_asset_directory_cannot_serve_symlinks_outside_build(build_scripted_graph, tmp_path):
    from app.gateway.main import FRONTEND

    outside = tmp_path / "private.txt"
    outside.write_text("private")
    (FRONTEND / "_next/leak.txt").symlink_to(outside)
    with client_for(RunService(graph=build_scripted_graph([AIMessage("你好")]))) as client:
        assert client.get("/_next/leak.txt").status_code == 404
