"""架构护栏：harness 不得依赖 app。

沿用 mini-flow-2 / deer-flow 的两层单向依赖约定：

    app/gateway/（FastAPI）  →  packages/harness/metaphys/
                    ↑ 只允许这一个方向

反向依赖会让排盘引擎与 agent 逻辑被 Web 框架绑架 —— 引擎将无法脱离
服务独立测试，而"排盘引擎可独立测试"正是本项目准确性的前提。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness" / "metaphys"
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 明令禁止出现在 harness 中的顶层包
FORBIDDEN_ROOTS = {"app", "fastapi", "starlette", "uvicorn"}


def _python_files() -> list[Path]:
    return sorted(p for p in HARNESS_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_roots(path: Path) -> set[str]:
    """收集一个模块里所有 import 的顶层包名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        # 相对 import（level > 0）不跨包，天然安全
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    return roots


def test_harness_package_exists():
    """护栏本身要有意义 —— 先确认扫描目标存在。"""
    assert HARNESS_ROOT.is_dir(), f"未找到 harness 包：{HARNESS_ROOT}"
    assert _python_files(), "harness 包内没有任何 Python 文件，护栏形同虚设"


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(BACKEND_ROOT)))
def test_harness_does_not_import_app(path: Path):
    """harness 的任何模块都不得 import app 或 Web 框架。"""
    offenders = _imported_roots(path) & FORBIDDEN_ROOTS
    assert not offenders, (
        f"{path.relative_to(BACKEND_ROOT)} 依赖了 {sorted(offenders)} —— harness 必须能脱离 Web 框架独立运行与测试"
    )


