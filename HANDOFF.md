# metaphys —— 交接文档（给 AI 读者）

> **最新：Next.js + M5 第一版已交付。** 前端为 Next.js App Router / React / TypeScript；安全输入短路、输出替换与审计已实现。最新契约与验收见 `NEXTJS-M5-FINDINGS.md`，启动见 `README.md`。下文原生前端、M5 仅通道等描述属于历史状态。


> **M4 最新交付（2026-09-12）：** 本地网关与前端已完成，启动见 `README.md`，当前范围、验收与限制见 `M4-FINDINGS.md`。本文件下文保留早期规划和基线；其中“M4 未开始/前端为空”的描述已过时。真实模型联调与 M5 仍未完成。


> 更新日期：2026-09-12 · M0–M3 后设计审查修复已完成，M4/M5 尚未交付
>
> **修复后的当前契约以 `DESIGN-FIXES.md` 为准。** 本文其余历史验收数字为修复前基线。
> 当前 `make check`：451 passed、15 skipped；新增 23 项设计回归。
> 核验失败会撤回本轮错误正文；命盘仅在相同出生资料下共存；显式时区输入被拒绝；
> 历史近似时间可通过用户本轮确认句升级；SSE 仅输出进度和核验结束后的 JSON 结果。
> 本文的读者是**接下来要在这个仓库里干活的人或 AI**。它只回答两件事：
> **后面要做什么**，以及**哪些事不要做**。
>
> 读法：§1 是可以直接信的事实，§2 是**声称过但没验过**的事实 —— 两者的分界
> 就是这份文档存在的主要理由。找不到出处的结论一律归入 §2。

---

## 0. 三十秒版本

**项目是什么。** 一个玄学 agent：中式八字命理 + 西方星盘。用户给出生信息，模型排盘并解读。

**唯一的架构铁律。** **LLM 不得参与任何数值推算或排盘。** 这条不是靠提示词求模型自觉，
而是三层强制：

| 层 | 机制 | 在哪 |
|---|---|---|
| 1. 工具化 | 干支 / 星座 / 宫位 / 相位只能来自工具返回值 | `tools/builtins/{bazi,astro}_chart.py` |
| 2. 依赖层护栏 | `middlewares/`、`agents/` 从依赖上**碰不到**历法库 | `tests/test_harness_boundary.py` |
| 3. 运行时核验 | 模型输出里的干支/结构断言必须能在盘上找到，否则记 `grounding_flags` | `middlewares/grounding.py` |

**里程碑。** M0 探针 → M1 八字引擎 → M2 机制（工具+护栏+核验）→ M3 星盘 →
**M4 网关与前端（未开始）** → M5 安全合规（未开始）。

**当前数字（已实测）。** `cd backend && make check` → **428 passed, 15 skipped**，
ruff check 与 ruff format --check 全绿。443 项中跳过的 15 项是 `test_adversarial.py`
（需要一个真实 `DEEPSEEK_API_KEY`，仓库里没有）。

**这一期最重要的一条结论。** 星盘这条链路上，第 3 层机制**天然抓不全** ——
详见 §2.2。这不是待修的 bug，是已确认的能力边界，并且**不能靠"顺手补个裸词扫描"来修**。

---

## 1. 可以直接信的事实（已实测，带可复现的证据）

> 这一节每一条都跑过。复现方式写在最后一列。

### 1.1 环境与仓库形态

| 事实 | 证据 / 复现 |
|---|---|
| **当前目录是 git 仓库**；原交接时的环境描述已经过时 | `git rev-parse --is-inside-work-tree` → `true` |
| **裸 `python` 在这台机器上不存在**，一律用 `backend/.venv/bin/python` | `python` → `command not found` |
| `config.yaml` 在**仓库根**，不在 `backend/` | 路径：`metaphys/config.yaml` |
| `config.yaml` 里的 `$DEEPSEEK_API_KEY` **缺失即报错**，不会静默变空串 | `config/app_config.py`；`.env.example` 亦如此声明 |
| 仓库根 `.env` **不存在**（所以对抗性测试跳过） | `ls metaphys/.env` |

### 1.2 测试现状

`make check` = `ruff check` + `ruff format --check` + `pytest`，当前全绿。

