# v2 Phase 1A：可审计模型调用基础层

## 状态与边界

Phase 1A 已实现的是 A-prime（foundation-only）基础层：它为未来、可替换的模型规划调用提供严格数据契约、离线 deterministic fake、可恢复审计记录和原子 Token 预算。它**没有接入**现有生产 Planner，也没有接入真实模型供应商。

因此，现有 `/api/v1`、`/api/v2`、Quest 创建/确认/运行/回放、Gateway、Sandbox、Verifier、Evidence、成果审核和 Godot 的行为保持不变。默认 API、回放和 formal-v1.0 deterministic runtime simulation 不会产生模型调用。

| 组件 | Phase 1A 状态 | 说明 |
| --- | --- | --- |
| provider-neutral DTO 与严格候选校验 | 已实现 | `ModelRequest`、`ModelResponse` 与 planning candidate 使用 Pydantic strict/`extra=forbid` 契约。 |
| deterministic fake adapter | 已实现 | 仅供离线测试与契约验证；不是模型供应商。 |
| `ModelCallCoordinator` | 已实现、未接线 | 可预留、派发、校验并结算候选；不会采用或执行候选。 |
| 模型调用审计与预算 | 已实现 | additive migration 5 保存公开审计元数据、状态和 Token 摘要。 |
| 真实 provider、密钥注入 | 未实现 | 需要供应商、模型、预算和密钥策略的明确决定。 |
| Planner 生产接线、candidate adoption | 未实现 | 必须作为后续版本化协议/事件账本设计实施。 |
| 真实模型评测 | 未实现 | 必须与 deterministic runtime simulation 分目录、分标签报告。 |

`validated_current` 仅说明候选结算时其绑定的 Quest 快照仍是当前版本；它**不表示**候选已被采用、执行、验证完成或改变 Quest 状态。

## 组件与调用边界

`backend/app/v1/model_adapter.py` 定义不依赖 Storage、Gateway、Sandbox 或 Tool Registry 的 provider-neutral 契约。候选在进入任何后续流程前必须经过 allowlist、依赖 DAG、路径安全、大小和哈希校验。

`backend/app/v1/model_runtime.py` 的 `ModelCallCoordinator` 是未接线的显式调用入口。它先对非敏感输入计算哈希、从 Quest 快照建立耐久绑定并预留预算，再派发 adapter；仅持久化输入哈希、公开参数、候选/响应哈希、受限候选 JSON 与使用量/成本摘要。它不持久化原始 Prompt、provider 原始响应、隐藏推理过程、密钥或凭据。

## 数据模型、预算与恢复

Migration 5 只新增以下表、索引和防篡改触发器；migration 1–4 的文本和校验和不变。

| 记录 | 用途 |
| --- | --- |
| `v1_model_calls` | Quest/Contract/Plan/状态版本的不可变绑定，以及一个调用的赢家 attempt。 |
| `v1_model_attempts` | adapter/model 的公开标签、参数摘要、dispatch token、状态、候选/响应哈希、Token/成本摘要和安全错误码。 |

同一 `(quest_id, idempotency_key)` 的重复提交须与既有耐久绑定完全一致；否则被视为冲突。预算预留和检查在 SQLite 事务中完成。已知失败释放预留；派发后无法确定结果的情况标记为 `unknown_outcome` 并继续持有预留。恢复时，未派发的 `prepared` 可安全取消，`dispatched` 转为未知结果；每个调用最多允许三次**显式** retry，不会自动重试。

成功响应被独立校验后只能成为 `validated_current` 或 `stale`。状态版本、Contract 或 Plan 在派发期间变化时，候选保留审计记录但不得当作当前候选采用。

## 隐私与安全

- 密钥和凭据只能留在未来的 secret provider/进程环境边界，不能写入仓库、事件、Trace、模型审计或测试夹具。
- DTO 与摘要参数拒绝敏感字段名；错误记录只保存稳定安全码，不保存 provider 异常原文。
- 规划候选只能引用请求中的工具 allowlist；不安全的绝对路径、父目录遍历、循环/缺失依赖、重复 ID、超限数据或未知 schema 会被拒绝。
- 本阶段没有赋予模型工具权限，也没有绕过 Gateway、Sandbox、Verifier、Evidence、Goal Contract 或成果预览/显式 retain-discard 流程。

## 对既有面向的影响

| 面向 | Phase 1A 影响 |
| --- | --- |
| API | 无新增公开端点、无 `/api/v1` 或 `/api/v2` 语义改变；coordinator 未在 `create`、`run` 或 replay 路径调用。 |
| 数据库 | 旧库可通过 additive migration 5 升级；不改写 migration 1–4；仅新增模型审计/预算表。 |
| Quest 与恢复 | 不追加 Quest event，不更新 projection、Plan、Contract 或 Checkpoint；模型记录独立恢复。 |
| 成果审核 | 无影响；候选不是成果，也不会跳过预览与用户 retain/discard。 |
| 安全 | 仅持久化哈希和公开摘要；仍是单机 SQLite、无内置认证的既有边界。 |
| Benchmark | formal-v1.0 仍是 `model_calls=0`、`model_tokens=0` 的 deterministic runtime simulation；不应被描述为真实模型评测。 |
| Godot | 无协议或界面改动；既有昼夜、Quest 恢复和响应式控制台语义不变。 |

## 回滚与兼容

源代码回滚可移除未接线的 coordinator/adapter 调用点，而既有 API 和 Quest 数据不依赖它。数据库发布遵循只前进的 additive migration 原则：不要重写或降级 migration 1–5。若需要停用该能力，停止创建新 model call，并保留已有审计记录以支持恢复和审计；在迁移版本上回退前须制定独立的数据迁移方案。

## Phase 1B 门禁

接线前必须完成以下门禁：

- 设计版本化的候选审阅/采用协议，明确 Event ledger、CAS、回放、Checkpoint 和 stale candidate 的处理；候选采用不得直接改变既有 Planner 语义。
- 明确 provider、模型、每 Quest/全局预算、成本口径、密钥注入与轮换、超时、重试、数据保留和故障处置策略。
- 为真实模型建立隔离评测集、Prompt 版本、参数记录、Token/成本/延迟摘要及独立报告目录；不得混入 formal-v1.0 runtime simulation。
- 补齐真实 adapter 的契约、失败/未知结果、旧数据库迁移、默认 API 零调用、回放零调用和安全审计回归测试。
- 由 Sol 审查接线差异、迁移、测试证据和变更范围后再验收。

## 需要用户决定的事项

1. 供应商与具体模型，以及是否允许付费 API。
2. 每 Quest、每日/全局 Token 与成本预算，及超限后的用户体验。
3. 密钥来源、开发/部署环境隔离、轮换和日志保留策略。
4. Prompt 的作者责任、版本治理及是否允许保存可审计 Prompt 摘要。
5. 真实模型评测任务集、成功标准与结果的展示方式。
6. 项目许可证、Git/tag 与公开演示媒体；这些仍由作者选择，未在本阶段执行。
