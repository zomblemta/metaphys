"""独立临时 PostgreSQL 数据库中的部署契约；显式 TEST_POSTGRES_DSN 启用。"""

import asyncio
import os
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.domain.errors import Conflict, NotFound
from app.infrastructure.database.repository import Database, migrate
from app.infrastructure.execution import migrate_executions
from app.infrastructure.identity import hash_password
from app.production import create_app

DSN = os.environ.get("TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="需要显式 TEST_POSTGRES_DSN 指向隔离测试 PostgreSQL")
PASSWORD = "test-password-only-123"


@pytest.fixture
def database_dsn():
    name = "metaphys_test_" + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
    dsn = make_conninfo(**{**conninfo_to_dict(DSN), "dbname": name})

    async def setup():
        await migrate(dsn)
        await migrate(dsn)
        await migrate_executions(dsn)

    asyncio.run(setup())
    with psycopg.connect(dsn, autocommit=True) as conn:
        for user in ("alice", "bob"):
            conn.execute("INSERT INTO app_users(id,password_hash) VALUES (%s,%s)", (user, hash_password(PASSWORD)))
    yield dsn
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(psycopg.sql.Identifier(name)))


class Executor:
    def __init__(self):
        self.calls = 0
        self.deleted = []

    async def execute(self, **kwargs):
        self.calls += 1
        yield {"event": "update", "node": "model"}
        yield {"event": "final", "data": {"reply": "测试解读", "charts": {}}}

    async def delete_execution(self, cid):
        self.deleted.append(cid)


def test_restart_history_auth_idempotency_and_replay(database_dsn):
    executor = Executor()

    def app():
        return create_app(dsn=database_dsn, origin="https://localhost", executor=executor)

    with TestClient(app(), base_url="https://localhost") as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/session").status_code == 401
        client.auth = ("alice", PASSWORD)
        response = client.get("/api/session")
        assert response.status_code == 200, response.text
        headers = {"x-csrf-token": response.json()["csrf_token"], "idempotency-key": "retry-1"}
        cid = client.post("/api/conversations", headers=headers).json()["id"]
        path = f"/api/conversations/{cid}/runs"
        response = client.post(path, headers=headers, json={"message": "你好"})
        assert response.status_code == 200, response.text
        assert "测试解读" in response.text
        response = client.post(path, headers=headers, json={"message": "你好"})
        assert response.status_code == 200
        assert executor.calls == 1
        assert client.post(path, headers=headers, json={"message": "不同内容"}).status_code == 409
        assert client.post(path, json={"message": "缺少CSRF"}).status_code == 403
        client.auth = ("bob", PASSWORD)
        assert client.get(f"/api/conversations/{cid}").status_code == 404
    with TestClient(app(), base_url="https://localhost") as client:
        client.auth = ("alice", PASSWORD)
        headers = {"x-csrf-token": client.get("/api/session").json()["csrf_token"], "idempotency-key": "retry-1"}
        result = client.get(f"/api/conversations/{cid}").json()
        assert result["messages"] == [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "测试解读"}]
        assert result["busy"] is False
        response = client.post(f"/api/v1/conversations/{cid}/runs", headers=headers, json={"message": "你好"})
        rid = response.json()["run_id"]
        events_url = response.json()["events_url"]
        response = client.get(events_url, headers={"last-event-id": f"{rid}:1"})
        assert "run.final" in response.text
        assert "run.accepted" not in response.text
        assert client.get(events_url, headers={"last-event-id": f"{rid}:9999"}).status_code == 400
        assert client.delete(f"/api/conversations/{cid}", headers=headers).status_code == 204
        assert client.get(f"/api/conversations/{cid}").status_code == 404
        assert cid in executor.deleted


def test_atomic_admission_recovery_and_instance_lock(database_dsn):
    async def scenario():
        db = Database(database_dsn)
        await db.open()
        peer = Database(database_dsn)
        with pytest.raises(RuntimeError, match="单 worker"):
            await peer.open()
        cid = (await db.create_conversation("alice"))["id"]
        results = await asyncio.gather(
            *(db.admit("alice", cid, "hello", str(i)) for i in range(8)), return_exceptions=True
        )
        assert sum(isinstance(item, str) for item in results) == 1
        assert sum(isinstance(item, Conflict) for item in results) == 7
        with pytest.raises(NotFound):
            await db.conversation("bob", cid)
        row = await db.claim()
        rid = str(row["id"])
        await db.close()
        db = Database(database_dsn)
        await db.open()
        await db.recover()
        run = await db.run("alice", cid, rid)
        assert run["status"] == "failed" and run["error_code"] == "server_restart"
        assert (await db.conversation("alice", cid))["failed"]
        with pytest.raises(Conflict):
            await db.admit("alice", cid, "do not reuse partial checkpoint")
        events = await db.events("alice", cid, rid, 0)
        assert events[-1]["kind"] == "run.end"
        await db.close()

    asyncio.run(scenario())


