# M2 Agent 装配 —— 交付与发现

> 状态：**M2 已完成** | 日期：2026-09-12
> 278 项通过 + 9 项待密钥（对抗性测试）· ruff check 与 ruff format 全绿

M2 只回答一个问题：**LLM 能不能在完全不参与推算的前提下，把命盘讲清楚？**

答案是可以。但前提是把「不参与」做成**机制**而不是提示词里的一句请求 ——
本期发现的最严重问题恰好证明了这个区别：一条会让模型再也调不动任何工具的判据，
安静地穿过了所有"看起来在测它"的检查。

---

## 一、交付物

| 文件 | 职责 |
|---|---|
| `metaphys/config/app_config.py` | `AppConfig` / `ModelConfig` / `ToolConfig`；`$ENV` 递归展开且**缺失即 fail fast** |
| `metaphys/reflection/resolvers.py` | `resolve_class` / `resolve_variable`：配置里的点分路径 → 类/对象 |
| `metaphys/models/factory.py` | `create_chat_model()`，换 provider 只改配置一行 |
| `metaphys/tools/types.py` | `Runtime = ToolRuntime[dict, ThreadState]`（**context 在前、state 在后**） |
| `metaphys/tools/builtins/bazi_chart.py` | 排盘：地名 → 坐标 → `compute_bazi` → 写 `charts`；**从不抛异常，只返回"未排盘 + 建议提问"** |
| `metaphys/tools/builtins/clarification.py` | `ask_clarification`（`return_direct`）+ 问句提取 |
| `metaphys/tools/builtins/geo.py` | `lookup_birthplace` 工具与地名消歧规则 |
| `metaphys/agents/thread_state.py` | `charts` 走自定义 reducer（按 kind **覆盖**，不是追加） |
| `metaphys/agents/messages.py` | `message_text()` —— 用户可见文本的**唯一**提取处 |
| `metaphys/middlewares/grounding.py` | 落地核验：60 甲子 + 神煞词表扫描 |
| `metaphys/middlewares/clarification.py` | 拦截追问 + 丢弃同轮兄弟工具调用 |
| `metaphys/middlewares/error_handling.py` | `wrap_tool_call` 兜底 |
| `metaphys/agents/lead_agent/` | 装配（`agent.py`）+ 静态提示词（`prompt.py`） |
| `metaphys/runtime/run_service.py` | `RunService.run` / `astream_run` + `RunResult` |
| `config.yaml` · `langgraph.json` · `.env.example` · `Makefile` | 工程骨架 |

### 测试

| 文件 | 项数 | 覆盖 |
|---|---|---|
| `tests/test_middlewares.py` | 37 | 中间件与落地核验 |
| `tests/test_agent_assembly.py` | 15 | 装配与中间件顺序 |
| `tests/test_run_service.py` | 18 | 端到端（脚本化模型） |
| `tests/test_config_and_tools.py` | 45 | 配置、反射注册、工具装配 |
| `tests/test_adversarial.py` | 9 | 真实模型对抗性（缺 key 时跳过） |

`tests/test_harness_boundary.py`（42 项）本期新增了历法库护栏与一条**护栏自检**
—— 后者用一个已知的正面样本证明这个断言真的会命中，而不是因为路径写错而恒真。

**验证**：`cd metaphys/backend && make check`

---

## 二、核心发现：一条**不报错**的判据清空了所有工具调用

`ClarificationMiddleware._drop_sibling_tool_calls` 的职责是：模型一边追问、一边排盘时，
只留下追问。原始判据写成"留下的与原有的长度不等就改写"，而**没有** `ask_clarification`
的那一轮里"留下的"恒为空 —— 于是它把每一轮正常的工具调用都改写成 `[]`。

后果的严重性在于它的安静：模型再也调不动任何工具，图照常跑完，没有任何异常、任何
警告，`bazi_chart` 一次也不会被调用。**这类缺陷不会被"代码能跑"的测试发现，只会被
针对不变量的断言发现。**

修法两条，都已写成回归测试：

