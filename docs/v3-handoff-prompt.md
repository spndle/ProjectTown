# ProjectTown v3.0 新会话启动 Prompt

> **2026-08-24 取代通知：**本文件旧的“第六节 v3.0 已知缺口与建议顺序”及其首要建议，已由用户批准的 [`v3-product-direction.md`](v3-product-direction.md) 取代。本文其余历史事实、v1/v2 兼容护栏、秘密处理规则和协作约束仍然保留有效；不得把新章程中的未来方向误报为已实现行为。

> **2026-08-24 Phase 0/1/2 状态：**[`v3-phase-0.md`](v3-phase-0.md) 的离线 metadata 检查器已完成；[`v3-phase-1.md`](v3-phase-1.md) 的隔离离线内容建议、显式确认、来源绑定、预览、创建式导出和外部恢复工程闭环已通过工程验收。[`v3-phase-2.md`](v3-phase-2.md) 的外部 Study→Trial→Summary record tooling 也已通过离线工程验收：Study 严格绑定固定 T001–T010 candidate manifest hash，drift 会被拒绝；records 仍是 external self-consistent、unanchored，fixed candidates 与 synthetic fixture 不构成真人证据，10 个真实任务与 7/10 人工采纳价值门槛仍未接受。Web UI、直接 Apply、Quest/API/数据库接线和认证型外部会话证明也未实现。

> 本文件是当前会话的脱敏交接，不包含曾在会话中出现的 API Key、私有 Base URL、session token、完整 Prompt/Response 或 `.secrets` 内容。复制本文件全文到新的 Codex 会话即可开始 v3.0。

---

$sol-terra

你现在接手 ProjectTown v3.0 的设计与开发。

项目根目录：

`D:\pycharmproject\ProjectTown`

## 一、协作与权限规则

- Sol 使用 `gpt-5.6-sol`、medium，负责需求澄清、架构、风险、任务拆分、代码审查、证据复核和最终验收。
- 当前环境使用 Terra 承担所有边界明确的探索、实现和测试任务，使用 `gpt-5.6-terra`、medium。
- 不得尝试调用 Luna；即使旧文档或旧 prompt 提到 Luna，也必须使用仓库当前 Sol-Terra 工作流。
- Terra 完成不等于验收通过。Sol 必须检查实际 changed paths、关键代码、migration checksum、命令 exit code、测试计数、Docker/SQLite 状态和变更范围。
- 同一文件或同一代码区域的写入任务必须串行；并行只用于独立的只读探索、测试和日志分析。
- 遵守根目录 `AGENTS.md` 的八字段委派合同。不得让代理自行扩大范围、删除共享数据、读取秘密或替用户执行公开发布。
- 不得调用任何真实模型、外部 embedding、真实 MCP server 或付费 API，除非用户在新会话中重新给出明确的供应商、模型、预算、数据出境和 live-call 授权。

## 二、开始修改前必须完整阅读

首先完整阅读 `docs/v3-product-direction.md`、`docs/v3-phase-0.md` 和 `docs/v3-phase-1.md`；前者是当前 v3 的规范性产品方向，后两者记录已实现的 Phase 0/1 合同与边界，均优先于本文件中已被取代的旧路线图。

1. `docs/v2-closeout.md`
2. `docs/v3-handoff-prompt.md`
3. `docs/adr-0001-artifact-shadow-provenance.md`
4. `docs/code-audit-2026-08-22.md`
5. `docs/project-development-v2.0.md`
6. `docs/v2-handoff.md`
7. `docs/validation-v1.0.md`
8. `docs/architecture.md`
9. `docs/limitations.md`
10. `docs/v2-phase-1a.md` 至 `docs/v2-phase-1e.md`
11. `docs/v2-phase-2.md`、`docs/v2-phase-3.md`
12. `README.md`、`AGENTS.md`
13. 当前后端、Godot、测试、Docker、Benchmark、RAG、MCP 和验证脚本代码

外部《ProjectTown-项目开发文档.md》和旧聊天结论属于早期规划材料。若与当前代码、v2 closeout、ADR、v2 交接或最新实测冲突，以当前代码和上述仓库文档为准。不得仅沿用本 prompt 中的通过数字；必须重新运行验证。

