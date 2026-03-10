# Progress Log

## [2026-03-10 11:27:07] task-3a5046 - 优化
**Status**: SUCCESS
**Commit**: 30d30d3

**任务：Web 端删除确认框优化**

- **做了什么：** 将 Web 端删除任务时的浏览器原生 `confirm()` 对话框替换为页面内自定义 Modal 确认框，涉及单个删除、详情页删除和批量删除三处调用点。同时修复了 `api.py` 中后台线程访问 Flask `current_app` 导致的 "Working outside of application context" 错误。
- **遇到的问题：** `confirm()` 是同步 API，而自定义 Modal 是异步的，需要用 `Promise` 包装并将调用处改为 `async/await`；此外发现 `is_git` 配置在后台线程中读取 `current_app` 会报错。
- **解决方案：** 新增 `showConfirm()` 函数返回 Promise，通过事件监听 resolve true/false，复用已有的 `.modal-overlay` 样式；将 `is_git` 的读取提前到请求上下文中完成。
- **经验教训：** 替换浏览器原生 API 时要全局搜索所有调用点，避免遗漏；Flask 后台线程不能访问请求上下文对象，需在主线程预读配置值。

---

## [2026-03-10 00:35:49] task-f02fd6 - 前端页面LOGO更新
**Status**: SUCCESS
**Commit**: 864472d

- **做了什么：** 将侧边栏 `.sidebar-brand` 的布局从单行改为双列，LOGO 放上方、文字放下方，LOGO 尺寸从 28px 放大到 52px，移动端响应式下回退为行内布局。
- **经验教训：** Flex 布局方向的切换是解决侧边栏空间受限问题的有效手段；响应式适配需要同步处理，避免大屏改动破坏小屏体验。

---

## [2026-03-10] task-a5cc40 - 测试框架评审
**Status**: SUCCESS | **Commit**: ca8c189

对现有测试框架全面评审，输出测试重构计划（`docs/plans/2026-03-09-test-restructure.md`）。发现边界/异常测试和端到端测试严重不足。此任务为纯规划性质，实际重构需后续分步实施。

---

## [2026-03-09] task-3744b8 - 增加 LOGO
**Status**: SUCCESS | **Commit**: 4cb669e

将 SVG LOGO 集成到 README、Web 侧边栏和 favicon。favicon 采用渐进增强：`SVG -> PNG -> Apple Touch Icon`。

---

## [2026-03-09] task-716617 - All Tasks 点击跳转 BUG
**Status**: SUCCESS | **Commit**: 011076e

修复详情页闪现即消失。根因：多面板 SPA 视图切换未保证互斥性。进入新视图时需显式关闭所有其他视图，返回时根据 `activeTab` 恢复正确来源。

---

## [2026-03-09] task-5e0086 - Finalize Plan 文案统一
**Status**: SUCCESS | **Commit**: 66c025e

将 "Generate Plan" 全局替换为 "Finalize Plan"，7 处文本修改。UI 文案应准确反映功能语义。

---

## [2026-03-09] task-a05c6c - Generate Plan 按钮重复点击
**Status**: SUCCESS | **Commit**: 27ec5c4

前端点击即禁用 + 后端幂等性检查，双重防护防止重复提交。

---

## [2026-03-09] task-0a6e06 - RUNNING 状态 View Log 无输出
**Status**: SUCCESS | **Commit**: 4d88df0

`subprocess.run` 改为 `Popen` 流式写日志，前端增加 3 秒自动刷新。实时可见性需求须前后端同步改造。

---

## [2026-03-09] task-f9fae9 - 交互式 Plan 初始 Prompt 优化
**Status**: SUCCESS | **Commit**: 3e7c85e

新增 `send_initial_prompt`，进入 chat 时自动发送任务 prompt 给 AI。前序步骤收集的输入不应在流程衔接中丢失。

---

## [2026-03-09] task-e0a6d4 - Merge Lock
**Status**: SUCCESS | **Commit**: 95a16b3

在 `WorktreeManager` 中新增 `_with_merge_lock`（`fcntl.flock`），防止多 Worker 并发合并竞态。复用项目已有的锁模式保持一致性。

---

## [2026-03-09] task-15cf06 - 前端纵向布局重构
**Status**: SUCCESS | **Commit**: 0798464

横向 Tab 重构为纵向侧边栏。CSS flex + sticky 布局，净减约 576 行。

---

## [2026-03-09] task-347726 - Token 用量统计
**Status**: SUCCESS | **Commit**: a510cb0

新增 `usage.py` + `cf usage` 命令组。双策略架构：`ccusage` 优先，降级为本地日志解析。依赖外部工具时务必设计降级方案。

---

## [2026-03-09] task-c9c2fb - 流程图文档
**Status**: SUCCESS | **Commit**: c7afd86

创建 `docs/flowchart.md`，9 个 Mermaid 图。复杂系统拆分为多个独立图比单一巨型图更清晰。

---

## [2026-03-06] task-c08052 - Web 全流程打通
**Status**: SUCCESS | **Commit**: 98f9fcd

补齐 Web 端完整任务生命周期 API（10+ 端点），耗时操作用 `threading.Thread(daemon=True)` 异步执行。核心模块解耦使 API 层只需薄薄一层胶水代码。

---

## [2026-03-06] task-fcf3e7 - cf plan 后台执行
**Status**: SUCCESS | **Commit**: 870a747

`os.fork()` + `os.setsid()` 守护进程化。关键教训：子进程必须用 `stdin=subprocess.DEVNULL` 隔离 stdin，防止终端状态污染；fork 后需重建所有有状态依赖（文件锁、连接等）。

---

## [2026-03-06] task-db3392 - Markdown 渲染
**Status**: SUCCESS | **Commit**: fa12397

纯前端 Markdown 渲染器，零依赖。按钮可见性应基于数据状态（有无 plan 文件）而非流程状态。

---

## [2026-03-06] task-759ee1 - Reject 按钮无响应
**Status**: SUCCESS | **Commit**: de448c5

两 bug 叠加：前端状态清理顺序错误 + 后端未持久化多字段更新。状态依赖的清理操作应放在状态消费之后；多字段原子更新需 load-modify-save 模式。

---

## [2026-03-06] task-65f477 - 移动端 Tab 适配
**Status**: SUCCESS | **Commit**: b212ad2

纯 CSS + 原生 JS，渐进增强策略。响应式适配优先考虑信息架构切换而非布局缩放。