| 文件 | 项数 | 覆盖 |
|---|---|---|
| `tests/test_middlewares.py` | 81 | 中间件 · 两套核验规则 · 无盘探测 |
| `tests/test_shensha_strength.py` | 74 | 神煞规则表 · 旺衰评分 |
| `tests/test_harness_boundary.py` | 51 | 架构护栏（import 白名单 + 词表可达性探针） |
| `tests/test_bazi.py` | 47 | 八字引擎 |
| `tests/test_astro_engine.py` | 47 | 星盘引擎 · 跨引擎同一瞬间 · 落盘安全 |
| `tests/test_config_and_tools.py` | 45 | 配置 · 工具装配 |
| `tests/test_run_service.py` | 23 | 端到端（脚本化模型） |
| `tests/test_china_time.py` | 23 | 中国时区偏移表（跨引擎共用的真源） |
| `tests/test_astro_tool.py` | 22 | 星盘工具：拒绝 · 文件名 · 目录 · state |
| `tests/test_agent_assembly.py` | 15 | 提示词铁律 · 图装配 |
| `tests/test_adversarial.py` | 15 | **真实模型对抗性 —— 从未跑过，见 §2.1** |
| **合计** | **443**（428 通过 + 15 跳过） | `.venv/bin/python -m pytest --collect-only -q` |

### 1.3 已经钉成测试的不变量

这些**不是**"我们打算这么做"，是**破坏它就会有测试变红**：

- 跨引擎同一瞬间：`engines/bazi` 的硬编码夏令时表与 kerykeion 的时区库，
  在 7 个样本上 UTC 与偏移完全一致（含夏令时首/末日的切换点两侧）。
- 词表不漂移：`engines/astro/points.py` 的中文名集合与 `schemas/chart.py` 的枚举**双向相等**。
- 中间件侧取词表**不触发星历表**：subprocess 探针 + 反向样本（防 PEP 562 shim 被"简化"掉）。
- SVG 渲染结果**无繁体残字**（按**字**判据，不按词）。
- 语言包传参形态：正面 + 反面样本（多包一层 `{"CN": ...}` 必须被发现）。
- 含 `../` 的 `name` 不会让产物跑出输出目录；工具自己建目录。
- 非 `exact` 出生时间一律拒绝星盘（`hour_known` / 未知 / 只给日期都算）。
- 同一 thread 先八字后星盘 → **相同出生资料的两张盘都在**；资料变更时旧盘失效。
- 模型输出里的编造会被抓：星座错 · 宫位错（阿拉伯数字与中文数字各一）· 相位错 ·
  命中必带可定位的上下文片段。
- 不误报：散文中提到盘外星座不报；「火星是天蝎座的守护星」这类句子不报。

### 1.4 实现里几处**故意**偏离直觉的地方（别当成 bug 改回去）

| 现状 | 为什么 |
|---|---|
| `engines/astro/__init__.py` 用 PEP 562 `__getattr__` 惰性转发 | 导入任何子模块都会执行父包 `__init__`；eager import 会把 kerykeion 拖进中间件的依赖图，而单文件护栏**看不见**这一点 |
| `BirthProfile.gender` 是**可选**的 | 性别是八字专用输入（决定大运顺逆），星盘不需要；星盘工具要写共享的 `birth_profile` 槽位 |
| kerykeion 异常映射成 `artifact["status"] = "engine_error"`，并**明确要求模型不要追问** | 偏离计划书 §3.5（那里写的是 `_needs_clarification`）。成因在部署侧，用户答不了，追问只会把配置故障变成一份错误数据。见 §4.1 |
| `RunResult.summary()` 报 `charts` 列表而非 `has_chart` 布尔 | 旧布尔只反映八字，接着排星盘的那一轮日志仍写"没排盘" |
| `find_astro_claims()` 的形态是**句式**而不是词表 | 星盘词表开放，「双子座」是日常词汇。见 §2.2 |
| 刻画相位时两路共用 `ACTIVE_ASPECTS` | `create_natal_chart_data` 与 `AspectsFactory` 默认的次要相位不一致，收窄一处会让「模型读到的相位表」与「图上画的线」对不上 |
| 只保留 12 个点（10 行星 + 上升 + 天顶） | 每多一个点，既是模型可以张冠李戴的对象，也是核验要覆盖的面 |

---

## 2. 声称过、但**没有**验证过的事实（别直接信）

### 2.1 ⚠️ 15 项对抗性测试**一次都没有真跑过**

`tests/test_adversarial.py`（八字 9 项 + 星盘 6 项）需要真实 `DEEPSEEK_API_KEY`，
仓库里没有 `.env`，所以**长期处于 skip 状态**。`make check` 里那 15 个 `s`
不构成任何证据 —— Makefile 特意把 `test-live` 做成独立目标，就是为了让"跳过"
不被误读成"跑过了"。