## 三、v2.0 收官后的当前事实

ProjectTown 是一个“可审计、可恢复、可评测的长任务 Agent Runtime”：把模糊目标变为需要用户确认的 Goal Contract，再执行 milestone DAG，通过受控 Gateway 调用工具，由独立 Verifier 重新读取当前成果并生成 Evidence，最后在需要时让用户预览并明确保留或丢弃成果。

当前支持边界仍是本地、loopback、单用户、单进程、单节点、SQLite；不是认证后的公共 SaaS、HA、多节点系统或任意代码执行平台。

已交付的核心：

- `/api/v1` 兼容面和 `/api/v2` Quest runtime；
- Goal Contract 两阶段确认；
- Event ledger、state-version CAS、Checkpoint、replay、暂停/恢复；
- Gateway allowlist、Sandbox、幂等键、原子 receipt、模糊副作用协调；
- 独立 Verifier/Evidence、Watchdog、预算、受限 replan；
- 成果 hash-frozen preview、幂等 retain/discard 和中断恢复；
- WebSocket at-least-once 与 Godot `(quest_id, sequence)` 去重；
- Quest 历史搜索、状态筛选、分页和基础恢复；
- Godot 4.7.1 昼夜、live API/restore smoke 和 7 fixture × 3 viewport 的确定性截图回归；
- formal-v1.0 deterministic runtime simulation Benchmark；
- Phase 1A–1C 的 provider-neutral contract、attempt/cost ledger、隔离的 OpenAI/Qwen adapter 和默认关闭的本地 Settings 控制面；
- Phase 2A provider-free deterministic lexical RAG/citation/bundle 与合成离线评测；
- Phase 3A default-off、fixture-only local stdio MCP Gateway Adapter；
- migration 7 compatibility-shadow artifact provenance：受限 baseline/final snapshot、原子 `write_file` observation、manifest/Evidence/action/event 绑定、历史 `legacy_unobserved` 和 Godot 只读审计提示；
- Git ignore 与 Docker build context 均已排除 `.env`、`.env.*` 和 `.secrets/`，但保留 `.env.example`。

v2 收官时的有日期证据快照是：默认 Pytest 两轮及 Sol 独立轮均为 `605 passed, 12 skipped`；coverage 两轮 `92%`；Docker 最新代码重建、重启和 SQLite migration 1–7 通过；Godot 4.7.1 两轮视觉回归均 21/21 且像素误差全零；formal 两次 4320-row 重生成和 P2A RAG 两次输出逐文件一致，真实 provider/embedding 调用均为 0。一次复用旧 pytest basetemp 的尝试因 Windows SQLite 锁产生 setup errors，已明确判为无效而不是通过。新会话必须用全新目录重新验证，不能直接沿用这些数字。

仓库的正式发布标识仍是 `VERSION=1.0.0`、health `1.0.0`、Godot 标题 `v1.0`。这里的 v2.0 是本地开发里程碑收官；在用户选择许可证、Git/tag 和发布策略前，不得擅自把它描述为已发布的 2.0.0。

### Migration 不变量

migration 1–7 均已版本化。1–7 的期望 checksum：

```text
1  a5cf0cc34069eb682302ddfb7fc73dc512813b58d1ce1a805d5dcc66a98e0404
2  64ba14a8f3d5b7d33083d5567b43f8096b80b7d15d72bfdc12a12d92d06e0d44
3  5790cbf8ea42f8eca1d7b56e2ee5ea073c3a423ac1daf086d655baf54e4fbdf3
4  a13b4bdb679de98545c0427aa66e4de7f9fb1e808eb2af11deb1b35ae3bb888c
5  35e9cdedf7b779368ccfe2a7dca520adf27c07e689dfbb40860a7338e90c1e36
6  a00417e01f234239c4f7715750c4b9f34fc1ea5385828b1fc895d49b4ebd936c
7  53eac8c6c4241107c5c1cd3e85e2ef162f9a6322e5d288f706924a785589a7ce
```

