# M3 星盘 —— 交付与发现

> 状态：**M3 已完成** | 日期：2026-09-12
> 428 项通过 + 15 项待密钥（对抗性测试）· ruff check 与 ruff format 全绿

M1 交付了确定性排盘引擎，M2 把「LLM 不参与推算」做成了**机制**（工具化 + 依赖层
护栏 + 运行时落地核验）—— 但只覆盖了八字一条链路。

M3 把同一个命题搬到星盘上，回答一个新问题：

> 同一个机制，换到词表完全不同的另一套体系上，还成立吗？

**答案是部分成立，而不成立的那一半必须被明确说出来** —— 见 §二。这是本期最重要
的结论，也是唯一一条会影响后续排期的结论。

---

## 一、交付物

| 文件 | 职责 |
|---|---|
| `metaphys/engines/astro/points.py` | 简体中文词表的**唯一真源**（12 点 · 12 星座 · 五大相位 · 宫位序号）+ 语言包覆盖表；**刻意不 import kerykeion** |
| `metaphys/engines/astro/chart.py` | `compute_astro()` → `AstroChart`；`ENGINE_VERSION = "m3"`；`AstroError` |
| `metaphys/engines/astro/svg.py` | `render_svg()` —— **纯函数，不落盘** |
| `metaphys/engines/astro/__init__.py` | PEP 562 惰性 shim（见 §四.1） |
| `metaphys/engines/china_time.py` | 中国时区偏移的**跨引擎唯一真源**（从 `engines/bazi` 抽出） |
| `metaphys/schemas/chart.py` | 新增 `AstroChart` / `AstroPoint` / `HouseCusp` / `AspectRef` / `HouseSystem` / `ZodiacType`；`BirthProfile.gender` 改可选 |
| `metaphys/tools/builtins/astro_chart.py` | `astro_chart` 工具：拒绝非精确时间、哈希文件名、自建目录 |
| `metaphys/tools/builtins/birth.py` | 两工具共享的出生信息助手（解析时刻 / 沿用上次出生地 / 追问构造） |
| `metaphys/config/app_config.py` | `AstroConfig`（宫位制 / 黄道制 / 恒星模式 / `svg_dir`）+ 恒星模式校验 |
| `metaphys/middlewares/grounding.py` | `find_astro_fabrications()` —— **只认结构断言**（§二） |
| `metaphys/agents/lead_agent/prompt.py` | 双体系提示词：星盘铁律、体系选择、宫位制/黄道制前提 |
| `metaphys/runtime/run_service.py` | `RunResult.astro` + `summary()` 如实反映排过哪些盘 |

### 测试

| 文件 | 项数 | 覆盖 |
|---|---|---|
| `tests/test_astro_engine.py` | 47 | 引擎、跨引擎同一瞬间、繁简、落盘安全 |
| `tests/test_astro_tool.py` | 22 | 工具：拒绝、文件名、目录、state |
| `tests/test_china_time.py` | 23 | 时区偏移表（跨引擎共用的那个真源） |
| `tests/test_middlewares.py` | 81 | 中间件 + 两套核验规则 + 无盘探测 |
| `tests/test_harness_boundary.py` | 51 | 架构护栏（含词表可达性探针） |
| `tests/test_run_service.py` | 23 | 端到端：抓星盘编造、两张盘共存 |
| 其余（八字 · 神煞 · 配置 · 装配） | 181 | M1/M2 既有覆盖 |
| `tests/test_adversarial.py` | 15 | 真实模型对抗性（**缺 key 时跳过**） |

**验证**：`cd metaphys/backend && make check`

---

## 二、核心发现：星盘的落地核验**天然弱于**八字

八字抓编造靠的是「扫 60 甲子」：干支是两字组合，普通中文散文几乎不会自然出现，
所以**命中了又不在盘上 ≈ 一定是编的**。

这个特征在星盘上**不成立**：「双子座」「天蝎座」是日常词汇，用户自己就会说
「我朋友是处女座」。于是：

- **不能**照搬裸词扫描 —— 必然误报，而且误报的是用户自己的话。
- **只能**扫**结构化断言**：`太阳在双子` / `上升落狮子` / `太阳刑月亮`。

后果是一条**能力边界**，不是实现细节：

| 模型的行为 | 抓得住吗 |
|---|---|
| 把太阳说成双子（盘上是巨蟹） | ✅ 抓得住 |
| 把月亮安错宫 | ✅ 抓得住 |
| 说一个盘上没有的相位 | ✅ 抓得住 |
| 顺口提一个星座而不作断言（「双子座的人通常……」） | ❌ **抓不住** |
| 用列表式 / 无谓语的写法绕开句式 | ❌ **抓不住** |

