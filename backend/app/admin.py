"""显式数据库迁移与受邀账号管理，python -m app.admin --help。"""

import argparse
import asyncio
import getpass
import os
import re

from psycopg import AsyncConnection

from app.infrastructure.database.repository import migrate
from app.infrastructure.execution import migrate_executions
from app.infrastructure.identity import hash_password


async def run(args):
    dsn = os.environ["METAPHYS_DATABASE_URL"]
    if args.command == "migrate":
        await migrate(dsn)
        await migrate_executions(dsn)
        print("数据库迁移完成。")
    else:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", args.username):
            raise ValueError("用户名仅允许字母、数字、点、下划线和连字符")
        async with await AsyncConnection.connect(dsn, autocommit=True) as conn:
            if args.command == "disable-user":
                await conn.execute("UPDATE app_users SET enabled=false WHERE id=%s", (args.username,))
                await conn.execute("DELETE FROM app_sessions WHERE owner_id=%s", (args.username,))
            else:
                password = getpass.getpass("新密码（至少12字符）：")
                if password != getpass.getpass("再次输入："):
                    raise ValueError("两次密码不一致")
                encoded = hash_password(password)
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO app_users(id,password_hash) VALUES (%s,%s) ON CONFLICT(id) DO UPDATE SET password_hash=excluded.password_hash,enabled=true",
                        (args.username, encoded),
                    )
                    await conn.execute("DELETE FROM app_sessions WHERE owner_id=%s", (args.username,))
            print("账号已更新。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "set-user", "disable-user"])
    parser.add_argument("username", nargs="?")
    args = parser.parse_args()
    if args.command != "migrate" and not args.username:
        parser.error("账号操作需要 username")
    asyncio.run(run(args))