- migration 1–7 不得重写；v3 只能新增 migration 8 或更高版本。
- Quest-bound RAG provenance 若启动，必须使用 migration 8+，不能复用 migration 7。
- 每个新 migration 都必须有 0/4/5/6/7 旧前缀升级、重复打开、checksum 冲突、future/gap 拒绝、失败原子回滚、旧数据库 replay/checkpoint/recovery 回归。
- additive migration 不做破坏性 down migration；需要数据回退时只能停服恢复完整、已验证的备份。

## 四、必须保持的 v1/v2 不变量

- `/api/v1` 兼容面和现有 `/api/v2` 语义不得被静默破坏；行为升级必须版本化。
- Goal Contract 继续两阶段确认，Agent 不得自行确认用户目标。
- Event ledger 是状态迁移唯一写路径；保持 CAS、replay、Checkpoint 和恢复。
- 工具必须通过 Gateway allowlist、Sandbox、approval、幂等 receipt 和 unknown-effect 协调；模糊写副作用不得自动重试。
- Verifier/Evidence 独立，不能相信 Agent、RAG、MCP 或调用方自报完成。
- Watchdog、步骤/工具/消息/时间预算和受限 replan 继续成立。
- 成果审核启用时必须先预览，由用户明确 retain/discard；shadow provenance 不能替用户决定。
- WebSocket 按 at-least-once 处理，并按 `(quest_id, sequence)` 去重和断线对账。
- replay 不重新调用模型、检索、MCP 或工具。
- deterministic runtime simulation、deterministic RAG evaluation 与真实模型评测必须分别报告，不能混写指标。
- SQLite、RLock、进程内 lease/heartbeat 不能直接扩展为多节点方案。

## 五、当前会话形成的模型与秘密决策（已脱敏）

- 用户早期曾指定 OpenAI / `gpt-5-mini`，单次不超过 0.5 元、开发阶段总计不超过 20 元；只允许结构化目标摘要出境，禁止保存完整 Prompt/Response。
- 后续因开发测试成本，用户倾向使用 Qwen（当前允许模型配置包含 `qwen-plus`）进行未来实测。这个倾向不等于新会话的 live-call 授权。
- Provider 配置是不可拆分的同源三元组：`base_url`、`api_key`、`model`。不得写死，不得跨环境变量/文件/面板混合来源。
- 本地统一位置为忽略的 `.secrets/model-providers.local.toml`；Docker opt-in Settings 模式把配置保存在专用 named volume `/app/.secrets`，Godot Quest 控制台 Settings 只通过受限本地控制面编辑。GET 必须脱敏，API Key 只允许 keep/replace/clear 语义。
- 不得读取、复制、打印或写入文档中的 `.secrets` 实际内容。配置面板可用不代表 provider 已授权或调用成功。
- 先前会话中曾明文出现供应商 API Key。该 key 视为已泄露，任何真实调用前必须由用户在供应商侧撤销并轮换；不能因为它仍可能存在于本地 Docker volume 就继续使用。
- 新文档、测试、日志、Event、Evidence、trace、OTel 和 Benchmark 不能保存完整 Prompt/Response、Key、Authorization header 或私有 endpoint。只允许经批准的结构化目标摘要、prompt/version hash、模型参数摘要、token/cost/latency 摘要。

## 六、v3.0 已知缺口与建议顺序

### V3-1：版本化成果审核与 provenance fail-closed

需要先由用户决定：

1. 新版模式是否对所有产生写成果的 Quest 强制 `waiting_user`；
2. 无 previewable artifact 时允许直接完成还是失败；
3. 旧 `/api/v2` 保持兼容，还是新增明确版本/behavior profile；
4. 单 Quest 是否独占 workspace，以及外部进程/用户并发修改如何归责。

决策后才能让 observer 取代 self-reported `diff_scope`、把 shadow 状态作为门禁，或增加 provenance detail API。migration 7 只允许 `shadow`、`legacy_unobserved`、`unrecoverable`，不得原地增加 `verified`；强证明需要 migration 8+ 和版本化语义。

### V3-2：Quest archive 与失败/恢复导航

