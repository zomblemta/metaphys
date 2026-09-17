# M4 网关与前端：本地版交付

日期：2026-09-12。状态：本地应用已交付；真实模型联调缺密钥，生产部署与 M5 未交付。

## 交付范围

- FastAPI 同源网关；默认仅监听 127.0.0.1:8010。
- HttpOnly/SameSite Cookie + CSRF + Origin/Host 校验，服务端会话归属隔离。
- 服务端生成对话 id；创建、恢复、切换、删除；删除同步清理 checkpoint。
- SSE 进度与核验后的终态；超时/错误统一提示；同会话并发拒绝。
- 浏览器断线不取消后台模型任务，刷新可恢复；真实 TCP 断开测试验证。
- 根据当前会话的有效 AstroChart 推导 SVG 路径，检查目录边界；不接受用户文件路径，不公开可列举目录。SVG 加 sandbox CSP 与 no-store。
- 中文响应式前端：资料表单、八字/星盘选择、多轮消息、四柱、行星落点、SVG 及完整数据；用户与模型文本按文本节点渲染。
- uv.lock、make install/dev、README 启动和 API 契约。

框架选择沿用项目已有 FastAPI 方向。前端选择原生模块以避免为本地 MVP 再引入独立服务、跨域认证和构建链；仍可在之后迁移到组件框架，公开 API 已独立。

## 验证证据

最终 `cd backend && make check`：**464 passed、15 skipped**；ruff 与 66 文件格式检查通过。`node --check frontend/app.js` 和 `git diff --check` 通过。两条第三方弃用警告不影响测试。

新增 `test_gateway.py` 13 项，包括：真实图的 HTTP→工具→SSE→刷新→删除 checkpoint；另一浏览器禁止读取/修改/删除；CSRF/Host/静态路径；无效输入拒绝；SVG 归属及 symlink 出目录；模型失败/超时；并发拒绝；到期回收；真实 TCP 断开后结果继续保存。

浏览器人工自动化验收：

- 资料表单提交八字、同会话追问星盘，两盘共存，真实 SVG 加载成功。
- 刷新恢复原对话与命盘。
- 桌面 1366px 和手机 390px 布局；手机 scrollWidth=clientWidth=390，无横向溢出。
- 浏览器控制台没有 error/warn。
- `<img src=x onerror=alert(1)>` 输入作为文本显示，对话 DOM 中新增图片数量为 0。

浏览器验收的模型是明确标注的脚本模型，日期地点来自固定测试夹具，排盘引擎真实执行。项目没有 .env，进程没有真实 DEEPSEEK_API_KEY；真实模型对抗测试仍跳过。不能据此宣称真实模型质量或生产安全已验收。

## 已知边界

- 会话/checkpoint 在内存，重启丢失；单进程，不能多 worker。
- 依赖本地浏览器会话所有权，不是账户认证系统。
- 图文件不随会话删除，避免误删共享图；停止服务后可清理 var/charts。尚无自动磁盘保留策略。
- 任务异常/超时后该会话禁止继续，提示新建，避免消费半轮状态。
- 前端出生表单只填写公历；农历通过聊天明确输入。
- M5 仍只有预留通道。页面用途说明不替代安全判定。
- 原有星盘 grounding 句式覆盖范围仍有限。

实现与接口的参考：[FastAPI 自定义响应文档](https://fastapi.tiangolo.com/advanced/custom-response/)；具体行为以上述本地测试为准。