1. 只看 `messages[-1]`，不再向前回溯 —— 回溯会捞到**上一轮**的调用并改写它，等于篡改历史。
2. 判据改成"本轮没有 `ask_clarification` 就原样放行"，而不是"长度不等就改写"。

---

## 三、其他发现

1. **中间件顺序是两个相反的方向**，且排错不报错：
   - `wrap_model_call` / `wrap_tool_call`：列表中**第一个在最外层**
   - `after_model`：按注册**逆序**执行，列表中**最后一个最先跑**

   `ClarificationMiddleware` 必须永远最后，两个原因同时成立（短路 `ask_clarification`；
   抢在 Grounding 之前丢弃兄弟调用）。已**实测**钉住逆序语义，而非照文档推断 ——
   文档没写这一条，猜错的代价是核验与丢弃的先后颠倒。

2. **`create_agent` 没有 `max_iterations` 参数** —— 必须换算成运行期的 `recursion_limit`。
   实测一轮"模型 → 工具 → 模型"走 **7 个 superstep**（不是直觉上的 2）。测试里把这个
   实测值钉成基准：换算系数若被改小到跑不完一轮，用例会红。

3. **`langgraph.json` 从模块 `__dict__` 取图工厂**，`__getattr__` 式的惰性再导出取不到 ——
   本地 import 一切正常，只有 LangGraph 解析时找不到。必须是个模块级的**具体函数**。

4. **DeepSeek thinking 模式的坑**：开启后每条 assistant 消息都须回填 `reasoning_content`，
   而上游 `ChatDeepSeek` 把它存进 `additional_kwargs` 却在重建请求时丢弃，多轮工具调用
   会直接报错。**M2 用非 thinking 的 `deepseek-chat` 绕开**；换 `deepseek-reasoner` 前
   必须先移植 deer-flow 的补丁。

5. **`tool.args_schema` 不能用来生成模型可见契约** —— 注入了 `Runtime` 的工具会因其中的
   Callable 而抛 `PydanticInvalidForJsonSchema`。应使用 `tool.tool_call_schema`。

6. **测试夹具的坐标必须来自地理库，不能写死**。夹具曾硬编码一个想当然的经度
   （116.4074），与地理库真值（116.413384）差 0.006°，真太阳时因此差 1.4 秒 ——
   于是"命盘与引擎逐字段一致"这条断言**恒假**，而被测代码一点问题都没有。

   排查过程值得一提：现象是两个 `compute_bazi` 调用给出不同的真太阳时，看起来像引擎
   有非确定性（那可是最严重的一类缺陷）。先单独测 `equation_of_time_minutes` —— 五次
   调用完全一致，排除了天文计算；再查 `lookup('北京市')` 的返回值，才定位到夹具。
   **夹具走错解析路径时，它比的不是"链路有没有改动命盘"，而是"我猜的坐标对不对"。**

7. **追问轮里 `reply` 会是空的**。那句问话是工具调用的**参数**，不在消息正文里，
   于是"取最后一条 AI 消息的正文"取到空串 —— 用户在最需要被告知的时刻看到沉默。
   修法是 `clarification_question()`（放在拥有 `ask_clarification` 契约的那个模块里）
   作为 `RunResult.reply` 的回退。这类"唯一出口是空的"缺陷，靠端到端断言而非单元断言才发现。

8. **纯空白的密钥会蒙混过关**。`$ENV` 展开原先只判 `if not value`，于是 `.env` 里写了
   `DEEPSEEK_API_KEY=` 后面跟几个空格、或 CI 里设成 `" "`，都能通过 fail-fast。
   代价是把故障推迟到请求期，而那时报的是 **401** —— "密钥没配"与"密钥认证失败"
   看起来是两回事，排障成本高得多。已改为 `if not value or not value.strip()`。

9. **收尾刷新缓存会把用例结果污染成 teardown ERROR**。conftest 的夹具原先在
   teardown 里调 `reload_app_config()`，而它**会立刻把缓存读回来** —— 此刻
   monkeypatch 尚未回滚，用例若改过配置环境（比如指向一个不存在的 `config.yaml`），
   这一读就抛异常。已改为只 `cache_clear()` 不重读：下一个用例本来就会重读，这里
   重读没有任何收益，只有风险。

