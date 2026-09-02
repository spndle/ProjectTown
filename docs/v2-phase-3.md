# ProjectTown v2 Phase 3：本地 MCP Gateway Adapter

日期：2026-08-22  
状态：**P3A（fixture-only local stdio MCP Gateway Adapter）已由 Sol 验收通过。它不是完整 Phase 3，也不是对真实第三方 MCP server 的验收。**

收官注：本文件中的 migration 1–6、Docker 与测试数字是 P3A 当时的验收快照。当前仓库随后新增了与 MCP 无关的 migration 7 artifact shadow provenance；最新整体状态以 [`v2-closeout.md`](v2-closeout.md) 为准。P3B 若需要持久状态，只能新增 migration 8 或更高版本。

## 目标与非目标

P3A 验证一个最小、默认关闭的本地 stdio MCP 适配边界：已知的固定子进程经 MCP 2025-06-18 生命周期完成 discovery 与一次工具调用，但只能作为现有 Gateway 的受控工具来源。每次 discovery 或调用均使用独立短生命周期进程；默认运行时、API、数据库、Docker、Godot 与 formal benchmark 路径不改变。

P3A **不**提供配置面板、配置文件读取、任意 executable/argv 执行 API、真实 server 兼容性结论、远程 MCP、OAuth、网络沙箱或生产级子进程隔离。它不接入真实模型或 RAG，也不把 MCP result 当作 Evidence。

## 架构链路

```text
固定测试 server descriptor
  -> MCP initialize / tools/list
  -> descriptor + schema hash 比对
  -> 静态 binding 映射为 mcpv1_<server>_<tool>_<binding-hash>
  -> 既有 Gateway allowlist / approval / idempotency / receipt
  -> 独立 stdio tools/call 子进程
  -> 受控结果摘要（不是 Evidence）
```

`backend.app.main.create_app` 只有在 `Settings.enable_local_mcp=true` **并且**调用方显式注入固定 `local_mcp_servers` 时才安装 adapter；开启开关但缺少这份注入配置会在启动前拒绝。默认值为 `false`，因此没有 MCP 子进程、工具名或额外 API 出现在默认路径。

每个 server descriptor 固定绝对 executable、argv、cwd、精简 env、超时与 stdout/stderr 上限。discovery 的完整 descriptor/schema hash 必须匹配静态 binding；名称冲突或 drift 拒绝安装。mutable binding 自动进入已有 Gateway high-risk approval，所有 invocation 继续使用 allowlist、idempotency、原子 receipt 与 `unknown_effect` 协调；没有 MCP 专用 reconcile，也不会自动重试模糊写副作用。现有文件路径 Sandbox 并不能约束任意子进程访问本机，因此 P3A 只允许受信任 fixture，不能据此声称真实 MCP server 已被沙箱化。

## 配置契约与漂移处理

P3A 的 `local_mcp_servers` 是仅由受信任应用创建者在 `create_app` 调用中显式传入的内存 fixture 配置，而非用户、Godot、HTTP 或文件配置面。每个 binding 固定 remote tool、descriptor/schema hash、风险类别及输入/输出约束。adapter 拒绝相对路径、非常规文件/目录、危险名称的环境变量、未支持 schema、重复 remote tool、绑定与 discovery 不符、参数不符或响应协议异常。

内部工具名包含 binding hash，而不是直接沿用远端名称；远端 schema 或 descriptor 任一漂移都会停止该 binding 的安装。调用结果受双流及结果大小上限约束，超时、进程拒绝启动、协议错误或超限均映射为受控 Gateway 工具失败，不能被当作成功回执。

## 安全、恢复与审计矩阵