**已知确实成立的两件事**（这两件是确定性验证的，可以信）：

1. `find_astro_claims()` 本身有确定性单测，含 7 条"像断言而不是断言"的反面样本；
2. 一条脚本化镜像证明"判据是活的"：模型不调工具直接作答时，那句作答**确实**会被探测到。
   它挡的是最阴的一种失败 —— 对抗性测试读 `result.reply`，而 `reply` 走
   `message_text()`；断言若写在正文之外，`reply` 就是空串，测试会**恒绿**且看不出原因。
   （实测确认过：断言只写在 `additional_kwargs` 里时 `reply == ''`、探测返回 `[]`。）

**但这两条不能替代真跑。** "判据是活的" ≠ "真实模型不被诱导"。要真跑：

```bash
cd metaphys/backend
echo 'DEEPSEEK_API_KEY=sk-...' > ../.env   # 需要人提供真实密钥
make test-live                              # -rs，跳过原因会打印出来
```

> 密钥纪律（沿用至今）：只判断 key 的**有无**，**从不打印其值**；测试里的占位值是
> `test-placeholder-not-a-real-key`；`.env` 在 `.gitignore` 里。

### 2.2 星盘的落地核验抓不到的两类输出

第 3 层机制在星盘上只能认**结构化断言**（`太阳在双子` / `月亮在第七宫` / `太阳刑月亮`）。
因此下面两类**抓不住**：

- **顺口提一个星座而不作断言** —— 「你是典型的双子座」
- **列表式 / 无谓语的写法** —— 「太阳：双子」

还有一类**已知误报**：「太阳在狮子座的人喜欢被关注」形如断言、实为泛指。
这些缺口与误报都写在 `grounding.py` 的模块 docstring 里并有测试钉住。
**写下来是为了让下一个人不要"顺手补上"裸词扫描。**

### 2.3 其他未验证项

| 声称 | 实际状态 |
|---|---|
| 「M3 已完成」 | 计划书 §五的验收表逐行核过，均已有对应测试；但 §2.1 那一格是真空白。**不要把 M3 说成"全部验证通过"** |
| kerykeion 版本兼容性 | 只在当前安装的 5.12.9 上实测。语言包的繁体字是按**当前版本的键**逐个审出来的 —— 升级 kerykeion 后若新增的键带繁体值且会被渲染，测试会红（判据是按字扫渲染结果），但**没有验证过升级路径** |
| 前端 / HTTP 层 | `backend/app/` 三个文件全是**空的**，`frontend/` 是空目录。存在性 ≠ 已实现 |
| M5 安全合规 | state 里只有 `safety_flags` 通道，**没有任何判定逻辑** |

---

## 3. 未完成的任务（按优先级）

### P0 —— 跑一次 `make test-live`（唯一的硬空白）

见 §2.1。需要人提供密钥。这是"真实模型会不会被诱导"这个问题的**唯一**答案来源。

### P1 —— M4：网关 + 前端（未开始，只有占位）

现状：`backend/app/gateway/routers/` 空；`config.yaml` 里已有 `gateway: {host, port: 8010}`
并注明是 M4 占位；`run_service.py` 的 docstring 已写明"M4 的 FastAPI/SSE 层把
`RunService.astream_run` 转发出去"。

已经定下来的约束（来自 M2/M3 findings，不是我的推断）：

1. **网关转发 `astream_run` 的公开 JSON 事件**：update 仅含进度，final 才含答复；禁止直接暴露原始图状态。详见 `DESIGN-FIXES.md`。
2. **SVG 按 `artifact["svg_path"]` 取文件，且不要把 `svg_dir` 做成可列举的静态目录。**
   文件名是出生信息 + 排盘前提的哈希（`astro-<16位>.svg`），但目录可列举就等于
   把所有用户的盘摊开。
3. `var/` 已在 `.gitignore`（图是出生信息渲染出来的，属个人数据）。
4. **转义的边界是"哪个 sink"，不是"哪个字段"。** 渲染层（SVG）已经转义过；
   M4 若还要把 `profile` 里的原文（含用户填的 `name` / `city`）写进 HTML，**得再转义一次**。
5. 换持久化只改一行：`graph.checkpointer = ...` 的绑定处（当前是 `InMemorySaver`，
   生产要换 `AsyncPostgresSaver`）。

**尚未决定、需要人拍板的事**见 §4.2。

### P2 —— M5：安全合规

只在 `safety_flags` 通道上追加判定，通道已在 `thread_state.py` 里预留。
当前**没有任何**实现。

