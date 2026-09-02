# ProjectTown v2 Phase 1C：隔离的真实模型 Adapter 与评测边界

日期：2026-08-20

状态：**实现与离线/模拟验收通过；真实供应商评测未运行（NOT_RUN）**。本记录不能被解读为已完成真实模型效果、成本或可用性验收。

## 目标与范围

Phase 1C 在 Phase 1A 的 provider-neutral 模型调用基础上，保留仅用于单独标记评测的 OpenAI Responses adapter，并新增 native DashScope Qwen adapter。当前 Qwen 固定为 `qwen-plus`，只接受北京地域 workspace base URL 的 `/api/v1`，并由 adapter 固定追加 DashScope generation path；协议、base URL 规则与模型能力以[阿里云 DashScope API 文档](https://help.aliyun.com/en/model-studio/qwen-api-via-dashscope)、[Base URL 文档](https://help.aliyun.com/en/model-studio/base-url)和[`qwen-plus` 文档](https://help.aliyun.com/en/model-studio/qwen-plus)为准。用户确认的安全 cap 是单次不超过 0.5 元、总计不超过 20 元；它只是 fail-closed 保护，不构成付费调用授权。所有 provider 仅允许结构化目标摘要出境，禁止持久化完整 Prompt/Response。schema v3 的同源三元组为 `base_url`、`api_key`、`model`：开发/测试可从受保护的固定本地文件读取；环境来源必须同源且完整地提供对应 provider 的三元组。不存在代码内默认 URL、模型、跨来源补全或回退。

此阶段不把模型接入现有 Quest Planner、不会自动采纳候选、不会调用工具或改变 Quest 状态；也不增加认证、多用户或多节点语义。

## 设计与实现

- `backend/app/v1/openai_adapter.py` 固定使用模型快照 `gpt-5-mini-2025-08-07`，向 Responses API 发送 `store: false`、固定指令、受限结构化摘要和严格结构化输出格式。
- `backend/app/v1/provider_summary.py` 定义窄的 `StructuredGoalSummary`；未知字段、敏感字段名和 canary 标记会被拒绝。adapter 仅接受该摘要及其哈希绑定。
- `backend/app/v1/prompt_registry.py` 保存固定指令的版本/哈希，而非 Quest 原始目标或完整 Prompt。
- `backend/app/v1/model_runtime.py` 与 `storage.py` 使用 migration 6 新增的成本账户与 reservation，在 SQLite 事务中预留、结算或释放微分人民币预算；不改写 migration 1–5。
- `benchmark/real_model_evaluation/runner.py`（及其 fixtures/package 与测试入口）与 formal-v1.0 deterministic runtime simulation 分开运行、分开标记。真实评测需要显式开关、环境密钥和硬预算；默认流程没有网络调用。
- `backend/app/v1/qwen_adapter.py` 使用 native DashScope generation 协议、严格的 workspace URL 校验、固定 `qwen-plus`、bounded response 与 fail-closed structured-output 校验。`benchmark/real_model_evaluation/qwen_runner.py` 是独立、default-off 的 Qwen runner；只允许 sandbox 输出，拒绝 formal-v1.0。
- Qwen 使用独立的 `phase2-qwen-plus-cny-v1` 成本账户与 `dashscope-beijing-2026-08-21` 定价 profile，审计只保留 provider/model/版本、hash 与 Token/成本摘要。它不与 OpenAI 账户共用，也不代表已经发生计费。

请求/响应正文、API key、原始 provider 异常和隐藏推理均不进入数据库、事件账本、遥测、Benchmark 结果或日志。持久化范围是哈希、公开模型/参数版本、状态、安全错误码及 Token/成本摘要。

## v1 不变量与接口影响

| 面向 | 影响 |
| --- | --- |
| API | 不新增面向用户的模型执行端点；`/api/v1`、`/api/v2` 的既有 Quest 语义保持不变。 |
| 数据库 | 仅新增 migration 6；migration 1–4 不改写，migration 5 也不回写。旧库升级、重开、完整性与外键检查纳入回归。 |
| 恢复/回放 | 模型调用审计与 Quest event ledger 分离；`prepared` 可取消，派发后不确定结果保留为 unknown outcome，不把模型候选当作回放真相或已执行计划。 |
| 成果审核 | 不产生可 retain/discard 的成果，不绕过独立 Verifier/Evidence 或用户预览确认。 |
| 安全 | Gateway allowlist、Sandbox、幂等键、原子回执和模糊副作用协调不因 adapter 放宽；adapter 无工具执行权限。 |
| Benchmark | formal-v1.0 继续是 `runtime_simulation=true`、`model_calls=0`、`model_tokens=0`；真实模型评测只能使用独立结果目录和明确标签。 |
| Godot | **Phase 1C 当时**无协议或 UI 变更；之后的独立本地 Settings 增量不改变 Quest/WebSocket 语义，at-least-once 与 `(quest_id, sequence)` 去重要求不变。 |

## 已获得的验证证据

- migration 1–6、成本 reservation/结算/恢复、OpenAI adapter、结构化摘要、Prompt registry、真实评测门禁和 secret canary 均有单元、集成与恢复测试；实现阶段的 mock/离线验证通过。
- 无完整连接配置的真实评测 CLI 被实际执行，结果为 `NOT_RUN`；两条 fixture 的 dry-run 总上限为 32,768 micro-CNY，进程按预期以 exit 2 结束，未创建真实评测结果或数据库记录。当前 schema v3 统一配置后，缺少完整三元组使用 `OPENAI_CONNECTION_REQUIRED`，只提供 URL、Key 或模型中的部分字段使用 `SECRET_CONNECTION_PARTIAL`。
- 已复核 deterministic benchmark 实现仍明确输出 `runtime_simulation`，不调用 LLM。

证据目录（本机）：`sandbox/tmp/p1c-*20260820/`。这些目录是验收痕迹，不是对真实 provider 质量的替代。

## 回滚

代码回滚应只停用/移除未接线的 Phase 1C 调用入口；不要重写或降级 migration 1–6。发布后若要停止能力，停止创建新的 model call，保留已有审计/预算记录以支持审计与恢复；数据库降级必须另行设计数据迁移。

## 限制与未决事项

- 当前环境没有完整且已授权的 OpenAI 三元组（`base_url`、`api_key`、`model`），因此没有进行真实网络调用、实际账单、模型输出质量、供应商限流或端到端延迟验证。
- 即使提供密钥，也必须在运行前复核供应商价格、模型快照可用性、账户限额和用户确认的 0.5/20 元预算；不得把测试默认开关改成自动调用。
- Qwen 的真实网络调用**尚未运行且未获验收**。此前在会话中出现的 Key 视为已泄露，不能写入本地文件、环境、文档或测试；用户须先在供应商侧轮换，再仅通过本机 Quest 设置面板直接输入替换后的 Key。此后仍须有新的、明确的 live/budget 授权，才可进行一次受控实测。
- 后续若要将候选接入 Planner，必须单独设计候选审阅/采纳协议，明确 CAS、ledger、checkpoint、replay 与 stale candidate 的行为，并由 Sol 审核。