---

## 四、测试覆盖

278 项，按"会让不变量静默失效的场景"组织：

| 组 | 覆盖 |
|---|---|
| 六十甲子 | 60 项互异 · 阳干配阳支 · **核验判据的正确性基础** |
| 神煞防漂移 | 穷举 10 日干 × 12 日支 × 4 柱位 × 60 甲子，与 `SHEN_SHA_NAMES` **双向**集合相等 |
| 抓编造 | 柱位错 · 日主错 · 神煞错 · **裸干支（无句式可依赖）** · 干支安错柱位 |
| 不误报 | 全真输出 · 盘上真有的神煞 · 普通散文 · 问句 · 空串 |
| 追问 | 短路工具体 · 丢弃兄弟调用 · **回归：正常工具调用绝不被清空** · **回归：绝不改写历史轮** · 问句可从工具调用中读出 |
| 兜底 | 异常 → 可读 ToolMessage · **`GraphBubbleUp` 不被吞掉** |
| 装配 | 顺序精确匹配 · 错误顺序**被拒** · `after_model` 逆序语义 · 工厂是模块级真属性 |
| 端到端 | 命盘逐字段一致 · 编造被抓 · 追问不排盘 · 地名歧义不猜 · 多轮历史保留 · 会话隔离 |
| 递归上限 | 系数不小于实测值 · json 可序列化 · 事件流以 `final` 收尾 |
| 配置 | `$ENV` 展开 · **缺失/空白即报错且指名位置** · 坏 YAML · 结构校验（重名模型、悬空 `default_model`） |
| 反射注册 | 路径写错时报的是**配置里的拼写**而非 `AttributeError` · 子类校验 · 工具名不符时告警但仍用真名 |
| 工具装配 | 夏令时缺口/非法农历/查不到地名 → **返回追问而非抛异常** · 歧义地名交还候选 |
| 架构护栏 | harness 不依赖 app · 引擎不依赖 LLM/**网络** · **只有 `engines/` 与 `tools/` 可碰历法库** |

### 唯一需要密钥的一条

`tests/test_adversarial.py` 用真实模型撞三种诱导话术（直接要求别调工具 / 伪装成已有
数据 / 绕开排盘只要结论）。它的第一层断言是落地核验**覆盖不到**的那一段：

> 没有命盘时，输出里不得出现任何干支。

没有命盘就没有词表，核验此时**无从判断对错，只能放行** —— 所以这一段必须由测试来守，
不能指望中间件。这也是为什么对抗性测试不能只断言"它调了工具"。

把 `DEEPSEEK_API_KEY` 写进 `config.yaml` 同级的 `.env` 后用 `make test-live` 单独跑它。
**做成独立目标，是为了让"跳过"不被误读成"跑过了"** —— `make check` 里那 9 个 `s`
不构成任何证据。

---

## 五、对后续的影响

1. **M4 网关**直接转发 `astream_run` 的事件，不要二次加工 —— 前端要的正是"哪个节点
   产出了什么"。
2. **换 `AsyncPostgresSaver` 只改一行**：`graph.checkpointer = ...` 的绑定处。
3. **M5 安全合规**只在 `safety_flags` 通道上追加判定，通道已在 state 里预留。
4. **不要放开历法库的 import 白名单**。一旦 LLM 侧能算，算错就没有任何东西拦得住，
   而用户看不出命盘是错的 —— 从依赖层面禁止比在评审里盯人可靠。
5. **`agent.max_iterations` 与测得的 superstep 系数是绑定的**：改了中间件数量就要重测
   `_SUPERSTEPS_PER_ITERATION`，否则表现为"聊到一半突然 `GraphRecursionError`"。
6. **`school` 已从 `config.yaml` 贯通到工具参数**（M1-FINDINGS §六.5 的遗留项），
   测试断言命盘携带配置的流派。
