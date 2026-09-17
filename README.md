# metaphys · 命理知时

八字命理与西洋星盘的本地对话应用。排盘由确定性引擎完成，模型负责解读。当前已实现 M0–M4 本地版本、Next.js 前端及 M5 第一版安全规则；真实模型验收、持久化和公网部署尚未完成。

## 启动

需要 Python 3.12+、uv，以及 Node.js 22.18+（推荐使用现有 Node 24）。前端采用 Next.js 16 App Router、React 19 和 TypeScript，不依赖外部 CDN。

1. 在项目根目录复制 `.env.example` 为 `.env`，填入真实 `DEEPSEEK_API_KEY`。不要将密钥提交到 Git。
2. 在 `backend/` 执行：

   ```bash
   make install
   make frontend-build
   make dev
   ```

3. 打开 <http://127.0.0.1:8010>。

`make install` 按 `backend/uv.lock` 安装 Python 依赖；`make frontend-build` 按 `frontend/package-lock.json` 安装并构建 Next.js 静态导出。生产构建由网关同源提供，不需要额外常驻 Node 服务。启动会读取根目录配置；密钥缺失时明确报错，不会用模拟回答伪装成真实服务。

## 前端开发（热更新）

先配置根目录 `.env`，然后在两个终端分别运行：

```bash
cd backend
make next-dev
```

```bash
cd frontend
npm ci
npm run dev
```

打开 <http://127.0.0.1:3000>。Next.js 将 `/api/*` 代理到本地 8010 网关。`make next-dev` 显式启用仅本地 3000 来源；普通 `make dev` 不放开此来源。不要把密钥放进 `NEXT_PUBLIC_*`，浏览器不需要模型密钥。

## 使用

- 选择八字或西洋星盘，填写公历出生日期、当地钟表时间与出生地，然后排盘。
- 日期为农历时，在下方对话中明确说明农历日期及是否闰月，交给八字工具处理；表单本身只填写公历。
- 八字支持时辰未知；西洋星盘要求精确到分钟。
- 右侧显示四柱、行星落点与星盘图，也可查看完整命盘数据。
- 出生资料变化后，旧资料的另一种盘会失效；相同资料的两种盘可以共存。
- 核验发现错误时撤回本次解读，仍可查看引擎生成的命盘。
- 若历史时间不精确，工具会给出明确确认句；核对后按提示回复即可升级精度。

## 会话与数据

这是**单进程本地预览版**，默认仅监听 `127.0.0.1`。不同浏览器会话使用独立 HttpOnly Cookie，所有对话和星盘图读取均检查归属。请勿将它直接作为公网多用户服务部署。

对话和 checkpoint 暂存在内存：浏览器刷新可恢复，服务重启后清空。会话空闲 24 小时失效；最多 128 个浏览器会话，每个保留 20 段对话，每段最多 40 轮。模型运行最长 180 秒，最多同时运行 4 个请求。

关闭或刷新浏览器不会取消已接收的任务；服务继续运行时，完成结果可通过会话恢复。运行异常或超时后必须新建会话，避免继续使用不完整的 checkpoint。

SVG 保存在根目录 `var/charts`，该目录不通过静态文件接口公开。删除对话会移除公开记录及 checkpoint；磁盘 SVG 可能由同资料会话共用，**不会随对话删除**。需要清理渲染文件时，可在停止服务后删除 `var/charts`。当前未实现账户、跨设备登录、Postgres 持久化或自动磁盘保留策略。

## 验证

```bash
cd backend
make check       # ruff、格式、全部确定性测试
make test-live   # 真实模型对抗性测试，需要真实密钥
```

前端检查：

```bash
cd frontend
npm run typecheck
npm test
npm run build
```

验收使用真实排盘引擎和脚本模型，未冒充真实 DeepSeek 联调。最新交付见 `NEXTJS-M5-FINDINGS.md`，早期记录见 `M4-FINDINGS.md` 与 `DESIGN-FIXES.md`。

## HTTP 契约

全部 API 同源；先 `GET /api/session` 获取 Cookie 和 `csrf_token`。所有写入需要 `X-CSRF-Token`；浏览器 Origin 若存在必须匹配本服务。

规范接口在 `/api/v1`；旧 `/api/*` 保留为兼容适配器。两者由同一批路由与同一条执行路径提供，差异只在路径前缀，以及帧与错误的呈现方式。前端目前仍走 `/api/*`。