`grounding.py` 的模块 docstring 把这两条缺口与一个已知误报（「太阳在狮子座的人
喜欢被关注」—— 形如断言，实为泛指）都写明了，并有测试钉住。**写下来是为了让下
一个人不要"顺手补上"裸词扫描** —— 那会把一类已知的漏报换成一类未知的误报。

**直接后果：星盘这条链路上，提示词与对抗性测试的权重比八字更高。** 机制能兜的底
变少了，`prompt.py` 里星盘那一节因此写得比八字细，这是刻意的，不要为了整齐压缩
回去（该文件的模块 docstring 里也记了这一条）。

---

## 三、kerykeion 5.x 实测

M0 的 H4 是在**旧 API** 上做的验证，本期实测发现 API 已换代，且多出几个 M0 没记的坑。

### 1. API 换代，且旧 API 会发弃用警告

正式的 v5 API 是工厂 + Pydantic 模型：
`AstrologicalSubjectFactory.from_birth_data(...)` → `ChartDataFactory.create_natal_chart_data(...)`
→ `ChartDrawer(...).generate_svg_string()`（**纯函数**），相位另走 `AspectsFactory`。
M0 记录的 `AstrologicalSubject` / `KerykeionChartSVG` 已挪进 `kerykeion.backword`
（拼写如此），一用就发 `DeprecationWarning`。**M3 一律用 v5 API。**

### 2. M0 三坑的现状复核

| M0 记录 | 现状 |
|---|---|
| 不传 `city`/`nation` 会默认 Greenwich/GB | **仍在**。我们显式传 `city`/`nation="CN"`，并有测试断言盘上的地名不是 Greenwich。`online=False` 时若不传 `tz_str`/`lat`/`lng`，直接抛 `KerykeionException`，不会静默取默认值 |
| SVG 默认写用户主目录 | **仍在**（`output_path=None` → `Path.home()`）。**新增一条**：`save_svg` **不创建目录**，目录不存在直接 `FileNotFoundError` |
| `chart_language` 正确值是 `"CN"` | **仍在**，但它在 `ChartDrawer` 上，不在 subject 上 |

### 3. 新坑：`"CN"` 出来的中文是**繁简混排**

M0 只核对了「行星名是中文、英文残留为 0」，没核对繁简一致性。实测同一张 SVG 里
`宫位划分`（简体宫）与 `宮 1:`（繁体宮）并存，含繁体字的串有 11 个。**对中文产品
这是硬伤。**

修法是 `ChartDrawer(..., language_pack=PACK)` 逐键覆盖，但有两个陷阱：

- **传参形态是静默陷阱。** 源码是 `overrides = {self.chart_language: dict(language_pack)}`，
  所以要传**该语言那一层**的 dict，**不是** `{"CN": {...}}`。传错形态**不报错、
  也不生效** —— 与 M2 的 DeepSeek 坑同一类：错法比正法安静。已用正反两个样本钉住。
- **覆盖是逐键的，一轮补不齐。** 实测一轮部分覆盖后 `宫`/`東`/`視` 仍在，它们来自
  另外几个键。所以 `points.py` 里逐键补全，**验收线是一条测试**：渲染结果里不得
  出现任何繁体字（按**字**列判据，不是按词 —— 按词会漏掉日后新出现的键）。

### 4. 新坑：`save_svg` 的 `filename` 不过滤路径分隔符

实测 `save_svg(output_path=".", filename="../../evil")` **写出了输出目录之外**。
而它的默认文件名是 `"{subject.name} - Natal Chart.svg"`，`name` 来自用户。

→ 我们**不走它的落盘路径**：文件名由出生信息 + 排盘前提**哈希**得到
（`astro-<16位>.svg`），路径穿越无从谈起，同一份出生信息重复排盘命中同一个文件。

> `name` **参与哈希但不出现**在文件名里。不参与的话，两个出生时刻与出生地完全相同、
> 只有称呼不同的人会算出同一个文件名，后者的图覆盖前者的 —— 而 M4 网关要按路径
> 伺服这些文件，于是先前那个人拿到的链接会显示出别人的名字。

### 5. 新坑：SVG 是**存储型 XSS** 的载体