def test_persistent_graph_survives_new_executor(database_dsn, monkeypatch):
    from langchain_core.messages import AIMessage

    from app.infrastructure.execution import persistent_executor
    from tests.conftest import ScriptedChatModel

    monkeypatch.setattr(
        "metaphys.agents.lead_agent.agent.create_chat_model",
        lambda **kwargs: ScriptedChatModel(responses=[AIMessage(content="测试回复")]),
    )

    async def scenario():
        async with persistent_executor(database_dsn) as executor:
            first = [
                event async for event in executor.execute(run_id="one", thread_id="test-thread", message="第一轮")
            ][-1]["data"]
        async with persistent_executor(database_dsn) as executor:
            second = [
                event async for event in executor.execute(run_id="two", thread_id="test-thread", message="第二轮")
            ][-1]["data"]
            assert second["message_count"] > first["message_count"]
            await executor.delete_execution("test-thread")
        async with persistent_executor(database_dsn) as executor:
            third = [
                event async for event in executor.execute(run_id="three", thread_id="test-thread", message="重建")
            ][-1]["data"]
            assert third["message_count"] == first["message_count"]

    asyncio.run(scenario())


def test_result_transaction_and_artifact_deletion(database_dsn, tmp_path):
    from app.infrastructure.storage import SvgArtifactStore

    async def scenario():
        db = Database(database_dsn)
        await db.open()
        cid = (await db.create_conversation("alice"))["id"]
        rid = await db.admit("alice", cid, "hello")
        await db.claim()
        original = db._event

        async def broken(conn, run_id, kind, data):
            if kind == "run.final":
                raise RuntimeError("simulated interruption")
            await original(conn, run_id, kind, data)

        db._event = broken
        with pytest.raises(RuntimeError, match="simulated"):
            await db.finish(rid, result={"reply": "must roll back", "charts": {}})
        assert (await db.run("alice", cid, rid))["status"] == "running"
        view = await db.conversation("alice", cid)
        assert view["result"] is None and len(view["messages"]) == 1
        assert not any(e["kind"] == "run.final" for e in await db.events("alice", cid, rid, 0))
        db._event = original
        key = "astro-0123456789abcdef.svg"
        (tmp_path / key).write_text("<svg/>")
        await db.finish(rid, result={"reply": "committed", "charts": {}}, artifacts=[key])
        storage = SvgArtifactStore(lambda: tmp_path)
        await db.prune_artifacts(storage)
        assert (tmp_path / key).exists()
        await db.mark_deleting("alice", cid)
        with pytest.raises(NotFound):
            await db.admit("alice", cid, "race with deletion")
        await db.remove(cid)
        await db.prune_artifacts(storage)
        assert not (tmp_path / key).exists()
        await db.prune_artifacts(storage)
        await db.close()

    asyncio.run(scenario())


def test_account_disable_revokes_access(database_dsn):
    with TestClient(
        create_app(dsn=database_dsn, origin="https://localhost", executor=Executor()), base_url="https://localhost"
    ) as client:
        client.auth = ("alice", PASSWORD)
        assert client.get("/api/session").status_code == 200
        with psycopg.connect(database_dsn, autocommit=True) as conn:
            conn.execute("UPDATE app_users SET enabled=false WHERE id='alice'")
        assert client.get("/api/session").status_code == 401


def test_quota_survives_conversation_deletion(database_dsn):
    from app.domain.errors import RunCapacity

    async def scenario():
        db = Database(database_dsn)
        await db.open()
        cid = (await db.create_conversation("alice"))["id"]
        rid = await db.admit("alice", cid, "first")
        await db.claim()
        await db.finish(rid, result={"reply": "done", "charts": {}})
        await db.mark_deleting("alice", cid)
        await db.remove(cid)
        async with db.transaction() as conn:
            usage = await (await conn.execute("SELECT count FROM app_daily_usage WHERE owner_id='alice'")).fetchone()
            assert usage["count"] == 1
            await conn.execute("UPDATE app_daily_usage SET count=200 WHERE owner_id='alice'")
        cid = (await db.create_conversation("alice"))["id"]
        with pytest.raises(RunCapacity):
            await db.admit("alice", cid, "over budget")
        await db.close()

    asyncio.run(scenario())


def test_database_backup_restores_readable_history(database_dsn, tmp_path):
    import shutil
    import subprocess

    if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
        pytest.skip("备份演练需要 pg_dump 和 pg_restore")

    async def seed():
        db = Database(database_dsn)
        await db.open()
        cid = (await db.create_conversation("alice"))["id"]
        rid = await db.admit("alice", cid, "backup test")
        await db.claim()
        await db.finish(rid, result={"reply": "restore me", "charts": {}})
        await db.close()
        return cid, rid

    cid, rid = asyncio.run(seed())
    archive = tmp_path / "test.dump"
    subprocess.run(
        ["pg_dump", "--format=custom", "--file", str(archive), "--dbname", database_dsn],
        check=True,
        capture_output=True,
    )
    target = "metaphys_restore_" + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(target)))
    restored = make_conninfo(**{**conninfo_to_dict(DSN), "dbname": target})
    try:
        subprocess.run(
            ["pg_restore", "--no-owner", "--exit-on-error", "--dbname", restored, str(archive)],
            check=True,
            capture_output=True,
        )

        async def check():
            db = Database(restored)
            await db.open()
            assert (await db.conversation("alice", cid))["result"]["reply"] == "restore me"
            assert (await db.events("alice", cid, rid, 0))[-1]["kind"] == "run.end"
            await db.close()

        asyncio.run(check())
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(psycopg.sql.Identifier(target)))
