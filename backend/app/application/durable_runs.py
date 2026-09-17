"""持久化运行监督器：已提交任务由数据库扫描领取，HTTP 连接不持有任务。"""

import asyncio
import logging

logger = logging.getLogger(__name__)


class DurableRuns:
    def __init__(self, repository, executor, timeout=180, artifacts=None):
        self.artifacts = artifacts
        self.repository = repository
        self.executor = executor
        self.timeout = timeout
        self.ready = False
        self.closing = False
        self.tasks = set()
        self.supervisor = None

    async def start(self):
        await self.repository.recover()
        await self.cleanup()
        self.ready = True
        self.supervisor = asyncio.create_task(self.loop())

    async def cleanup(self):
        for row in await self.repository.deleting():
            await self.executor.delete_execution(row["id"])
            await self.repository.remove(row["id"])
        if self.artifacts is not None:
            await self.repository.prune_artifacts(self.artifacts)

    async def loop(self):
        try:
            while not self.closing:
                while not self.closing and len(self.tasks) < 4:
                    row = await self.repository.claim()
                    if not row:
                        break
                    task = asyncio.create_task(self.execute(row))
                    self.tasks.add(task)
                    task.add_done_callback(self.completed)
                await self.cleanup()
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.ready = False
            logger.error("运行监督器停止；拒绝新运行，等待进程重启。")

    def completed(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.ready = False
            self.closing = True
            logger.error("运行结果持久化失败；拒绝新运行。")

    async def execute(self, row):
        rid, cid = str(row["id"]), str(row["conversation_id"])
        try:
            final = None
            async with asyncio.timeout(self.timeout):
                async for event in self.executor.execute(run_id=rid, thread_id=cid, message=row["input"]):
                    if event.get("event") == "final":
                        final = event.get("data")
                    else:
                        await self.repository.progress(rid, event.get("node", ""))
            if not isinstance(final, dict) or not isinstance(final.get("reply"), str) or not final["reply"].strip():
                raise ValueError("missing final result")
            artifact_keys = []
            astro = (final.get("charts") or {}).get("astro")
            if astro and self.artifacts is not None:
                path = self.artifacts.locate(astro)
                if path is None:
                    raise ValueError("missing chart artifact")
                artifact_keys.append(path.name)
            await self.repository.finish(rid, result=final, artifacts=artifact_keys)
        except asyncio.CancelledError:
            # A restart will terminalize any row whose cleanup cannot reach the DB.
            await self.repository.finish(rid, error="server_shutdown")
            raise
        except TimeoutError:
            await self.repository.finish(rid, error="run_timeout")
        except Exception as exc:
            logger.warning("运行失败 run_id=%s type=%s", rid, type(exc).__name__)
            await self.repository.finish(rid, error="run_failed")

    async def shutdown(self):
        self.ready = False
        self.closing = True
        if self.supervisor:
            self.supervisor.cancel()
            await asyncio.gather(self.supervisor, return_exceptions=True)
        tasks = list(self.tasks)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=5)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=2)
        # Deployment supervisor enforces the outer process deadline. Late tasks
        # cannot publish after the DB closes; startup recovers their durable rows.
