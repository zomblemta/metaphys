"""点分路径反射解析。

`config.yaml` 里写 `use: langchain_deepseek:ChatDeepSeek`，运行时解析成类并校验
它确实是 `BaseChatModel` 的子类。这样加 provider / 工具不必改代码。

**报错信息是这里的核心产品。** 配置写错是运维期最高频的故障，而
`ModuleNotFoundError: No module named 'langchain_deepseak'` 会把人引向
"依赖没装"的错误方向。故一律转成指出「哪个 use: 写错了」的 ImportError。
"""

from __future__ import annotations

import importlib
from typing import Any, TypeVar

T = TypeVar("T")

_PATH_EXAMPLE = "模块路径:属性名，例如 langchain_deepseek:ChatDeepSeek"


def _split_path(variable_path: str) -> tuple[str, str]:
    """拆出 (模块路径, 属性名)。

    用 rsplit 而非 split：从右侧拆能容纳模块路径里出现冒号的写法。
    """
    try:
        module_path, variable_name = variable_path.rsplit(":", 1)
    except ValueError as err:
        raise ImportError(f"{variable_path!r} 不是合法的 use: 路径，应为 {_PATH_EXAMPLE}") from err

    if not module_path or not variable_name:
        raise ImportError(f"{variable_path!r} 不完整，应为 {_PATH_EXAMPLE}")

    return module_path, variable_name


def _load_attribute(variable_path: str) -> Any:
    """导入模块并取出属性，把两类失败都转成能定位到配置的 ImportError。"""
    module_path, variable_name = _split_path(variable_path)

    try:
        module = importlib.import_module(module_path)
    except ImportError as err:
        # 区分「模块名拼错」与「模块存在但它自己的依赖缺失」—— 后者的提示
        # 应指向缺失的那个依赖，而不是让人去改 use: 路径。
        if getattr(err, "name", None) == module_path.split(".", 1)[0]:
            raise ImportError(
                f"无法导入 use: {variable_path!r} 中的模块 {module_path!r}。"
                f"请检查 config.yaml 的 use: 是否拼写正确（注意不是依赖缺失）。原始错误：{err}"
            ) from err
        raise ImportError(f"导入 {module_path!r} 时其内部依赖出错（use: {variable_path!r}）：{err}") from err

    try:
        return getattr(module, variable_name)
    except AttributeError as err:
        raise ImportError(f"模块 {module_path!r} 中不存在 {variable_name!r}（use: {variable_path!r}）") from err


def _type_name(expected_type: type | tuple[type, ...]) -> str:
    if isinstance(expected_type, tuple):
        return " 或 ".join(t.__name__ for t in expected_type)
    return expected_type.__name__


def resolve_variable[T](
    variable_path: str,
    expected_type: type[T] | tuple[type, ...] | None = None,
) -> T:
    """按 ``"模块路径:属性名"`` 取出对象，可选校验类型。

    Args:
        variable_path: 形如 ``metaphys.tools.builtins.bazi_chart:bazi_chart_tool``
        expected_type: 传入则用 ``isinstance`` 校验，不符即报错

    Returns:
        解析出的对象。

    Raises:
        ImportError: 路径格式非法、模块无法导入、属性不存在。
        TypeError: 对象存在但类型不符。
    """
    variable = _load_attribute(variable_path)

    if expected_type is not None and not isinstance(variable, expected_type):
        raise TypeError(
            f"{variable_path!r} 解析出的对象是 {type(variable).__name__}，但期望 {_type_name(expected_type)}"
        )

    return variable


def resolve_class[T](
    class_path: str,
    expected_type: type[T] | tuple[type, ...] | None = None,
) -> type[T]:
    """按 ``"模块路径:类名"`` 取出类，可选校验其是否为期望类型的子类。

    与 :func:`resolve_variable` 分开，是因为**校验方向相反**：这里要的是
    ``issubclass`` 而非 ``isinstance`` —— 拿到的是类本身，不是实例。

    Raises:
        ImportError: 路径格式非法、模块无法导入、属性不存在。
        TypeError: 取到的不是类，或不是期望类型的子类。
    """
    candidate = _load_attribute(class_path)

    if not isinstance(candidate, type):
        raise TypeError(f"{class_path!r} 解析出的不是类，而是 {type(candidate).__name__} 实例")

    if expected_type is not None and not issubclass(candidate, expected_type):
        raise TypeError(f"{class_path!r} 是 {candidate.__name__}，不是 {_type_name(expected_type)} 的子类")

    return candidate


__all__ = ["resolve_class", "resolve_variable"]