**兼容层（现有前端使用）**

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/session` | 初始化浏览器会话、读取会话列表与 `csrf_token` |
| POST | `/api/conversations` | 新建对话，服务器生成 id，返回 201 |
| GET | `/api/conversations/{id}` | 读取对话、运行状态、当前命盘 |
| DELETE | `/api/conversations/{id}` | 删除对话和 checkpoint，运行中拒绝，返回 204 |
| POST | `/api/conversations/{id}/runs` | `{ "message": "…" }`，返回 SSE |
| GET | `/api/conversations/{id}/chart.svg` | 访问该会话当前星盘图 |

**规范接口 `/api/v1`**

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/session` | `csrf_token` 与 `capabilities`（不含会话列表） |
| GET | `/api/v1/conversations` | 会话列表，游标分页（`cursor`、`limit`，默认 20，上限 100） |
| POST | `/api/v1/conversations` | 新建对话，返回 201 |
| GET | `/api/v1/conversations/{id}` | 读取对话与运行状态 |
| GET | `/api/v1/conversations/{id}/messages` | 消息历史，游标分页（`cursor`、`limit`，默认 50，上限 200） |
| DELETE | `/api/v1/conversations/{id}` | 删除对话与 checkpoint，运行中拒绝，返回 204 |
| POST | `/api/v1/conversations/{id}/runs` | 受理一轮运行，立即返回 202 + `run_id` 与 `events_url` |
| GET | `/api/v1/conversations/{id}/runs/{run_id}` | 读取该轮运行的状态与结果 |
| GET | `/api/v1/conversations/{id}/runs/{run_id}/events` | 订阅该轮事件（SSE），支持 `Last-Event-ID` 重放 |

另有 `GET /health/live` 与 `GET /health/ready`（未就绪时 503）。

未实现的 v1 路径**显式注册为 501 而非 404**，文案点名所属阶段：`profiles*`、`charts/{id}`、`charts/{id}/versions`、`deletions/{id}`、`runs/{id}/cancel`。`GET /api/v1/session` 的 `capabilities` 把这些能力列为 `false`，并有测试交叉校验「`capabilities` 中的每项 `false` 都有对应的 501 路由」。带 `Idempotency-Key` 头的受理请求同样返回 501 —— 静默接受比明确拒绝更糟。

错误响应分两轨：`/api/v1/*` 为 `{"error":{"code","message","retryable","request_id"}}`，其余路径保留 `{"detail":…}`（前端 `lib/api.ts` 读它）。响应头回显 `X-Request-ID`，缺失时服务端生成。

`run_id` 由应用层生成后贯通执行与事件，因此「这一轮的结果属于哪一轮」始终可回答，不再由 harness 内部各持一个。

### 运行与事件

运行状态机为 `queued → running → succeeded | failed | timed_out | cancelled`；`busy` 与 `failed` 保留为阶段 A 的兼容投影，权威状态是运行仓储，二者由同一处代码同时写入。

v1 事件依次为 `run.accepted`、`run.started`、若干 `run.progress`、一个终态事件（`run.final`｜`run.failed`｜`run.cancelled`），以及恒为最后一帧的 `run.end`。每个事件带 `id: <run_id>:<seq>`；断线后带 `Last-Event-ID`（或 `?cursor=`）重连即可从该序号续读，不重复也不漏。坏游标或跨 run 游标返回 400，未知 run 返回 404。15 秒无数据时发送 SSE 注释保活。

旧接口的帧是 v1 事件的**翻译**，不是第二条执行路径：`run.progress` → `update`（`data` 恰为 `{"status":"running"}`）、`run.final` → `final`、`run.failed` → `error`（只有通用文案，不回显模型异常或密钥），其余一律丢弃。这正是终态帧之后旧流立即结束的原因 —— 旧客户端靠这一点判断本轮完成。旧帧增量带上 `run_id`，字段只增不改。

`run.final` 在结果落库**之后**入队，所以收到 `final` 的那一刻就能查到 `succeeded`，不会出现「拿到答复再查却还是 running」。

资料表单不会直接绕过 Agent 调用引擎，它将明确的出生资料提交给相同对话链路。harness 不依赖 FastAPI；Web 层分层为 `domain/`（实体与状态机）、`application/`（用例与端口）、`infrastructure/`（内存仓储与图适配器）、`gateway/`（HTTP），依赖方向由 AST 护栏测试强制，`_graph`/`checkpointer` 只允许出现在 `infrastructure/execution.py`。

## 后续

1. 配置真实密钥，完成 `make test-live` 和真实多轮 HTTP 联调。
2. 扩展 M5 真实模型对抗与语义覆盖；第一版有限句式规则已经接入，不能据此宣称全面安全或合规。
3. 再设计持久化、账户/身份认证、数据删除和部署监控，完成生产上线验收。

## 2026-09-13 请求边界改进

请求正文现在按实际接收字节限制，覆盖分块传输与虚报长度；提前拒绝响应同样包含安全头、请求 ID 和 v1 错误信封。服务关闭开始后拒绝新运行。`make test` / `make check` 只运行离线套件；真实模型验收使用 `make test-live`，需要有效密钥并产生外部调用。详见 `REVIEW-IMPROVEMENTS.md`。