当前已有搜索、状态筛选、分页和失败摘要，仍缺 archive/unarchive 语义、失败原因到 event/Evidence/checkpoint/恢复动作的导航。先定义 archive 是“默认隐藏”“禁止继续执行”还是两者，以及 unarchive 是否二次确认；ledger 不得删除。

### V3-3：统一 provider registry 与全局成本域

OpenAI/Qwen adapter 已隔离，但注册、host/model allowlist、pricing 和成本账户仍按 provider 分支。真实调用前必须明确：20 元是跨 provider/数据库共享的开发阶段总额、周期额度还是各自额度；谁能 reset；如何与 provider 账单对账。实现共享不可随 CLI 任意替换的 cost ledger、provider manifest/registry 和 prompt/token/cost 查询投影后，再做一次最小 live evaluation。

### V3-4：真实 MCP Phase 3B

P3A 只证明仓库 fixture 合约。用户必须指定第一个 server 的固定 executable、argv、cwd、工具 allowlist、文件/网络范围和风险/审批矩阵。需要消除 hash-to-spawn TOCTOU，使用 OS/容器级进程 containment，并验证超时、取消、输出上限、unknown effect、重启恢复和无 secret 环境。不得把现有 Sandbox 描述为任意子进程沙箱。

### V3-5：工程化与发布

- strict config：非法布尔/枚举配置应启动失败，不应静默变为 false；
- telemetry exporter cooperative cancellation 或可终止进程边界；
- 在 characterization tests 后逐步拆分过大的 `storage.py`、`service.py`、Godot `main.gd`，禁止一次性重写；
- dependency lock/constraints、Docker base digest、SBOM、clean-install CI；
- 用户选择许可证、Git 远端/首次提交、release tag 和演示媒体后才能公开发布；
- 认证、多用户、PostgreSQL、durable queue、distributed fencing 和 WebSocket fan-out 另立架构阶段。

## 七、新会话第一轮工作方式

先全面审查，不直接沿用本文件的结果：

1. 记录无敏感值的 Git、许可证、Python、依赖、Docker 与端口状态；
2. 使用 SQLite online backup 备份 live DB，并验证 integrity、foreign key 和 migration 1–7 checksum；
3. 运行 Ruff、compileall、pip check、默认 Pytest 两轮和 coverage fail-under 80 两轮；
4. 重建/复验 Docker health、安全选项、loopback、Settings 无 token 403、数据库完整性和 provenance 端到端；
5. 运行 Godot 4.7.1 editor/main/API/restore/time/provenance/layout 与完整视觉回归两轮；
6. 重生成 formal-v1.0 与 P2A RAG 临时结果两轮并逐文件比对 committed manifest；运行 P3A focused tests 两轮；
7. 做脱敏 secret hygiene 扫描，但不读取 `.secrets/**`；记录不可访问范围，禁止提升权限强行扫描；
8. 输出当前状态、实际证据、v3 gap、分阶段路线图、第一迭代最小可回滚方案和需要用户决定的重大事项。

若第一迭代不依赖成果审核语义、预算域、provider、真实 MCP、许可证或部署架构等重大选择，则直接实施、测试并由 Sol 验收。若确实依赖，只询问最关键的一个问题，不得自行假设授权。

建议 Sol 优先判断：是否先做不依赖产品决策的 strict-config/可维护性 characterization 小迭代，还是先向用户询问 V3-1 的版本化成果审核语义。不要为了增加技术栈标签而引入 LangGraph、向量库、Redis、Kafka、PostgreSQL 或 Kubernetes。

## 八、每次交付必须报告

- changed paths 与未触及范围；
- API、数据库、恢复、成果审核、安全、Benchmark/RAG/MCP 和 Godot 影响；
- migration/checksum 与旧库回归；
- 每条验证命令的完成状态、exit code、pass/fail/skip 数和输出路径；
- deterministic 与 real evaluation 的明确分离，以及实际 provider call 数；
- 回滚方法和仍需用户决定的事项；
- Terra 证据与 Sol 独立复核结论。

不得把“测试已启动”“Terra 已完成”“Mock 通过”“Settings 可保存”或“Docker healthy”单独等同于 v3 功能验收。