kerykeion 对 `city` 与 `name` **不做任何转义**：把 `<script>alert(1)</script>` 当
出生地传进去，它会原样出现在 SVG 的文本节点里 —— 而 SVG 是要由网关伺服给浏览器的
（M4）。已在 `compute_astro` 里用 `xml.sax.saxutils.escape` 处理，**转义而非剔除**：
用户看到的仍是自己填的地名，XML 也是合法的。只影响渲染用的 vendor 对象，
`AstroChart.profile` 里保留原值。

### 6. 相位两路必须用**同一份**配置

`create_natal_chart_data` 与 `AspectsFactory` 各有各的默认 `active_aspects`（两处
都含 quintile 等次要相位，容许度表还各写各的）。若只在一处收窄，**模型读到的相位表
与图上画出来的线就会不一致** —— 图上多几条解释不了的线，或者图上没有而解读里提了，
用户按图索骥时无从判断谁对。已抽出共享的 `ACTIVE_ASPECTS`（五大主相位），两路同传。

### 7. `point.house` 实测**总是**有值

初稿在核验里写了「不落宫位」的分支，并按"活代码"注释。实测 12 个参与排盘的点
（含上升、天顶）**全部**落宫，该分支只可能是防御性的。注释已改成如实说明 ——
把防御分支描述成常态，会让下一个人以为它测得到。

---

## 四、其他发现

1. **PEP 562 惰性 shim 是必需，不是风格选择。** 导入任何子模块都会先执行父包的
   `__init__`，所以 `engines/astro/__init__.py` 若把 `compute_astro` 早早 import 进来，
   中间件侧只要碰到 `engines.astro.points`（词表）就会顺带把 kerykeion 拉进依赖图
   —— 而护栏**看不出**这一点（它只看单文件的顶层 import）。用 `__getattr__` 惰性
   转发后，取词表不触发星历表。已用一个 subprocess 探针钉住。

2. **`BirthProfile.gender` 从必填改为可选。** 性别是**八字专用的输入**（决定大运
   顺逆），不是出生信息的固有属性 —— 星盘不需要它，而星盘工具要写共享的
   `birth_profile` 状态槽，为了一个用不上的字段去跟用户要性别是错的。
   配套：`compute_bazi()` 开头加了守卫，缺性别时抛语义明确的 `ValueError`。
   测试夹具里的星盘刻意**不传** gender，于是这条改动同时被端到端证明了。

3. **与计划书 §3.5 的一处有意偏离。** 计划书写的是把 kerykeion 的异常映射成
   `_needs_clarification`；实现改成映射成 `artifact["status"] = "engine_error"`，
   并**明确要求模型不要调用 `ask_clarification`**。理由：这类异常的成因在部署侧
   （宫位制配错、恒星黄道模式非法），用户既看不懂也改不了。若走追问，用户会被问
   一个自己无法回答的问题，然后大概率随便答一个 —— **把一次配置故障变成一份错误
   数据**。有一条测试专门断言这条 ToolMessage 里**不含** `ask_clarification`。

4. **`RunResult.summary()` 的 `has_chart` 换成了 `charts` 列表。** 原来的布尔只反映
   八字，接着排了星盘的那一轮日志依然写着"没排盘"。这类错不会让任何东西崩溃，
   只会让排查的人照着一个假事实找原因。

5. **跨引擎同一瞬间已实测成立，并钉成了不变量。** `engines/bazi` 用硬编码的
   `_DST_RANGES`（1986–1991）换算 UTC，kerykeion 用 `Asia/Shanghai` 时区库换算 ——
   两条独立路径在 **7 个样本**上完全一致：夏令时内/外 · 夏令时内跨日 · 立春当天 ·
   1949 前 · **夏令时首日已过切换点** · **夏令时末日未到切换点**。
   时区写错是这类系统最隐蔽的错法（整盘平移而看不出来），所以这条是**测试**，
   不是"两边都写着 Asia/Shanghai"的口头保证。抽出 `engines/china_time.py` 之后
   两条路径共享同一个偏移表，但**跨引擎的一致性测试仍然保留** —— 它验的是
   kerykeion 那一侧的时区库与我们的表是否一致，共享真源不能替代它。

6. **夹具的坐标必须走与工具相同的解析路径。** M2 已经踩过一次（硬编码经度差
   0.006° 导致断言恒假），M3 的两个夹具一律 `resolve_place(_PLACE).point` 取值。

---

## 五、测试覆盖

412 项，按"会让不变量静默失效的场景"组织：