def test_engines_have_no_llm_dependency():
    """排盘引擎不得依赖任何 LLM / agent 框架。

    这是架构核心不变量"LLM 不得参与任何数值推算"在依赖层面的体现：
    引擎若能 import 到模型客户端，就存在让 LLM 参与推算的路径。
    """
    forbidden = {
        "langchain",
        "langgraph",
        "langchain_core",
        "langchain_openai",
        "openai",
        "anthropic",
        "httpx",
        "requests",
    }

    violations: list[str] = []
    for path in sorted((HARNESS_ROOT / "engines").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        offenders = _imported_roots(path) & forbidden
        if offenders:
            violations.append(f"{path.relative_to(BACKEND_ROOT)} -> {sorted(offenders)}")

    assert not violations, "排盘引擎出现 LLM/网络依赖：\n" + "\n".join(violations)


# --------------------------------------------------------------------------- #
# M2：推算能力只能存在于 engines/ 与 tools/
# --------------------------------------------------------------------------- #
#: 历法与天文库 —— 有了它们，一个模块就**有能力**自行推算干支、节气、星历。
CALENDAR_ROOTS = {"lunar_python", "swisseph", "kerykeion", "ephem", "skyfield", "flatlib"}

#: 允许持有推算能力的包。它们是命盘数据的**产地**，其余一切只是消费者。
CALENDAR_ALLOWED_PREFIXES = ("engines", "tools")


def test_only_engines_and_tools_may_touch_calendar_libraries():
    """除 ``engines/`` 与 ``tools/`` 外，任何 harness 模块都不得 import 历法/天文库。

    这是 M1 那条"解析引擎不许依赖 LLM"的**反向护栏**，也是 M2 之后风险真正所在：
    M1 时 LLM 侧还不存在，风险是"引擎被 Web 框架绑架"；M2 之后有了中间件、提示词、
    运行服务，风险变成**有人在 LLM 侧顺手算一下** —— 比如在 Grounding 中间件里
    现算一个干支来比对，或在运行服务里补一个缺失的时柱。

    那种代码看起来只是"补一个值"，实际却把"命盘只能来自确定性引擎"这条立身之本
    蛀空了：一旦 LLM 侧能算，算错就没有任何东西拦得住，而用户看不出命盘是错的。

    从依赖层面禁止，比在代码评审里盯人要可靠 —— 有没有这个能力是机械可查的。
    """
    violations: list[str] = []
    for path in _python_files():
        relative = path.relative_to(HARNESS_ROOT)
        if relative.parts[0] in CALENDAR_ALLOWED_PREFIXES:
            continue
        offenders = _imported_roots(path) & CALENDAR_ROOTS
        if offenders:
            violations.append(f"{relative} -> {sorted(offenders)}")

    assert not violations, (
        "LLM 侧代码取得了推算能力，命盘数据的来源不再唯一：\n"
        + "\n".join(violations)
        + "\n需要干支/节气/星历时，请经 bazi_chart 工具从 engines/ 取，不要就地算。"
    )


def test_the_calendar_guard_actually_catches_something():
    """护栏要能抓到人 —— 否则它只是一段永远为真的断言。

    直接拿 ``engines/`` 里真实的历法 import 喂给判据：若判据写错（比如包名拼成
    ``swiss_ephemeris``），这里会红。被测的是**判据**，不是路径白名单。
    """
    truesolar = HARNESS_ROOT / "engines" / "bazi" / "truesolar.py"
    chart = HARNESS_ROOT / "engines" / "bazi" / "chart.py"

    assert _imported_roots(truesolar) & CALENDAR_ROOTS == {"swisseph"}, (
        "truesolar.py 确实 import 了 swisseph；判据若认不出来，上面的白名单就是摆设"
    )
    assert _imported_roots(chart) & CALENDAR_ROOTS == {"lunar_python"}

    # 白名单外的路径判定：同一个文件换个位置，就必须被拦下。
    outside = Path("agents") / "lead_agent" / "truesolar.py"
    assert outside.parts[0] not in CALENDAR_ALLOWED_PREFIXES, "白名单把 agents/ 也放进去了"


# --------------------------------------------------------------------------- #
# M3：词表必须能脱离星历表取用
# --------------------------------------------------------------------------- #
#: 星盘词表的模块路径 —— 中间件的落地核验只需要它。
ASTRO_VOCABULARY_MODULE = "metaphys.engines.astro.points"


def _modules_pulled_by(module: str) -> set[str]:
    """在**干净的子进程**里 import ``module``，看它拉进了哪些星历库。

    必须开子进程：pytest 主进程早已被其他测试导入过 kerykeion，就地查
    ``sys.modules`` 会永远为真，测不到任何东西。
    """
    import json
    import os
    import subprocess
    import sys

    probe = (
        "import sys, importlib, json\n"
        f"importlib.import_module({module!r})\n"
        "print(json.dumps(sorted(r for r in ('kerykeion', 'swisseph', 'lunar_python')"
        " if r in sys.modules)))\n"
    )
    # 自己拼 PYTHONPATH，不从环境继承：``make test`` 导出它，但直接跑
    # ``pytest`` 时它并不存在 —— 后者会以 ModuleNotFoundError 的形式失败，
    # 看起来像"判据发现了问题"，实际只是路径没搭好。
    pythonpath = os.pathsep.join(
        [
            str(BACKEND_ROOT),
            str(BACKEND_ROOT / "packages" / "harness"),
            os.environ.get("PYTHONPATH", ""),
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": pythonpath},
    )
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def test_astro_vocabulary_is_reachable_without_an_ephemeris():
    """星盘词表必须能在**不引入 kerykeion** 的前提下取到。

    这条测试守的是 ``engines/astro/points.py`` 存在的全部意义。词表只是字符串，
    核验侧不需要星历表 —— 但 Python 在 import 子模块前一定会先执行父包的
    ``__init__``，所以只要 ``engines/astro/__init__.py`` 顶层 import 了
    ``chart`` 或 ``svg``，那么连 ``from metaphys.engines.astro.points import ...``
    都会把 kerykeion 拉进来（实测如此）。那时"词表与星历表分家"就白分了：
    中间件照样握有推算能力，而护栏只看单文件，看不出这一点。

    故 ``__init__.py`` 用 PEP 562 的模块级 ``__getattr__`` 惰性加载。
    有人日后"顺手"把 import 提到顶层，这条会红。
    """
    assert _modules_pulled_by(ASTRO_VOCABULARY_MODULE) == set()


def test_the_astro_lazy_guard_can_actually_fail():
    """反向样本：判据要能验出"有星历"的情形，否则上一条永远为真。

    拿 ``engines.astro.chart`` —— 它**必须**有 kerykeion。若这里也报空集，
    说明探针写错了（比如子进程没继承 PYTHONPATH），上一条就成了空转。
    """
    assert "kerykeion" in _modules_pulled_by("metaphys.engines.astro.chart")


def test_lazy_shim_still_exports_the_names_it_promises():
    """惰性加载不能把 API 弄丢 —— ``__all__`` 里每个名字都要真能取到。"""
    import importlib

    module = importlib.import_module("metaphys.engines.astro")
    missing = [name for name in module.__all__ if not hasattr(module, name)]
    assert not missing, f"惰性 shim 漏掉了这些名字：{missing}"
