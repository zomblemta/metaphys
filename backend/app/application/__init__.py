"""应用层：用例编排。

只依赖 ``domain/`` 与 ``ports.py``。**不 import fastapi、不 import
``app.infrastructure``、不 import ``app.gateway``** —— 这条由
``tests/test_app_layers.py`` 机械校验。它保证的是：业务规则能在没有 Web
框架、没有真实存储的情况下被推理与测试。

各模块职责：

- ``runs.py``   准入、运行生命周期、事件、订阅（核心）
- ``sessions.py`` 会话引导与过期回收
- ``conversations.py`` 会话增删查
- ``artifacts.py`` 受控制品授权读取
- ``views.py``  线上 JSON 形状（纯函数）
- ``messages.py`` 对外文案（一处定义）
"""
