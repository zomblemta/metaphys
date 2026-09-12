"""反射解析 —— 把配置里的点分路径解析成真实对象。

工具与模型都走这条路注册：`config.yaml` 只写 `use: 模块:对象`，加一个 provider
或工具不必改代码。代价是错误被推迟到解析时，所以这里的报错必须**足够明确** ——
不能把 `ModuleNotFoundError` 直接漏出去，那会让人以为是依赖没装好。
"""

from metaphys.reflection.resolvers import resolve_class, resolve_variable

__all__ = ["resolve_class", "resolve_variable"]