| 组 | 覆盖 |
|---|---|
| 跨引擎同一瞬间 | 7 个样本的 UTC 与偏移都相等 · 含夏令时首/末日的切换点两侧 |
| kerykeion 三坑 | 地名不是 Greenwich · 图落在指定目录（不是 home）· 渲染结果无繁体残字 |
| 语言包传参形态 | **正面样本 + 反面样本**（多包一层必须被发现） |
| 文件名不可控 | 含 `../` 的 `name` → 产物仍在输出目录内 |
| 目录不存在 | 工具自建目录（kerykeion 不建） |
| 非 exact 一律拒绝 | `hour_known` / `unknown` / 只给日期 → 追问且**不含** `charts` |
| 继承的占位时刻 | 上次是 `hour_known` 的八字、这次原样传回来 → 拒绝 |
| 抓星盘编造 | 星座错 · 宫位错（阿拉伯数字与中文数字各一）· 相位错 · 命中必带可定位的上下文片段 |
| 不误报 | 全真输出 · 散文中提盘外星座不报 · 带"守护星"的句子不报 |
| 无盘探测 | 三种句式都认得 · 重复只报一次 · 7 条"像断言而非断言"的反面样本 · 与核验认得同一批句子 |
| 盘能进 state | `Command.update["charts"]["astro"]` 与 `compute_astro()` 逐字段一致 |
| 两种盘共存 | 同一 thread 先八字后星盘 → 两张盘都在，谁也不挤掉谁 |
| 确定性 | 同一输入两次 → `model_dump()` 相等 |
| 词表防漂移 | 盘上的名字都出自词表 · 词表与各枚举**双向**集合相等 · 引擎产物能被 schema 收下 |
| 架构护栏 | 中间件侧取词表**不触发星历表**（subprocess 探针 + 反向样本） |

### 唯一需要密钥的那一组

`tests/test_adversarial.py` 共 15 项：八字 9 项（三种诱导话术 + 正常路径 + 多轮 +
缺出生地），星盘 6 项（同样三种话术 + 正常路径 + 只给时辰 + 多轮落宫）。

**星盘那 6 项的判据比八字弱，这一点必须写在明处。** 八字那边"没有命盘时不得出现
干支"是可判定的，因为干支词表封闭；星盘没有对应的东西（§二），所以只能退到
**结构化断言**。为此新增了 `find_astro_claims()` —— `ALL_GANZHI` 在星盘上的
对应物，但形态是**句式**而不是词表。

更要紧的是：**这 6 项在无密钥环境里一律跳过，等于长期没人验。** 所以额外做了两件事
把它的判据变成可验证的：

1. **`find_astro_claims` 本身有确定性单测**（`test_middlewares.py`），含 7 条
   "像断言而不是断言"的反面样本；
2. **一条脚本化镜像**（`test_run_service.py`）：模型不调工具、直接作答，断言那句
   作答确实会被探测到。它挡的是最阴的一种失败 —— 对抗性测试读 `result.reply`，
   而 `reply` 走 `message_text()`；断言若写在正文之外，`reply` 就是空串，
   于是那条对抗性测试会**恒绿**且看不出原因。实测确认过：断言只写在
   `additional_kwargs` 里时，`reply` 为 `''`、探测返回 `[]`。

**跑一次 `make test-live` 仍是必要的**：上面两条能证明"判据是活的"，不能证明
"真实模型不被诱导"。这两件事只有真实密钥能回答。

---

## 六、对后续的影响

1. **M4 网关**：SVG 是**按路径伺服**的用户文件，落盘目录已在 `.gitignore` 里
   （`var/` —— 图是由出生信息渲染出来的，属个人信息，不该进版本库）。网关要按
   `artifact["svg_path"]` 取文件，且**不要**把该目录做成可列举的静态目录。
2. **转义的边界是"哪个 sink"，不是"哪个字段"。** 渲染层已经转义；M4 若还要把
   `profile` 里的原文写进 HTML，得再转义一次。
3. **不要因为"星盘没抓到"就加裸星座扫描**（§二）。那会把一类已知的漏报换成
   一类未知的误报，而误报伤的是用户自己的话。判据的遍历只有一处
   （`iter_astro_claims`），核验与无盘探测共用 —— 想加句式就加在那里，两处
   会同时生效；分开写两遍必然漂移。
4. **跑一次 `make test-live`**（§五末）—— 15 项对抗性测试至今一次都没真跑过。
5. **行运 / 推运 / 返照 / 合盘**都属独立功能，本期明确不做。若要做，注意
   `TransitTimeRangeFactory` 一类接口会引入**时间范围**这个新输入维度 ——
   时区与夏令时那套推理要跟着走一遍，不能假定只有"一个瞬间"。
6. **提示词的星盘那一节不要压缩**（§二末）。
