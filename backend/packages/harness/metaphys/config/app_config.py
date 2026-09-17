"""应用配置 —— ``config.yaml`` 的唯一读取口。

三件事在这里定死：

1. **缺变量即报错。** ``$DEEPSEEK_API_KEY`` 在加载时展开，取不到就抛 ``ValueError``。
   若留成空字符串，服务会正常启动，直到第一次真实请求才炸 —— 那时错误信息
   离病因已经很远，排障要重走一遍配置链路。
2. **多余键透传。** ``ModelConfig`` / ``ToolConfig`` 都是 ``extra="allow"``：
   ``temperature`` 这类键由模型构造函数消费，这里不重复声明一遍参数表。
   代价是拼错的键不报错，所以下面的 ``_METADATA_KEYS`` 要把"配置元数据"
   和"构造参数"分清楚。
3. **``school`` 在这里就校验。** 它是全部解读结论的前提，写错流派会让整份
   解读的前提失准；等到排盘时才失败太晚，也不能被静默忽略。
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Self

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

from metaphys.schemas.chart import HouseSystem, School, ZodiacType

CONFIG_FILENAME = "config.yaml"
ENV_PATH_VAR = "METAPHYS_CONFIG_PATH"

# 只认 $VAR / ${VAR} 两种写法；本项目用不到 $$ 转义。
_ENV_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")

#: 属于"配置框架"而非"构造参数"的键，不往下传给模型构造函数。
_METADATA_KEYS = frozenset({"name", "use", "display_name"})


# --------------------------------------------------------------------------- #
# $ENV 展开
# --------------------------------------------------------------------------- #
def _expand_env(node: Any, *, where: str) -> Any:
    """递归展开 ``$VAR``。

    ``where`` 是出错的键路径（如 ``models[0].api_key``）—— 报错必须能直接
    定位到 config.yaml 的哪一处，否则"缺环境变量"这句话没有可操作性。
    """
    if isinstance(node, str):

        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            value = os.environ.get(name)
            # 空串与纯空白都视为缺失：`.env` 里写了键却没填值、或 CI 里设成 " "，
            # 都是常见疏忽。放行只会把故障推迟到请求期 —— 那时报的是 401，
            # 与"密钥没配"看起来是两回事，排障成本高得多。
            if not value or not value.strip():
                raise ValueError(
                    f"config.yaml 的 {where} 引用了 ${name}，但该环境变量未设置或为空。"
                    f"请把它写进 config.yaml 同级的 .env（可从 .env.example 复制）。"
                )
            return value

        return _ENV_REF.sub(_replace, node)

    if isinstance(node, dict):
        return {key: _expand_env(value, where=f"{where}.{key}") for key, value in node.items()}

    if isinstance(node, list):
        return [_expand_env(item, where=f"{where}[{index}]") for index, item in enumerate(node)]

    return node


# --------------------------------------------------------------------------- #
# 配置模型
# --------------------------------------------------------------------------- #
class ModelConfig(BaseModel):
    """一条模型配置。

    ``use`` 是点分路径（``模块:类``），由 ``reflection.resolve_class`` 解析；
    其余键一律透传给模型构造函数。
    """

    model_config = ConfigDict(extra="allow")

    name: str
    use: str
    display_name: str | None = None

    def constructor_kwargs(self) -> dict[str, Any]:
        """给模型构造函数的参数 —— 去掉配置元数据，去掉 None。

        去掉 None 是必要的：``api_key=None`` 会覆盖 provider 自己从环境变量
        读到的值，把"没配"变成"显式置空"。
        """
        return {key: value for key, value in self.model_dump(exclude_none=True).items() if key not in _METADATA_KEYS}


class ToolConfig(BaseModel):
    """一条工具配置。``use`` 指向 ``@tool`` 装饰后的对象。"""

    model_config = ConfigDict(extra="allow")

    name: str
    use: str


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_iterations: int = Field(default=12, ge=1)


class BaziConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    school: School = School.ZIPING
    true_solar_time: bool = True


class AstroConfig(BaseModel):
    """星盘参数。

    ``house_system`` / ``zodiac_type`` 是**全部星盘结论的前提**，与八字的
    ``school`` 同位：离开它们，"太阳在双子"这句话没有意义（换宫位制，宫位就
    变了；换黄道制，星座就变了）。所以它们在这里就校验，并且会被写进
    ``AstroChart`` 供解读层标注。
    """

    model_config = ConfigDict(extra="allow")

    house_system: HouseSystem = HouseSystem.PLACIDUS
    zodiac_type: ZodiacType = ZodiacType.TROPICAL
    sidereal_mode: str | None = None
    #: SVG 输出目录。相对路径按 **config.yaml 所在目录**解析（见工具的
    #: ``resolve_svg_dir``），这样"从哪跑"不影响"写到哪"。
    svg_dir: str = "var/charts"

    @model_validator(mode="after")
    def _check_sidereal_mode(self) -> Self:
        """恒星黄道必须指定模式 —— 照 ``bazi.school`` 的先例，写错在加载时就失败。

        留到排盘时才报错的话，用户已经等了一轮对话才被告知"配置错了"，
        而这是部署方的问题，不该由用户承担。
        """
        if self.zodiac_type is ZodiacType.SIDEREAL and not self.sidereal_mode:
            raise ValueError(
                "astro.zodiac_type=sidereal 时必须同时指定 astro.sidereal_mode"
                "（如 LAHIRI / FAGAN_BRADLEY / KRISHNAMURTI），否则每颗星的星座都无从确定"
            )
        return self


class GatewayConfig(BaseModel):
    """M4 本地 HTTP 网关参数。"""

    model_config = ConfigDict(extra="allow")

    host: str = "127.0.0.1"
    port: int = Field(default=8010, ge=1, le=65535)


class AppConfig(BaseModel):
    """``config.yaml`` 的完整结构。"""

    model_config = ConfigDict(extra="allow")

    config_version: int = 1
    log_level: str = "info"
    models: list[ModelConfig] = Field(default_factory=list)
    default_model: str | None = None
    tools: list[ToolConfig] = Field(default_factory=list)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    bazi: BaziConfig = Field(default_factory=BaziConfig)
    # 必须显式声明：本模型是 extra="allow"，不写这一行的话 config.yaml 里的
    # ``astro:`` 段会被**静默吞掉**，然后所有星盘都按默认参数排出来。
    astro: AstroConfig = Field(default_factory=AstroConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if not self.models:
            raise ValueError("config.yaml 未配置任何模型（models 为空）")

        duplicate_models = _duplicates([m.name for m in self.models])
        if duplicate_models:
            raise ValueError(f"models 中 name 重复：{duplicate_models} —— 按名字取模型会有歧义")

        model_names = {m.name for m in self.models}
        if self.default_model is not None and self.default_model not in model_names:
            raise ValueError(f"default_model={self.default_model!r} 不在 models 中；已配置：{sorted(model_names)}")

        duplicate_tools = _duplicates([t.name for t in self.tools])
        if duplicate_tools:
            # 工具重名会让模型看到两个同名 tool，调用结果不可预期，直接拒绝启动。
            raise ValueError(f"tools 中 name 重复：{duplicate_tools}")

        return self

    def get_model_config(self, name: str | None = None) -> ModelConfig:
        """按名字取模型配置；``name`` 为 None 时取 ``default_model``（再退到第一个）。"""
        target = name or self.default_model or self.models[0].name
        for model_config in self.models:
            if model_config.name == target:
                return model_config
        raise ValueError(f"未找到名为 {target!r} 的模型；已配置：{[m.name for m in self.models]}")

    def get_tool_configs(self) -> list[ToolConfig]:
        return list(self.tools)


def _duplicates(names: list[str]) -> list[str]:
    """返回重复出现的名字（去重、保持首次出现顺序）。"""
    seen: set[str] = set()
    dupes: list[str] = []
    for name in names:
        if name in seen and name not in dupes:
            dupes.append(name)
        seen.add(name)
    return dupes


# --------------------------------------------------------------------------- #
# 加载
# --------------------------------------------------------------------------- #
def find_config_path() -> Path:
    """定位 ``config.yaml``。

    顺序：``$METAPHYS_CONFIG_PATH`` → 从 cwd 逐级向上 → 从本文件逐级向上。
    后两者覆盖了"从 backend/ 跑 pytest"与"从仓库根跑脚本"两种常见情形。
    """
    override = os.environ.get(ENV_PATH_VAR)
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"${ENV_PATH_VAR}={override!r} 指向的文件不存在")
        return path.resolve()

    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for base in (start, *start.parents):
            candidate = base / CONFIG_FILENAME
            if candidate.is_file():
                return candidate

    raise FileNotFoundError(
        f"从 {Path.cwd()} 与 {Path(__file__).resolve().parent} 向上都找不到 {CONFIG_FILENAME}；"
        f"可用 ${ENV_PATH_VAR} 显式指定路径。"
    )


def load_app_config(path: Path | None = None) -> AppConfig:
    """读取并校验配置。每次都重读盘，不缓存 —— 缓存见 :func:`get_app_config`。

    Raises:
        FileNotFoundError: 找不到 config.yaml。
        ValueError: YAML 非法、``$ENV`` 缺失，或结构校验不通过
            （``ValidationError`` 是其子类）。
    """
    config_path = path or find_config_path()

    # 真环境变量优先于 .env：CI 注入的密钥不应被本地残留的 .env 覆盖。
    load_dotenv(config_path.parent / ".env", override=False)

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise ValueError(f"{config_path} 不是合法的 YAML：{err}") from err

    if raw is None:
        raise ValueError(f"{config_path} 是空文件")
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path} 的顶层必须是映射（mapping），实际是 {type(raw).__name__}")

    return AppConfig.model_validate(_expand_env(raw, where="(顶层)"))


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    """进程内单例。给生产路径用。"""
    return load_app_config()


def reload_app_config() -> AppConfig:
    """清缓存后重读 —— 测试与开发期改配置用。"""
    get_app_config.cache_clear()
    return get_app_config()


__all__ = [
    "CONFIG_FILENAME",
    "ENV_PATH_VAR",
    "AgentConfig",
    "AppConfig",
    "AstroConfig",
    "BaziConfig",
    "GatewayConfig",
    "ModelConfig",
    "ToolConfig",
    "find_config_path",
    "get_app_config",
    "load_app_config",
    "reload_app_config",
]
