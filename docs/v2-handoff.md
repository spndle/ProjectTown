# ProjectTown v2.0 新会话交接

本文件记录的是 **2026-08-13、Windows / Python 3.12.13 / Godot 4.7.1 / Docker
Desktop** 的 v1.0 冻结基线。它是 v2 的兼容护栏；下列运行结果属于有日期的历史证据，
新会话必须先重跑启动门禁，不能把它们当成当前环境仍然通过的证明。外部
`ProjectTown-项目开发文档.md` 是早期规划材料，冲突时以本文件、最终验收报告和当前代码为准。

## v1.0 已冻结事实

- 版本：`1.0.0`；核心 API 为 `/api/v2`，`/api/v1` 保持兼容。
- 发布边界：loopback、单节点、单进程 SQLite；无真实 LLM、无内置认证、非公网生产系统。
- 核心不变量：Goal Contract 两阶段确认、append-only Event ledger、状态版本 CAS、独立
  Verifier/Evidence、工具 allowlist 与 Sandbox、原子回执、Checkpoint/恢复、Watchdog、
  预算、受限重规划、成果冻结/预览/显式保留或丢弃。
- Godot：像素小镇、昼夜/云层、教程、响应式控制台、成果放大查看、历史 Quest 恢复；
  切换 Quest 后按来源 ID 丢弃迟到响应。
- 正式评测：30 Quest、B0–B4、七项消融、4,320 条 deterministic runtime simulation。
- 最终自动化：97 passed，覆盖率 87.75%；Docker healthy；Godot live transport 与只读
  restore smoke 通过；Benchmark 四项哈希可复现。

## v1.0 不应在 v2 中破坏的兼容面

1. 不改写既有 migration 1–4；数据库演进只允许 additive migration，并提供旧库回归。
2. 保留 `/api/v1` 兼容面和 `/api/v2` 现有请求/响应语义；协议变化必须版本化。
3. 任何 Quest 状态变更仍须通过事件账本、CAS 和可回放 projection。
4. 任何外部副作用仍须经 Gateway、allowlist、idempotency、receipt 与恢复协调。
5. Agent 声称完成不能替代 Verifier；成果必须先预览，只有用户明确选择后才保留或丢弃。
6. WebSocket 仍按 at-least-once 处理，客户端按 Quest 与 sequence 去重并可 REST 对账。
7. 不把 runtime simulation 数值包装成真实模型、Token、延迟或生产效果。

这里的 append-only 指 Event、Evidence、Contract/Plan 等受不变性约束的账本；Projection、
Checkpoint、Lease 与其他运行记录仍会受控演进。“只允许 additive migration”是 v2 必须
遵守的发布策略，并非数据库引擎会自动保证的性质。

## v2.0 建议优先级

### P0：先完成公开 v1 发布动作

- 由作者选择项目许可证；
- 初始化/关联 GitHub，审查首个提交并打 `v1.0.0` tag；
- 发布演示视频、截图/GIF 与 GitHub Release。

### P1：真实模型适配与可评测性

- 在现有确定性 Planner/Executor 接口后接入可替换的真实模型 adapter；
- 密钥仅通过环境/secret provider 注入，不进入事件、Trace 或仓库；
- 保存公开可审计的模型名、采样参数、Prompt 版本、Token/成本摘要，不保存隐藏思维过程；
- 将真实模型评测与 deterministic runtime simulation 严格分目录、分标签报告。

### P1：历史与可观测性产品化

- Quest 历史搜索、状态筛选、分页和归档；
- 失败详情、恢复点、Decision、Evidence、Artifact 的可导航关联；
- 将视觉测试夹具彻底离线化，避免向真实后端轮询虚构 Quest；
- 建立确定性 UI 截图基线，而不是只依赖人工截图。

### P2：认证、多用户和部署演进

- 先定义用户/Quest/Workspace 所有权与授权矩阵，再增加认证；
- 若要多进程或多节点，不能继续把 SQLite 与进程内锁当作协调机制；应单独设计数据库、
  lease、队列、幂等与迁移方案，并准备数据恢复演练；
- 公网部署必须有 TLS、认证、速率限制、审计与 secret 管理。

## 协作方式

- 主线程使用 `gpt-5.6-sol` medium，负责需求、方案、风险、审查和验收。
- 当前运行环境只有 Sol 与 Terra；所有有边界的探索、实现和测试使用
  `gpt-5.6-terra` medium，不尝试 Luna。
- 使用 `$sol-terra` 工作流；同一区域修改必须串行，Terra 完成不等于 Sol 验收。
- 每次开发先读本文件、`docs/validation-v1.0.md`、`docs/architecture.md`、
  `docs/limitations.md` 和目标代码，再提出最小方案。

## v2.0 启动门禁

首次 v2 修改前必须记录：

- Git 状态与 v1 tag（若已完成）；
- Docker 健康、数据库备份与 migration 1–4；
- `pytest` / Ruff / compileall / pip check 基线；
- Godot 4.7.1 editor、API smoke、restore smoke；
- formal-v1.0 manifest 的四个哈希。

任何 v2 方案都必须说明：对 v1 API、数据库、Quest 恢复、成果审阅、安全边界、Benchmark
与 Godot 的影响，以及明确回滚路径。