### 明确不做（本期范围外，别自作主张加）

- 行运 / 推运 / 返照盘（`TransitTimeRangeFactory`、`PlanetaryReturnFactory`）
- 合盘 / 比较盘（`synastry_aspects`、`RelationshipScoreFactory`）
- 七政四余等中式星盘体系
- LLM 侧的任何"自己算一下"

> 若将来做行运类功能，注意它引入**时间范围**这个新输入维度 —— 时区与夏令时那套
> 推理要跟着走一遍，不能假定"只有出生那一个瞬间"。

---

## 4. 悬而未决的决策（**需要人来定，不要自己定**）

### 4.1 两条遗留的确认项

| # | 事项 | 现状 |
|---|---|---|
| 1 | 任务清单里的「severity 分级」 | **在代码和批准的计划书里都没有对应物**。当时我把它悄悄关掉了，没有明说。请确认：是要做（那需要定义分级规则），还是撤掉 |
| 2 | 计划书 §3.5 的偏离（kerykeion 异常 → `engine_error` 而非追问） | 已实现且有测试断言那条 ToolMessage 里**不含** `ask_clarification`。若不同意，是一处小回滚 |

### 4.2 M4 动工前必须定的四件事

1. **HTTP 框架**？—— 代码注释里出现的是 FastAPI（护栏的 `FORBIDDEN_ROOTS` 也含
   `fastapi`/`starlette`/`uvicorn`），但这是**从注释反推的，用户从未确认**。
2. **SSE 事件形状**：设计修复已明确 v1 公开事件，见 `DESIGN-FIXES.md`，不再透传 LangGraph 对象。
3. **SVG 怎么伺服**？—— 受控路由（推荐，天然不可列举）vs 静态目录挂载。见 §3 P1.2。
4. **前端本轮是否交付**？—— `frontend/` 是空目录，技术栈未定（`.gitignore` 里
   预留了 `node_modules/`、`.next/`、`out/`，暗示 Next.js，但同样**未经确认**）。

---

## 5. 硬约束：**不要**做的事

> 这一节的每一条都有具体理由。违反它们的代价通常不是崩溃，而是**静默的错误**。

1. **不要给星盘加裸星座名扫描。** 那会把一类**已知的漏报**换成一类**未知的误报**，
   而误报伤的是用户自己的话（「我朋友是处女座」）。见 §2.2 与 `grounding.py` docstring。
2. **不要放开历法库的 import 白名单。** `test_harness_boundary.py` 的
   `CALENDAR_ROOTS` 只允许出现在 `engines/` 与 `tools/` 下。一旦 LLM 侧能算，
   算错就没有任何东西拦得住，而用户看不出命盘是错的。
3. **不要把 `astro.svg_dir` 做成可列举的静态目录。** 见 §3 P1.2。
4. **不要压缩 `prompt.py` 里星盘那一节。** 星盘链路的机制覆盖比八字弱（§2.2），
   兜底的重心因此转移到提示词上 —— 那一节写得比八字细是刻意的，理由记在
   `prompt.py` 的模块 docstring 里。
5. **不要在 `grounding.py` 里把句式遍历写成两份。** 核验与无盘探测共用
   `iter_astro_claims()` 这一处遍历；分开写必然漂移，届时会出现"能抓编造但探测不到"
   或反之。想加句式就加在那里，两处同时生效。
6. **改了中间件数量就要重测 `_SUPERSTEPS_PER_ITERATION`**（`run_service.py:32`）。
   它与 `agent.max_iterations` 是绑定的，漂了表现为"聊到一半突然 `GraphRecursionError`"。
7. **不要切到 `deepseek-reasoner`。** 开 thinking 后 DeepSeek 要求每条 assistant 消息
   回填 `reasoning_content`，而上游 `ChatDeepSeek` 会丢，多轮工具调用直接报错。
   已有一条测试断言配置里是 `deepseek-chat`。
8. **密钥纪律**：`.env` 不入库；只判断 key 有无、**从不打印值**；测试用固定占位值
   `test-placeholder-not-a-real-key`。
9. **不要让 harness 反向依赖 app。** 方向只能是
   `app/gateway/ → packages/harness/metaphys/`。引擎必须能脱离 Web 框架独立测试 ——
   那正是本项目准确性的前提。

---

## 6. 怎么在这个仓库里干活

### 6.1 命令

