"""基础设施层：``application/ports.py`` 里那些端口的具体实现。

阶段 A 全部是内存实现（``memory.py``）、harness 适配器（``execution.py``）
与本地文件存储（``storage.py``）。阶段 B 会新增 ``database/``、``events/``、
``identity/`` 三个子包并替换掉 ``memory.py`` —— 端口不变，因此 ``application/``
与 ``gateway/`` 不需要跟着改。

这一层可以 import FastAPI 之外的一切，但**不**应该 import ``app.gateway``：
依赖方向是 gateway → application → domain，infrastructure 实现 application
定义的端口。
"""
