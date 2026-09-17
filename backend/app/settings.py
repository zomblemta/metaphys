"""应用运行参数。

这些数字今天散落在 ``create_app`` 的字面量里（``180``、``80``、``4``、
``24000``），散着的问题不是难找，而是**测试够不着**：想验证"并发满了返回
429"就得真的同时起四个运行。收成一个可替换的对象后，测试把它设成 1 即可。

阶段 A 只从这里读，不从 ``config.yaml`` 读。理由：``create_app`` 必须能在
没有模型配置的前提下构造（集成测试注入脚本模型就是为了这个），而运行参数
与排盘配置是两件事。接 ``config.gateway`` 属阶段 B。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """一轮运行的时长上限。超时记 ``run_timeout`` 而不是笼统的失败。"""

    run_timeout: float = 180.0

    #: 会话消息条数上限。80 条 = 40 轮（每条用户消息都有一条助手回复）。
    max_messages: int = 80

    #: 全局并发运行数。超过返回 429（容量限制），与会话级的 409（忙）区分开。
    max_concurrent_runs: int = 4

    #: 请求体实际字节上限；分块正文、非法 JSON 与未知端点同样受限。
    max_body_bytes: int = 24000

    #: SSE 心跳间隔。心跳不占业务序号。
    keep_alive: float = 15.0

    #: 关闭时等待在途运行的秒数。到点即取消，不逐个无限等待。
    shutdown_timeout: float = 5.0


__all__ = ["Settings"]