```bash
cd metaphys/backend

make test        # 全部测试（无 key 时对抗性测试自动跳过）
make test-live   # 只跑对抗性测试，需要真实 key —— 见 §2.1
make lint        # ruff check
make fmt         # ruff check --fix + ruff format（写回）
make check       # lint + format --check + test —— 提交前跑这个
```

`.venv/bin/python` 是唯一可用的解释器（裸 `python` 不存在）。
`PYTHONPATH` 由 Makefile 导出为 `.:packages/harness`；直接跑 pytest 时靠
`pyproject.toml` 的 `pythonpath = [".", "packages/harness"]`。

### 6.2 目录

```
metaphys/                        # 仓库根（已有 git 仓库）
├── config.yaml                  # 模型 / 工具 / 排盘参数 / 网关占位
├── .env.example                 # 复制成 .env 填真实 key
├── M0-FINDINGS.md … M3-FINDINGS.md   # 每期的实测结论 —— 唯一的历史记录
├── HANDOFF.md                   # 本文
├── backend/
│   ├── app/gateway/             # M4 用，**当前是空的**
│   ├── langgraph.json           # graph: lead_agent → metaphys.agents:make_lead_agent
│   ├── packages/harness/metaphys/
│   │   ├── engines/{bazi,astro,china_time.py}   # 确定性排盘，可独立测试
│   │   ├── schemas/chart.py                     # BirthProfile / BaziChart / AstroChart
│   │   ├── tools/builtins/                      # bazi_chart · astro_chart · geo · birth · clarification
│   │   ├── middlewares/                         # grounding · clarification · error_handling
│   │   ├── agents/lead_agent/                   # agent.py · prompt.py
│   │   ├── runtime/run_service.py               # RunService.run / astream_run
│   │   └── config/app_config.py
│   └── tests/                   # 见 §1.2
└── frontend/                    # **空目录**，M4
```

### 6.3 在这个仓库里做改动的验收线

1. `make check` 全绿。
2. **改动任何一条不变量，就补一条测试。** 这个项目里几乎每个 bug 都是
   "不会崩溃、只会静默给错"那一类（地名默认 Greenwich、SVG 写进用户主目录、
   文件名可路径穿越、时区整盘平移看不出来……）。测试在这里的职责不是覆盖率，
   是**把静默的错变成响的错**。
3. 新发现的实测结论写进 findings 文件，**不要**只留在代码注释里 —— findings 是项目跨期记忆的一部分。
4. 报告状态时如实：**跑过就说跑过，跳过就说跳过**（`make check` 里那 15 个 `s` 就是坑）。

---

## 7. 已知的坑（踩过的，别再踩）

| 坑 | 症状 | 记在哪 |
|---|---|---|
| 不传 `city`/`nation` 时 kerykeion 默认 Greenwich/GB | 盘面整体偏移而**看不出来** | M0 · M3 §三.2 |
| SVG 默认写用户主目录；`save_svg` **不创建目录** | 文件跑到不该去的地方 / `FileNotFoundError` | M0 · M3 §三.2 |
| `save_svg(filename=...)` **不过滤路径分隔符** | `"../../evil"` 真的写到目录外 | M3 §三.4 |
| kerykeion 对 `city`/`name` **不做转义** | **存储型 XSS**，而 SVG 是要伺服给浏览器的 | M3 §三.5 |
| `language_pack` 传参形态错了**不报错也不生效** | 与"没传"字节级相同 | M3 §三.3 |
| kerykeion 的 CN 语言包是**繁简混排** | 同一张图里 `宫位划分` 与 `宮 1:` 并存 | M3 §三.3 |
| 两处 `active_aspects` 默认值不同 | 模型读到的相位表与图上画的线不一致 | M3 §三.6 |
| DeepSeek 的 `reasoning_content` 必须回填 | 多轮工具调用直接报错，报错信息离病因很远 | M2「thinking 模式」 |
| `_excerpt` 命中失败返回**空串** | 锚点若用归一化后的值，摘录会静默变空 | M3 实现 |
| 硬编码坐标与工具解析路径不一致 | 断言**恒假**（经度差 0.006°） | M1 · M3 §四.6 |
| 日期/时辰的占位值从 state 继承 | 上一轮的近似时刻被当成这一轮的精确输入 | M3（有专门测试） |
| `datetime.utcnow()` 在 kerykeion 里 | 导入即发 `DeprecationWarning`（第三方，不影响我们） | `make check` 输出 |

---

## 附：一句话交接

**八字那条链路是「机制兜底」，星盘那条是「机制兜一半、提示词兜一半」。**
后面无论做什么，先问一句：这件事是在**加强那三层之一**，还是在**绕过它们**？
