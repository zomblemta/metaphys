"""架构护栏：分层依赖方向，以及"路由不再触碰图内部"。

仿 :mod:`tests.test_harness_boundary` —— 那次证明了**机械可查的约束**比注释
活得久。本次拆分引入的依赖方向同样写成断言，因为它的破坏方式全是静默的：

- ``application/`` 里 import 一次 FastAPI，业务规则就再也无法脱离 Web 框架
  被推理与测试。不会有报错，只会有"为什么这个服务方法需要先造一个 Request"。
- 某个路由伸手读 ``runner._graph``，换 checkpointer 实现时就要改路由。
  同样不报错，直到真的去换。
- ``gateway/routers/`` 直接 import ``infrastructure``，端口层就成了摆设：
  路由能绕开应用服务直接改存储。

每一条都对应一个具体的、曾经真实发生过的问题，不是洁癖。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"

WEB_FRAMEWORKS = {"fastapi", "starlette", "uvicorn"}
#: 分层的实现细节包。上层可以知道下层的**端口**，但不知道实现。
INFRASTRUCTURE = {"app.infrastructure", "app.bootstrap"}
GATEWAY = {"app.gateway"}
APPLICATION = {"app.application"}

#: 只允许出现在适配器里的名字 —— 它们指向图/checkpointer 的内部结构。
AGENT_INTERNALS = {"_graph", "checkpointer"}
EXECUTION_ADAPTER = APP_ROOT / "infrastructure" / "execution.py"


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path) -> set[str]:
    """该模块 import 的全部绝对模块名（不截断到顶层包）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _matches(modules: set[str], prefixes: set[str]) -> set[str]:
    """按**前缀**匹配：``app.infrastructure`` 也要命中 ``app.infrastructure.memory``。"""
    return {module for module in modules for prefix in prefixes if module == prefix or module.startswith(prefix + ".")}


def _agent_internal_references(path: Path) -> set[str]:
    """模块里对图内部名字的**真实引用**（而非文档字符串里的提及）。

    三种写法都要抓：``x._graph``、``checkpointer = …``、
    ``getattr(x, "checkpointer")``。第三种是纯字符串，光看属性名会漏掉 ——
    而这正是适配器实际使用的写法。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in AGENT_INTERNALS:
            found.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in AGENT_INTERNALS:
            found.add(node.id)
        elif isinstance(node, ast.Call):
            func = node.func
            is_getattr = (isinstance(func, ast.Name) and func.id == "getattr") or (
                isinstance(func, ast.Attribute) and func.attr == "getattr"
            )
            if is_getattr and len(node.args) >= 2:
                name = node.args[1]
                if isinstance(name, ast.Constant) and name.value in AGENT_INTERNALS:
                    found.add(name.value)

    return found


# --------------------------------------------------------------------------- #
# 护栏自身要有意义
# --------------------------------------------------------------------------- #


def test_scan_targets_exist():
    for directory in ("domain", "application", "infrastructure", "gateway"):
        assert (APP_ROOT / directory).is_dir(), f"未找到 app/{directory}，护栏形同虚设"
    assert EXECUTION_ADAPTER.is_file(), f"未找到适配器 {EXECUTION_ADAPTER}"


# --------------------------------------------------------------------------- #
# 依赖方向
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", _files(APP_ROOT / "domain"), ids=lambda p: p.name)
def test_domain_depends_on_nothing(path: Path):
    """领域层是最内层：不依赖 Web 框架、应用层、基础设施或网关。"""
    forbidden = WEB_FRAMEWORKS | APPLICATION | INFRASTRUCTURE | GATEWAY
    offenders = _matches(_imported_modules(path), forbidden)
    assert not offenders, f"app/domain/{path.name} 依赖了 {sorted(offenders)} —— 领域层必须无依赖"


@pytest.mark.parametrize("path", _files(APP_ROOT / "application"), ids=lambda p: p.name)
def test_application_has_no_framework_or_implementation(path: Path):
    """应用层只依赖 domain 与 ports，不依赖 Web 框架，也不依赖任何实现。"""
    offenders = _matches(_imported_modules(path), WEB_FRAMEWORKS | INFRASTRUCTURE | GATEWAY)
    assert not offenders, (
        f"app/application/{path.name} 依赖了 {sorted(offenders)} —— "
        f"业务规则必须能在没有 Web 框架、没有真实存储的情况下被推理"
    )


@pytest.mark.parametrize("path", _files(APP_ROOT / "infrastructure"), ids=lambda p: p.name)
def test_infrastructure_has_no_web_framework(path: Path):
    """基础设施实现端口，不认识 HTTP。认识 HTTP 就没法在非 Web 场景复用。"""
    offenders = _matches(_imported_modules(path), WEB_FRAMEWORKS)
    assert not offenders, f"app/infrastructure/{path.name} 依赖了 {sorted(offenders)}"


@pytest.mark.parametrize("path", _files(APP_ROOT / "gateway" / "routers"), ids=lambda p: p.name)
def test_routers_do_not_reach_into_infrastructure(path: Path):
    """路由只能经应用服务访问数据，不能自己 new 仓储。"""
    offenders = _matches(_imported_modules(path), INFRASTRUCTURE)
    assert not offenders, (
        f"app/gateway/routers/{path.name} 依赖了 {sorted(offenders)} —— 路由直接操作存储会让端口层变成摆设"
    )


# --------------------------------------------------------------------------- #
# 图内部只在一个模块里被提及
# --------------------------------------------------------------------------- #


def test_only_the_execution_adapter_touches_agent_internals():
    """``_graph`` / ``checkpointer`` 只允许出现在 ``infrastructure/execution.py``。

    这正是"路由不再触碰 ``runner._graph``"的机械化版本：换 checkpointer 实现
    时要改的地方因此只有一处，而这一处是专门为此存在的。
    """
    offenders = {
        str(path.relative_to(BACKEND_ROOT)): sorted(names)
        for path in _files(APP_ROOT)
        if path != EXECUTION_ADAPTER and (names := _agent_internal_references(path))
    }
    assert not offenders, (
        f"这些模块触碰了图内部结构：{offenders} —— 它们应当经 application/ports.py 的 AgentExecutor 端口"
    )


def test_the_guardrail_can_actually_fail(tmp_path: Path):
    """反例自检：三种写法都真的会被抓出来。

    没有这条，"护栏恒过"与"代码干净"看起来完全一样。
    """
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def f(x):\n    return x._graph\n\ndef g(service):\n    return getattr(service, 'checkpointer', None)\n"
        "\ndef h():\n    checkpointer = 1\n",
        encoding="utf-8",
    )
    assert _agent_internal_references(sample) == {"_graph", "checkpointer"}


def test_docstring_mentions_are_not_references(tmp_path: Path):
    """提及不是引用：文档里写 ``_graph`` 不该算违规，否则没人敢解释这件事。"""
    sample = tmp_path / "doc.py"
    sample.write_text('"""这里解释为什么不再读 runner._graph 与 checkpointer。"""\n', encoding="utf-8")
    assert _agent_internal_references(sample) == set()