| 面向 | P3A 行为 | 明确边界 |
|---|---|---|
| 启用 | 默认关闭；缺少显式 fixture 配置 fail closed | 无用户配置面、无任意执行 API |
| 子进程 | 每 discovery/call 独立短进程；固定路径/argv/cwd、精简 env、timeout、输出上限 | Windows `taskkill` 与 POSIX `killpg` 仅经 fixture 验证；不是恶意逃逸防护或 OS 网络沙箱 |
| Gateway | 静态 binding 加入既有 allowlist；mutable 强制 high-risk approval | MCP 不绕过 Gateway、幂等键、receipt 或 unknown-effect 协调；现有路径 Sandbox 不构成子进程 OS 隔离 |
| 审计/Evidence | Gateway receipt 记录现有执行状态；MCP 返回为不可信工具结果 | MCP 返回、Agent 自报、RAG context 都不是 Evidence |
| 恢复/回放 | shutdown 会取消活动 session；模糊写副作用沿用 `unknown_effect` | replay 零 MCP 调用；无新增 reconcile 或外部重放 |
| 数据库/API | 无新增 route、表或 migration；当前 migration 为 1–6 | `/api/v1`、既有 `/api/v2` 和 WebSocket 语义不变 |
| 默认交付 | Docker、Godot、Benchmark 默认路径不加载 MCP | 已验证默认路径回归；不外推为真实 MCP server 验收 |

## 当前验证状态与复现

Sol 于 2026-08-22 完成独立复审和多轮门禁：

- MCP 聚焦 suite：`12 passed`；加上既有 Gateway 单元回归的扩展范围为 `21 passed`；
- 默认 Pytest 两轮：每轮 `467 passed, 10 skipped`；
- 覆盖率门禁两轮：每轮 `87.56%`，高于 `80%` 门槛；
- Ruff、compileall、pip check：均通过；
- Docker：两次 health 为 `ok`，SQLite 原库与备份均 `integrity_check=ok`，migration 1–6 完整；
- Godot 4.7.1：两轮隔离 editor/API Quest 实机通过，昼夜周期与 Docker Quest 恢复通过，两轮各 21 张截图均零像素差；
- formal-v1.0：manifest 两次核对通过，两转新的 4,320 行确定性 simulation 产物与正式哈希完全一致。

上述结果验证 P3A 默认关闭时不破坏既有路径，但仍不覆盖真实 executable 或第三方 server 的安全性和兼容性。全量重复验证期间另外修复了既有 `BoundedTelemetry.close()` 在寿命周期退出时丢弃已接受记录的竞态；该修复不启用 MCP，不改变 API 或持久化语义。

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\unit\test_v1_mcp_adapter.py tests\integration\test_phase3_mcp_gateway.py
```

上述各类门禁必须继续独立报告；不能用 fixture 测试代替 Docker、Godot、恢复或 Benchmark 证据。

## 回滚

P3A 无 API、数据库或 migration 变更。立即停止使用 `enable_local_mcp`/显式 fixture 注入即可恢复默认 runtime；进行源码回滚前，先让应用 lifespan 关闭以取消已知 session。不要以删除 migration、修改既有 Gateway 安全策略或将实际 server 配置写入仓库作为回滚手段。

## P3B：真实 server 激活门禁

只有用户明确决定以下事项，才可开始 P3B：受信任 server 清单、每个 executable/argv/cwd、所需网络与环境变量、逐工具风险/审批/幂等键以及写操作 reconcile 策略。P3B 还必须提供真实 server 的兼容性与失败矩阵、独立于现有路径 Sandbox 的最小权限子进程/网络隔离设计、secret/telemetry 扫描、启动/关闭/崩溃证据，以及 Docker/Godot/default/full regression 的独立验收。

Remote MCP、OAuth、资源/提示词授权与公网/多用户部署仍是单独架构阶段；它们不因 P3A 的 fixture 通过而获得授权。完整 Phase 3 的退出标准仍是：MCP 无法绕过 Gateway；写工具 `unknown_effect` 不自动重试；子进程无不必要 secret；禁用后 runtime 不变，并由 Sol 用真实已批准 server 和完整回归证据验收。
