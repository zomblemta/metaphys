"""命盘契约模型 —— 排盘引擎与 LLM 之间的唯一接口。

架构核心不变量的载体：引擎输出必须先通过这里的校验，才能进入 LLM 上下文。
LLM 永远不产出这些字段，只消费它们。

M1 定义八字，M3 补上星盘。两套体系各有自己的 ``kind``，在 state 的 ``charts``
槽里并存（见 ``agents/thread_state.merge_charts``）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"


class Calendar(StrEnum):
    SOLAR = "solar"  # 公历
    LUNAR = "lunar"  # 农历


class TimeAccuracy(StrEnum):
    """出生时间精度分级。

    多数用户不知道准确出生时间。不支持降级，产品就会持续产出错误命盘
    且用户无从察觉 —— 因此精度是必填语义，解读层必须据此降级表述。
    """

    EXACT = "exact"  # 精确到分钟：四柱完整
    HOUR_KNOWN = "hour_known"  # 只知时辰：四柱完整（时辰内）
    UNKNOWN = "unknown"  # 时辰未知：仅三柱，时柱缺失


class BirthProfile(BaseModel):
    """出生信息 —— 排盘的唯一输入。

    ``gender`` **可选**：它是**八字专用的输入**（决定大运顺逆），不是出生信息
    的固有属性。星盘完全不需要它，而两个工具共用 ``birth_profile`` 这一个
    state 槽 —— 让星盘工具为了写一个自己用不上的字段去跟用户索要性别，是错的。

    代价是"缺失"有了两种含义：八字路径下它是**必须**的。这个约束由
    :func:`metaphys.engines.bazi.compute_bazi` 在**用到它的那一刻**守住，
    而不是靠这里的必填 —— 因为 ``None`` 一旦流到大运方向判断里，
    ``None is Gender.MALE`` 会静默取假，把男命排成女命，用户完全看不出来。
    """

    name: str = ""
    gender: Gender | None = None
    birth_datetime: datetime
    place: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    time_accuracy: TimeAccuracy = TimeAccuracy.EXACT
    calendar: Calendar = Calendar.SOLAR
    is_leap_month: bool = Field(
        default=False,
        description="仅农历有意义。闰月必须显式声明 —— 否则该月会被当作普通月份解析成错误日期",
    )

    @field_validator("place")
    @classmethod
    def _place_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("出生地不能为空 —— 真太阳时与星盘都依赖经度")
        return v.strip()


class Pillar(BaseModel):
    """一柱：天干 + 地支。"""

    stem: str
    branch: str
    stem_ten_god: str | None = None  # 十神（日柱天干为"日主"）
    hidden_stems: list[str] = Field(default_factory=list)
    branch_ten_gods: list[str] = Field(default_factory=list)
    na_yin: str | None = None  # 纳音
    di_shi: str | None = None  # 十二长生（地势）

    @property
    def gan_zhi(self) -> str:
        return f"{self.stem}{self.branch}"


class SolarTermMark(BaseModel):
    """节气边界标记 —— 用于提示"出生时刻贴近交接点"的高风险情形。"""

    name: str
    at: datetime
    minutes_away: float


class DaYun(BaseModel):
    """一步大运。"""

    index: int
    gan_zhi: str
    start_age: int
    start_year: int
    end_year: int


class School(StrEnum):
    """命理流派。不同流派对旺衰轻重的取法不同，结论须标注适用前提。"""

    ZIPING = "ziping"  # 子平：以月令为纲，重格局
    MANGPAI = "mangpai"  # 盲派：重做功宾主，不重量化打分
    XINPAI = "xinpai"  # 新派：量化打分，各柱权重较均衡


class ShenSha(BaseModel):
    """一条神煞命中记录。

    刻意记下 ``base``（以何为准）与 ``pillar``（命中哪一柱），而非只给名字 ——
    神煞的查法流派分歧大，不写清依据，用户无法复核。
    """

    name: str
    base: str  # 判定基准："日干" / "年支" / "日支"
    pillar: str  # 命中的柱："年" / "月" / "日" / "时"
    branch: str  # 命中的地支


class ElementScore(BaseModel):
    """单个五行的旺衰得分。"""

    element: str
    score: float
    percent: float


class StrengthAnalysis(BaseModel):
    """日主旺衰强弱评分。

    **流派相关**：本评分是一种可复核的量化方案，不是唯一正解。
    给出 ``school`` 与完整分量，使结论可追溯、可复算。
    """

    school: School
    day_master_element: str
    scores: list[ElementScore]
    support_score: float = Field(description="同党：比劫（同我）+ 印（生我）")
    oppose_score: float = Field(description="异党：食伤（我生）+ 财（我克）+ 官杀（克我）")
    support_ratio: float
    verdict: str = Field(description="身强 / 偏强 / 中和 / 偏弱 / 身弱")
    is_rooted: bool = Field(description="日主是否通根于地支")
    rooted_in: list[str] = Field(default_factory=list, description="通根的地支")
    transparent_stems: list[str] = Field(default_factory=list, description="透干的天干")
    notes: list[str] = Field(default_factory=list)


class BaziChart(BaseModel):
    """八字命盘 —— 由确定性引擎产出，LLM 只读。"""

    # 输入回显（前端展示与 LLM 上下文必须是同一份数据）
    profile: BirthProfile

    # 真太阳时校正
    clock_time: datetime = Field(description="用户报出的钟表时间")
    true_solar_time: datetime = Field(description="校正后的出生地真太阳时")
    utc_offset_hours: int
    equation_of_time_minutes: float
    day_rolled: bool = Field(default=False, description="真太阳时是否跨日 —— 影响日柱")

    # 四柱
    year_pillar: Pillar
    month_pillar: Pillar
    day_pillar: Pillar
    time_pillar: Pillar | None = Field(default=None, description="时辰未知时为 None")

    day_master: str = Field(description="日干 —— 命主")

    # 扩展
    tai_yuan: str | None = None
    ming_gong: str | None = None
    shen_gong: str | None = None
    tai_xi: str | None = None

    # 运势
    qi_yun_desc: str | None = None
    da_yun: list[DaYun] = Field(default_factory=list)

    # 神煞与旺衰
    school: School = School.ZIPING
    shen_sha: list[ShenSha] = Field(default_factory=list)
    strength: StrengthAnalysis | None = None

    # 质量标记
    warnings: list[str] = Field(default_factory=list)
    nearby_solar_term: SolarTermMark | None = None
    engine_version: str = "m1"

    @property
    def pillars(self) -> list[Pillar]:
        """已排出的柱，按年/月/日/时顺序。时柱缺失时略过。"""
        return [p for p in (self.year_pillar, self.month_pillar, self.day_pillar, self.time_pillar) if p]

    @property
    def four_pillars(self) -> str:
        return " ".join(p.gan_zhi for p in self.pillars)


# --------------------------------------------------------------------------- #
# 星盘
# --------------------------------------------------------------------------- #
class HouseSystem(StrEnum):
    """宫位制 —— 星盘的**前提声明**，与八字的 ``School`` 同位。

    值是 kerykeion 的宫位制标识符（单字母），由它自己校验。取值收窄到常用的
    八种：每多一种，SVG 上就多一个需要简体化的宫位制名称，而多出来的那些
    几乎不会被用到。
    """

    PLACIDUS = "P"  # 普拉西达斯：现代最常用；高纬度会退化，kerykeion 会退回整宫
    KOCH = "K"  # 科赫
    EQUAL = "A"  # 等宫：从上升点起每宫 30°
    WHOLE_SIGN = "W"  # 整宫：以星座为宫，古典技法常用
    PORPHYRY = "O"  # 波菲里
    REGIOMONTANUS = "R"  # 雷焦蒙塔努斯
    CAMPANUS = "C"  # 坎帕努斯
    ALCABITIUS = "B"  # 阿卡比提乌斯


class ZodiacType(StrEnum):
    """黄道制 —— 同属前提声明，直接决定每个星座的起止度数。"""

    TROPICAL = "tropical"  # 回归黄道：以春分点为 0°，现代通行
    SIDEREAL = "sidereal"  # 恒星黄道：以固定恒星为基准，与回归黄道差一个岁差


class AstroPoint(BaseModel):
    """一个点（行星或轴点）在盘上的位置。"""

    key: str = Field(description="稳定英文键，如 sun / ascendant —— 供程序判断")
    name: str = Field(description="简体中文名，如 太阳 / 上升")
    sign: str = Field(description="所在星座（简体中文）")
    sign_index: int = Field(ge=0, le=11, description="星座序号，白羊为 0")
    position: float = Field(ge=0, lt=30, description="星座内度数")
    abs_pos: float = Field(ge=0, lt=360, description="黄经 — 绝对位置")
    house: int | None = Field(default=None, ge=1, le=12, description="所在宫位序号")
    retrograde: bool = False


class HouseCusp(BaseModel):
    """一个宫头。"""

    index: int = Field(ge=1, le=12)
    sign: str
    position: float
    abs_pos: float


class AspectRef(BaseModel):
    """一条相位。

    ``p1``/``p2`` 是简体中文名，顺序**不固定** —— 核验时按无序对处理，
    否则「月亮刑太阳」会被判成不存在。
    """

    p1: str
    p2: str
    aspect: str = Field(description="简体中文相位名，如 刑")
    orbit: float = Field(description="容许度偏差（度）")


class AstroChart(BaseModel):
    """星盘 —— 由确定性引擎产出，LLM 只读。

    与 :class:`BaziChart` 对称：**没有** ``kind`` 字段。"这是什么盘"由
    ``state.charts`` 的键承载，模型里再存一份就有两个真源，可以互相矛盾。
    """

    # 输入回显（前端展示与 LLM 上下文必须是同一份数据）
    profile: BirthProfile

    # 前提声明 —— 离开宫位制与黄道制，星盘的结论没有意义
    house_system: HouseSystem = HouseSystem.PLACIDUS
    zodiac_type: ZodiacType = ZodiacType.TROPICAL
    sidereal_mode: str | None = Field(default=None, description="仅恒星黄道需要")

    # 时刻。utc_offset_hours 与八字盘对照即可发现"两张盘不在同一瞬间"
    utc_datetime: datetime
    utc_offset_hours: int = Field(description="中国钟表时间实际使用的偏移（8 或 9）")

    points: list[AstroPoint] = Field(default_factory=list)
    houses: list[HouseCusp] = Field(default_factory=list)
    aspects: list[AspectRef] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)
    engine_version: str = "m3"

    def point(self, key: str) -> AstroPoint | None:
        """按英文键取点（``sun`` / ``ascendant`` …）。"""
        return next((p for p in self.points if p.key == key), None)
