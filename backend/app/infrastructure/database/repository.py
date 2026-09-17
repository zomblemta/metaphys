"""异步 PostgreSQL 仓储；准入和结果/公开事件分别在短事务内提交。"""

import secrets
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.domain.errors import Conflict, NotFound, RunCapacity, Unauthorized, Unavailable


class Database:
    def __init__(self, dsn):
        self.dsn = dsn
        self.pool = AsyncConnectionPool(
            dsn,
            min_size=1,
            max_size=8,
            open=False,
            timeout=5,
            kwargs={
                "row_factory": dict_row,
                "autocommit": True,
                "options": "-c statement_timeout=5000 -c lock_timeout=3000",
            },
        )
        self.guard = None

    async def open(self):
        await self.pool.open(wait=True)
        self.guard = await AsyncConnection.connect(self.dsn, autocommit=True, connect_timeout=5)
        locked = await (await self.guard.execute("SELECT pg_try_advisory_lock(730912013)")).fetchone()
        if not locked[0]:
            await self.close()
            raise RuntimeError("此数据库已有运行实例；当前只支持单 worker")
        async with self.transaction() as conn:
            versions = await (await conn.execute("SELECT version FROM app_schema")).fetchall()
            if versions != [{"version": 1}]:
                raise RuntimeError("数据库版本不匹配，请先迁移")

    async def close(self):
        await self.pool.close()
        if self.guard is not None:
            await self.guard.close()

    @asynccontextmanager
    async def transaction(self):
        if self.guard is None or self.guard.closed:
            raise Unavailable("数据库运行锁已失效。")
        await self.guard.execute("SELECT 1")
        async with self.pool.connection() as conn, conn.transaction():
            yield conn

    async def ready(self):
        async with self.transaction() as conn:
            await conn.execute("SELECT 1 FROM checkpoints LIMIT 1")

    async def authenticate(self, username):
        async with self.transaction() as conn:
            return await (
                await conn.execute("SELECT password_hash FROM app_users WHERE id=%s AND enabled", (username,))
            ).fetchone()

    async def session(self, owner, token, csrf):
        digest = sha256(token.encode()).hexdigest()
        async with self.transaction() as conn:
            await conn.execute("DELETE FROM app_sessions WHERE expires_at < now()")
            row = await (
                await conn.execute(
                    "SELECT csrf FROM app_sessions WHERE token_hash=%s AND owner_id=%s AND expires_at>now()",
                    (digest, owner),
                )
            ).fetchone()
            if row:
                return token, row["csrf"]
            token = secrets.token_urlsafe(32)
            digest = sha256(token.encode()).hexdigest()
            await conn.execute(
                "INSERT INTO app_sessions(token_hash, owner_id, csrf) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (digest, owner, csrf),
            )
            return token, csrf

    async def check_session(self, owner, token):
        async with self.transaction() as conn:
            row = await (
                await conn.execute(
                    "SELECT csrf FROM app_sessions WHERE token_hash=%s AND owner_id=%s AND expires_at>now()",
                    (sha256(token.encode()).hexdigest(), owner),
                )
            ).fetchone()
            if not row:
                raise Unauthorized("会话已过期，请刷新页面。")
            return row["csrf"]

    async def conversations(self, owner):
        async with self.transaction() as conn:
            return await (
                await conn.execute(
                    "SELECT c.id::text,c.title,c.failed, EXISTS(SELECT 1 FROM app_runs r WHERE r.conversation_id=c.id AND r.status IN ('queued','running')) AS busy FROM app_conversations c WHERE owner_id=%s AND NOT deleting ORDER BY created_at DESC",
                    (owner,),
                )
            ).fetchall()

    async def create_conversation(self, owner):
        cid = str(uuid4())
        async with self.transaction() as conn:
            await conn.execute("SELECT id FROM app_users WHERE id=%s FOR UPDATE", (owner,))
            row = await (
                await conn.execute("SELECT count(*) n FROM app_conversations WHERE owner_id=%s", (owner,))
            ).fetchone()
            if row["n"] >= 20:
                raise Conflict("最多保留 20 个会话。")
            await conn.execute(
                "INSERT INTO app_conversations(id,owner_id,title) VALUES (%s,%s,%s)", (cid, owner, "新的解读")
            )
        return {"id": cid, "title": "新的解读", "busy": False, "failed": False}

    async def _owned(self, conn, owner, cid, *, deleting=False):
        row = await (
            await conn.execute("SELECT * FROM app_conversations WHERE id=%s AND owner_id=%s FOR UPDATE", (cid, owner))
        ).fetchone()
        if not row or (row["deleting"] and not deleting):
            raise NotFound("会话不存在。")
        return row

    async def conversation(self, owner, cid):
        async with self.transaction() as conn:
            row = await self._owned(conn, owner, cid)
            msgs = await (
                await conn.execute(
                    "SELECT role,content FROM app_messages WHERE conversation_id=%s ORDER BY seq", (cid,)
                )
            ).fetchall()
            active = await (
                await conn.execute(
                    "SELECT id FROM app_runs WHERE conversation_id=%s AND status IN ('queued','running')", (cid,)
                )
            ).fetchone()
            return {
                "id": str(row["id"]),
                "title": row["title"],
                "failed": row["failed"],
                "busy": bool(active),
                "messages": msgs,
                "result": row["result"],
            }

    async def admit(self, owner, cid, message, key=None):
        rid = str(uuid4())
        async with self.transaction() as conn:
            # Global short admission lock makes the cross-conversation budget atomic.
            await conn.execute("SELECT pg_advisory_xact_lock(730912014)")
            conversation = await self._owned(conn, owner, cid)
            if key:
                old = await (
                    await conn.execute(
                        "SELECT id::text,input,status FROM app_runs WHERE conversation_id=%s AND idempotency_key=%s",
                        (cid, key),
                    )
                ).fetchone()
                if old:
                    if old["input"] != message:
                        raise Conflict("幂等键已用于不同输入。")
                    return old["id"]
            count = await (
                await conn.execute(
                    "SELECT count(*) n FROM app_runs WHERE conversation_id=%s AND status IN ('queued','running')",
                    (cid,),
                )
            ).fetchone()
            if count["n"]:
                raise Conflict("本会话正在解读，请等待完成。")
            if conversation["failed"]:
                raise Conflict("本会话的执行上下文需要恢复，请新建会话。")
            count = await (
                await conn.execute("SELECT count(*) n FROM app_runs WHERE status IN ('queued','running')")
            ).fetchone()
            if count["n"] >= 4:
                raise RunCapacity("当前解读任务较多，请稍后重试。")
            daily = await (
                await conn.execute(
                    "INSERT INTO app_daily_usage(owner_id,day,count) VALUES (%s,(now() AT TIME ZONE 'UTC')::date,1) ON CONFLICT(owner_id,day) DO UPDATE SET count=app_daily_usage.count+1 WHERE app_daily_usage.count<200 RETURNING count",
                    (owner,),
                )
            ).fetchone()
            if not daily:
                raise RunCapacity("已达到账号每日运行上限（UTC 每日重置）。")
            count = await (
                await conn.execute("SELECT count(*) n FROM app_messages WHERE conversation_id=%s", (cid,))
            ).fetchone()
            if count["n"] >= 80:
                raise Conflict("本会话已达到轮次上限。")
            await conn.execute(
                "INSERT INTO app_runs(id,conversation_id,input,status,idempotency_key) VALUES (%s,%s,%s,'queued',%s)",
                (rid, cid, message, key),
            )
            await conn.execute(
                "INSERT INTO app_messages(conversation_id,run_id,role,content) VALUES (%s,%s,'user',%s)",
                (cid, rid, message),
            )
            if count["n"] == 0:
                await conn.execute("UPDATE app_conversations SET title=%s WHERE id=%s", (message[:28], cid))
            await self._event(conn, rid, "run.accepted", {"status": "queued"})
        return rid

    async def _event(self, conn, rid, kind, data):
        await conn.execute(
            "INSERT INTO app_events(run_id,seq,kind,data) SELECT %s,COALESCE(max(seq),0)+1,%s,%s FROM app_events WHERE run_id=%s",
            (rid, kind, Jsonb(data), rid),
        )

    async def claim(self):
        async with self.transaction() as conn:
            row = await (
                await conn.execute(
                    "SELECT id,conversation_id,input FROM app_runs WHERE status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                )
            ).fetchone()
            if row:
                await conn.execute("UPDATE app_runs SET status='running' WHERE id=%s", (row["id"],))
                await self._event(conn, row["id"], "run.started", {"status": "running"})
            return row

    async def progress(self, rid, node):
        async with self.transaction() as conn:
            row = await (await conn.execute("SELECT status FROM app_runs WHERE id=%s FOR UPDATE", (rid,))).fetchone()
            if row and row["status"] == "running":
                await self._event(conn, rid, "run.progress", {"status": "running", "node": node})

    async def finish(self, rid, *, result=None, error=None, artifacts=()):
        async with self.transaction() as conn:
            row = await (await conn.execute("SELECT * FROM app_runs WHERE id=%s FOR UPDATE", (rid,))).fetchone()
            if not row or row["status"] not in ("queued", "running"):
                return
            status = "succeeded" if result is not None else ("timed_out" if error == "run_timeout" else "failed")
            await conn.execute(
                "UPDATE app_runs SET status=%s,result=%s,error_code=%s,finished_at=now() WHERE id=%s",
                (status, Jsonb(result), error, rid),
            )
            if result is not None:
                for key in artifacts:
                    await conn.execute(
                        "INSERT INTO app_artifacts(storage_key) VALUES (%s) ON CONFLICT DO NOTHING", (key,)
                    )
                    await conn.execute(
                        "INSERT INTO app_artifact_refs(storage_key,run_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                        (key, rid),
                    )
                await conn.execute(
                    "UPDATE app_conversations SET result=%s,updated_at=now() WHERE id=%s",
                    (Jsonb(result), row["conversation_id"]),
                )
                await conn.execute(
                    "INSERT INTO app_messages(conversation_id,run_id,role,content) VALUES (%s,%s,'assistant',%s)",
                    (row["conversation_id"], rid, result["reply"]),
                )
                await self._event(conn, rid, "run.final", result)
            else:
                message = "本轮未完成。为避免使用不完整上下文，请新建会话。"
                await conn.execute(
                    "UPDATE app_conversations SET failed=true,updated_at=now() WHERE id=%s", (row["conversation_id"],)
                )
                await conn.execute(
                    "INSERT INTO app_messages(conversation_id,run_id,role,content) VALUES (%s,%s,'error',%s)",
                    (row["conversation_id"], rid, message),
                )
                await self._event(conn, rid, "run.failed", {"message": message, "error_code": error})
            await self._event(conn, rid, "run.end", {"status": status})

    async def recover(self):
        async with self.transaction() as conn:
            rows = await (await conn.execute("SELECT id FROM app_runs WHERE status='running'")).fetchall()
        for row in rows:
            await self.finish(row["id"], error="server_restart")

    async def run(self, owner, cid, rid):
        async with self.transaction() as conn:
            await self._owned(conn, owner, cid)
            row = await (
                await conn.execute(
                    "SELECT id::text,conversation_id::text,status,result,error_code FROM app_runs WHERE id=%s AND conversation_id=%s",
                    (rid, cid),
                )
            ).fetchone()
            if not row:
                raise NotFound("运行不存在。")
            return row

    async def events(self, owner, cid, rid, seq):
        await self.run(owner, cid, rid)
        async with self.transaction() as conn:
            return await (
                await conn.execute(
                    "SELECT seq,kind,data FROM app_events WHERE run_id=%s AND seq>%s ORDER BY seq LIMIT 100", (rid, seq)
                )
            ).fetchall()

    async def mark_deleting(self, owner, cid):
        async with self.transaction() as conn:
            await self._owned(conn, owner, cid, deleting=True)
            row = await (
                await conn.execute(
                    "SELECT id FROM app_runs WHERE conversation_id=%s AND status IN ('queued','running')", (cid,)
                )
            ).fetchone()
            if row:
                raise Conflict("请等待运行结束再删除。")
            await conn.execute("UPDATE app_conversations SET deleting=true WHERE id=%s", (cid,))

    async def deleting(self):
        async with self.transaction() as conn:
            return await (await conn.execute("SELECT id::text FROM app_conversations WHERE deleting")).fetchall()

    async def remove(self, cid):
        async with self.transaction() as conn:
            await conn.execute("DELETE FROM app_conversations WHERE id=%s AND deleting", (cid,))

    async def prune_artifacts(self, storage):
        async with self.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(730912014)")
            active = await (
                await conn.execute("SELECT 1 FROM app_runs WHERE status IN ('queued','running') LIMIT 1")
            ).fetchone()
            if active:
                return
            rows = await (
                await conn.execute(
                    "SELECT storage_key FROM app_artifacts a WHERE NOT EXISTS (SELECT 1 FROM app_artifact_refs r WHERE r.storage_key=a.storage_key)"
                )
            ).fetchall()
            directory = storage.svg_directory()
            for row in rows:
                key = row["storage_key"]
                path = directory / key
                if path.parent != directory or path.is_symlink():
                    raise RuntimeError("非法制品登记路径")
                path.unlink(missing_ok=True)
                await conn.execute("DELETE FROM app_artifacts WHERE storage_key=%s", (key,))


async def migrate(dsn):
    async with await AsyncConnection.connect(dsn, autocommit=True) as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(730912013)")
        await conn.execute(Path(__file__).with_name("schema.sql").read_text())
