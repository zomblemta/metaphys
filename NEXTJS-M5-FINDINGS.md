# Next.js 迁移与 M5 第一版

日期：2026-09-12。前端按用户要求迁移至 Next.js；同时继续 M5 的基础实现。

## 前端

- Next.js 16.3.5 App Router、React 19.3.0、TypeScript；版本锁定在 package-lock.json。
- `app/layout.tsx` / `app/page.tsx`；独立 BirthForm、Messages、ChartPanel、Workspace 组件；lib 分离 API、SSE 和类型。
- 旧的命令式原生页面已移出项目，不采用在 React 中重新运行旧脚本的包装方式。
- 默认构建静态导出至 frontend/out，由原 FastAPI 网关同源提供。开发模式可 Next.js 热更新，代理本地 API。
- API 及 SVG 保留原 Cookie/CSRF/归属校验。开发 Origin 仅在 METAPHYS_NEXT_DEV=1 时接受本地 3000，不默认扩大允许来源。
- Next.js 内联 hydration 脚本按导出内容计算 CSP SHA-256；未开启 script-src unsafe-inline。构建资源路径检查目录边界，symlink 出目录被拒绝。
- SSE 单测以逐字节分块覆盖 UTF-8、多字节中文、CRLF、保活及截断。

## M5 安全规则

SafetyMiddleware 在模型调用边界实现两层处理：

1. 当前用户消息命中明确风险句式时，直接给出替代回应，不调用模型或排盘工具。
2. 模型输出命中危险诊断、具体投资指令、必然灾祸/死亡或法律结果断言时，替换正文并移除同轮工具调用。

当前分类：self_harm、medical、financial、fatalism、legal。safety_flags 保留历史审计；response_safety 仅标识当前回复，response_status=safety_redirect。下一轮普通对话不会被历史安全事件持续标记。

否定仅作用于当前匹配的紧邻前缀，句间分别检查；例如“不根据八字诊断”不等于允许后一句“按星盘决定治疗”。普通财运、健康主题、规则本身的讨论不按裸关键词拦截。

**能力边界：** 这是有限中英文句式规则，尚非完整语义风险识别。变体、隐喻、引用、其它语言可能漏报或误报；不宣称所有危险内容均能被识别。没有进行法域合规认证，也没有将它描述为生产上线完成。

危机回应采用支持、即时危险时联系当地急救、联系可信任的人及减少危险物品接触的方向，不猜测用户所在地或提供未经核实的当地热线。参考：[NIMH 支持有自杀想法者的行动建议](https://www.nimh.nih.gov/health/publications/5-action-steps-to-help-someone-having-thoughts-of-suicide)。

## 验证

- 后端 `make check`：**495 passed、15 skipped**；68 文件格式与 ruff 通过。2 条第三方弃用警告。
- Next.js `npm run build`：生产构建与静态导出成功；TypeScript 检查通过。
- `npm test`：2 项前端 SSE 测试通过。
- M5 新增 27 项正反例、同步/异步、历史隔离和工具调用阻断测试。
- 中间件由 3 增至 4 个；真实 debug stream 实测一轮工具加收尾为 9 个任务节点，递归预算已相应调整。
- 浏览器验证：Next.js 表单→真实八字引擎→安全替代回应→继续排星盘→刷新恢复；SVG 加载正常，普通后续回复不保留当前安全警告。
- 390px 手机布局无横向溢出；生产构建的控制台无 error/warn，CSP 未阻止 hydration。
- HTTP 单测用最小构建夹具避免依赖 Node；浏览器验收使用真实 Next.js 构建。

验收模型为明确标注的脚本模型；真实排盘引擎参与。没有真实 DEEPSEEK_API_KEY，因此真实模型对抗测试与真实模型 HTTP 联调仍未完成。

## 仍未完成

真实模型联调与更多安全对抗样本；Postgres 持久化、账户认证、跨设备会话、完整数据删除/保留策略和生产部署。当前依然是内存、单进程、本地版本。

前端构建方案参考：[Next.js 静态导出](https://nextjs.org/docs/app/guides/static-exports)、[开发代理 rewrites](https://nextjs.org/docs/app/api-reference/config/next-config-js/rewrites)。本项目具体行为以上述本地验证为准。
