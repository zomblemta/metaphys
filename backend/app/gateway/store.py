"""兼容再导出。实现已迁至 :mod:`app.infrastructure.memory`。

留着这一层是因为 ``tests/test_gateway.py`` 从 ``app.gateway.store`` 导入
``SessionStore`` —— 那个文件是本次拆分的验收基线，逐字节不改。新代码请直接
导入 ``app.infrastructure``：这里只是旧路径。
"""

from app.infrastructure.memory import SessionStore

__all__ = ["SessionStore"]
