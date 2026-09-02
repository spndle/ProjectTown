# ProjectTown v2 Phase 2：确定性 RAG 与离线评测（P2A）

日期：2026-08-21  
状态：**P2A 已于 2026-08-21 由 Sol 验收。** 它不是 Quest 集成，也不是完整 Phase 2 的退出。

## 目标、范围与非目标

P2A 提供一个 provider-free 的确定性检索基础层：调用者显式传入内存文档，得到可重算的 lexical 检索结果与 citation；独立的合成数据集和离线 runner 产生字节稳定的评测产物。它同时提供集中、fail-closed 的本地开发密钥注册位置，供已存在的真实模型评测入口读取。

本阶段不接入 Planner、Quest、Gateway、Sandbox、Event ledger、Checkpoint、恢复、Evidence 或 Godot；P2A 自身不新增 API、数据库表或 migration，其验收快照为 migration 1–6。不调用 OpenAI、Qwen 或任何外部 provider，也不宣称真实模型质量、账单、延迟或可用性已经实测。当前仓库随后新增的 migration 7 只服务于 artifact shadow provenance，最新整体状态见 [`v2-closeout.md`](v2-closeout.md)。

## P2A 架构与数据边界

`backend/app/v1/rag.py` 只接受显式的 `RAGDocument` 值，不读取路径、文件系统、数据库或网络。文档经过 NFC 规范化、大小/数量/metadata 限制后，按确定规则分块；检索使用整数 lexical token 分数、短语加分和稳定的同分排序。它不是问答器，不生成答案或工具调用。

检索结果的 `bundle_hash` 覆盖以下全部内容：schema、index hash、query hash、请求的 `top_k`、retriever/ranker 版本、已排序 hit、每个 hit 的 score 与 citation。citation 绑定 index/query、document/revision/hash、chunk、规范化字符偏移和文本 hash；`verify_citation` 与 `verify_search_result` 会从不可变 index 重新计算，篡改后拒绝。

```text
显式内存文档 -> canonical index -> lexical retrieve/rank -> citation + bundle hash
                                                     -> 独立离线评测产物
```

RAG 文本和命中结果都是不可信上下文：它们不能授予权限、改变 Gateway allowlist、成为 Evidence，或绕过 Goal Contract、Verifier 和成果预览/retain-discard。P2A 的评测集故意包含中英文本、NFC、同分、跨 chunk citation、无答案和提示注入样本；提示注入文本只作为待检索数据，不能变成系统指令。

| 数据类别 | P2A 的位置 | 允许内容 | 禁止内容 |
|---|---|---|---|
| RAG index | 进程内不可变对象 | 显式输入文档、chunk、版本、hash | 自动文件读取、网络、模型调用、Quest 写入 |
| 检索 bundle | 调用方内存/离线结果 | index/query/hash、命中、score、citation | Evidence、权限或工具命令 |
| 离线评测产物 | `sandbox/tmp/rag-evaluation/...` | 合成 dataset hash、指标、结果 hash | 密钥、Prompt/Response、真实模型输出 |
| Provider 配置 | 进程环境或固定本地文件 | 同一 provider 条目中的 `base_url`、`api_key`、`model` 与不可逆 destination hash | 预算、Prompt/Response、任何业务正文、任意代理地址 |

## 离线评测与指标

入口为 `benchmark.rag_evaluation.runner`，其数据集是仓库内的合成、自编写 JSON；runner 不读取 credentials，不构造模型 client，且产物明确记录 `provider_calls=0`、`embedding_calls=0`、`deterministic_rag_evaluation=true`。输出只能位于 `sandbox/tmp/rag-evaluation/` 下；它拒绝 `formal-v1.0` 和真实模型评测目录，不能覆盖既有正式 benchmark。

每次 run 原子写出 `results.json`、`results.csv`、`report.md` 和 `manifest.json`。manifest 记录 dataset/index/retriever/ranker 版本，以及 `results.json`、`results.csv`、`report.md` 三个非-manifest 产物的 SHA-256；manifest 自身哈希由外部验证命令或验收证据记录。它不包含时间戳、时延或绝对路径，以便逐字节复现。

| 指标 | 定义 |
|---|---|
| Recall@k | answer cases 中，检索到的 gold document 数 / gold document 总数。 |
| MRR@k | 每个 answer case 首个相关文档倒数排名的平均值；无相关文档记 0。 |
| Citation precision | answer cases 中，检索 document 与 gold document 的交集数 / 检索 document 总数；即使同一 document 的多个 chunk 被引用，也先按稳定的 document ID 去重归因。 |
| Citation recall / F1 | 以同样的稳定、去重 document attribution 计算 recall，并同 precision 求调和结果；指标针对 gold document ID，不把模型生成文本当作正确性。 |
| No-answer accuracy | 无答案 cases 中实际没有任何 hit 的比例。 |
| No-answer false-positive rate | 无答案 cases 中仍返回 hit 的比例。 |

唯一允许的内置消融轴是 `top_k=1/3/5`；报告必须写明这是 single-variable top-k 比较，不能把它包装为 embedding、reranker 或真实模型质量结论。

### 复现命令

以下示例只创建新的、此前不存在的 sandbox 输出目录；不要把输出指到 committed benchmark 目录。

```powershell
$py = ".\\.venv\\Scripts\\python.exe"
$a = "sandbox/tmp/rag-evaluation/manual-a"
$b = "sandbox/tmp/rag-evaluation/manual-b"
& $py -m benchmark.rag_evaluation.runner --output $a
& $py -m benchmark.rag_evaluation.runner --output $b
Get-FileHash "$a/results.json", "$a/results.csv", "$a/report.md", "$a/manifest.json" -Algorithm SHA256
Get-FileHash "$b/results.json", "$b/results.csv", "$b/report.md", "$b/manifest.json" -Algorithm SHA256
& $py -m pytest -q tests/unit/test_v1_rag.py tests/benchmark/test_rag_evaluation.py
```

同一输入的两套 SHA-256 必须逐项相同；不同输入、dataset hash、ranker/retriever 版本或 bundle 任一字段变化都必须导致可见差异或验证拒绝。默认测试与 formal-v1.0 仍需在发布门禁中分别运行，且不得把此评测同它们混合报告。

## 集中模型配置与本地 Settings（开发/测试）

固定路径是 `D:\pycharmproject\ProjectTown\.secrets\model-providers.local.toml`。该路径被 `.gitignore` 和 `.dockerignore` 排除；它只可用于 development/test，本机可从仓库中的无凭据模板 [`.secrets/model-providers.example.toml`](../.secrets/model-providers.example.toml) 创建。不要在 `docs/`、`.env.example`、代码、测试夹具、日志、数据库、event、trace 或 benchmark 产物中保存真实密钥。

本地文件以 schema v3 保存同源三元组：每个 provider 的 `base_url`、`api_key`、`model` 必须同时存在、同源读取；运行时不存在默认 URL、模型、跨来源补全或回退。无秘密示例：

```toml
version = 3

[providers.openai]
base_url = "https://api.openai.com/v1"
api_key = "replace-with-local-development-value"
model = "gpt-5-mini-2025-08-07"

[providers.qwen]
base_url = ""
api_key = ""
model = ""
```

1. 将示例复制到上述固定路径，初始 `api_key` 保持为空。OpenAI 仍使用其批准的官方入口/模型组合。Qwen 只允许 `qwen-plus` 及 HTTPS 北京 DashScope workspace `/api/v1` base URL；native adapter 固定追加 generation endpoint，不接受完整 endpoint、代理、任意 host 或其他模型。详情见[阿里云 DashScope API 文档](https://help.aliyun.com/en/model-studio/qwen-api-via-dashscope)与[Base URL 文档](https://help.aliyun.com/en/model-studio/base-url)。替换后的 Qwen Key 只能由用户在 Quest 设置面板中直接输入，不能手工写入该文件、环境变量、文档或命令历史。
2. 开发/测试若明确选择文件来源，设置 `PROJECTTOWN_SECRET_SOURCE=local_file` 和 `PROJECTTOWN_PROFILE=development` 或 `test`；同时移除全部 provider connection 环境变量，避免来源混合。
3. 默认来源仍是 `environment`：不设置 `PROJECTTOWN_SECRET_SOURCE` 时，所选 provider 必须由同一来源完整提供其 `BASE_URL`、`API_KEY`、`MODEL` 环境三元组；缺少任一字段都会被拒绝。生产 profile 禁止 `local_file`。
4. 本地文件须是普通、非链接文件，大小受限并通过 ACL/权限检查；权限过宽、软链接/重解析点、schema 不合法、占位值、三元组字段缺失或来源混合都 fail closed。首次空 API Key 是可编辑的配置状态，但严格真实模型调用仍会以安全的 NOT_RUN/缺配置结果结束，而不是网络调用。

Qwen native adapter 与本地配置已经实现，但只通过 offline/Mock 验收；Qwen 在 Godot 中显示为“可配置，真实调用待授权”。DeepSeek 仍显示为不可用。自定义代理、任意第三方 base URL 和其他模型均未启用，仍需要单独的 provider、安全和数据出境决策。预算、价格口径、超时以及是否允许数据出境不进入该本地文件；Qwen 的独立成本账户以 0.5/20 CNY 安全 cap 拒绝超限，但不授予付费调用权限。现阶段 RAG chunk 正文禁止发送给任何模型；仅已批准的结构化目标摘要可进入既有真实模型评测边界，且不得保存完整 Prompt 或 Response。此前在会话中出现的 Key 视为泄露：先在供应商侧轮换，再只由用户直接在 Quest 设置面板输入替换后的 Key。

### Quest 控制台的本地设置面板

这是 **P2A 之后的独立本地配置增量**，不是 P2A RAG 与 Quest 的绑定：它不读取 RAG 内容、不写 Quest/Event/Evidence、不发起 provider 调用，也不改变 `/api/v1`、`/api/v2` 或 WebSocket 语义。面板默认不可用；默认后端只会在非 Docker 注册、`PROJECTTOWN_ENABLE_LOCAL_SETTINGS_CONTROL=1`、`PROJECTTOWN_PROFILE=development` 或 `test`、并且 `PROJECTTOWN_SECRET_SOURCE=local_file` 同时成立时注册 `/local/settings/v1/providers/openai` 与 `/local/settings/v1/providers/qwen`。生产和基础 Docker Compose 中这些 route 不存在。专用 `docker-compose.local-settings.yml` 可用于当前用户的本机 development/test：provider 配置仅在 Docker named volume，宿主 `.secrets` 只经握手后镜像 session token。它还必须同时设置容器专用开关和唯一规范 IPv4 trusted peer，且固定 bridge gateway；服务只接受该精确 peer，不信任 `X-Forwarded-For`/`Forwarded`，仍强制 loopback Host、无 Origin/Cookie/query 和会话 token。容器以独占锁限制单实例，合法受保护的崩溃遗留 token 只会在持锁后原子轮换，其他状态 fail closed。详细启动、预检与回滚见 [`quickstart.md`](quickstart.md)；它不是生产 secret manager。

使用步骤：

1. 先按上文创建受保护的本地 schema v3 文件，保留空 API Key 也可以打开面板；不要将 Key 写进 `.env`、文档或截图。
2. 用本地设置控制面所需的环境开关启动本机后端，然后启动 Godot。会话启动时会在 `.secrets` 创建短时 session token；关闭服务后会清理。不要手工复制、提交或持久化该 token。
3. 在 Quest 控制台标题处点击“设置”。选择 OpenAI 或 Qwen；Qwen 仅可选 `qwen-plus`，Base URL 必须通过后端严格校验。Key 永不回显。选择“保持”、输入新 Key 替换，或显式清除；保存使用 revision CAS 与原子 ACL 写入。轮换后的 Qwen Key 只能由用户在这里直接输入，不能填入文档、命令、环境示例或测试。
4. Godot 只会把 session token 发送到严格的 `http://127.0.0.1:<port>` 或 `http://localhost:<port>` 控制面；HTTPS、远端 host、IPv6、userinfo、path/query/fragment 或缺失/非法端口都会在读取 token 前被拒绝。无 token、无 route 或不支持时，面板只显示泛化不可用状态。

该面板的实现/端到端最终验收由 Sol 单独记录；本段不声明真实 provider 网络、模型质量、账单或延迟已经验证。

## 对 v1 不变量的影响

| 面向 | P2A 影响 |
|---|---|
| API | 无新增或修改；`/api/v1` 和 `/api/v2` 语义不变。 |
| 数据库 | 无 migration；migration 1–6 不改写。 |
| 恢复/回放 | P2A 不适用：无 Quest 写入或 replay 参与。P2B 若接线，必须保存 retrieval bundle，并以零重检索、零模型重调方式回放。 |
| 成果审核 | 无变更；检索命中不能替代独立 Verifier/Evidence 或用户 retain/discard。 |
| 安全 | 无工具权限、无自动 egress；恶意 chunk 仅为不可信数据。 |
| Benchmark | 与 `formal-v1.0` runtime simulation 和 real-model evaluation 分目录、分标签；P2A provider/model 调用为零。 |
| Godot | **P2A 当时**无协议、场景或截图基线变动；其后的本地 Settings 增量只管理本机配置，不绑定 Quest/RAG。 |

## 回滚与进入 P2B 的门禁

P2A 是未接线的离线能力。停用时停止调用 RAG/evaluation/secret resolver；若撤回源码，连同对应测试和本阶段文档一起回退即可。它没有数据库状态，因此不允许也不需要重写或降级 migration 1–6；已生成的 sandbox 评测输出可作为临时证据保留或按本地开发清理策略删除。

P2B 只有在用户授权具体业务语料、许可、敏感等级、保留期、检索目标与数据出境策略后才能启动。migration 7 已在 v2 收官中专用于成果文件 compatibility-shadow provenance，不能被改写或复用。若要把 retrieval bundle 绑定到 Quest、审计、replay 或恢复，必须设计 **additive migration 8 或更高版本**、旧数据库回归和持久化最小 RAG provenance；replay 必须消费已保存 bundle，不能重新检索或重新调用外部模型。P2B 还需证明：恶意 chunk 无法影响权限、正文仍不进入 Event/Evidence/telemetry、外部 embedding/vector store 的成本与许可有独立评测和明确 opt-in。

完整 Phase 2 的退出还要求这些 P2B 门禁与 Quest/recovery 端到端测试均由 Sol 验收；P2A 通过离线测试不等于完整 Phase 2 完成。

## Sol 验收证据摘要（2026-08-21）

- P2A 聚焦测试连续 3 轮均为 `51 passed, 1 skipped`；默认测试连续 2 轮均为 `315 passed, 3 skipped`；覆盖率连续 2 轮为 `89.32%`。
- 三次独立 RAG runner 输出逐字节一致；最终产物 SHA-256 前缀分别为 `results.json=4deb…`、`results.csv=b34f…`、`report.md=5023…`、`manifest.json=932d…`。评测仍为零 provider、零 embedding、零模型调用。
- Docker 最终三轮均得到 health `200`，并复核 migration 1–6、SQLite integrity/foreign-key 检查、Godot restore 与 18 项视觉回归通过。

### Provider 配置增量（P2A 之后；最终验收待 Sol）

- 后续 schema v3/configuration 与 Settings control-plane 的实现状态、测试结果和验收结论以该增量的 Sol 复审记录为准；不得倒灌为 P2A 的 RAG 成果。
- 该增量不发起真实 Provider 网络请求，也不改变“真实模型质量、价格、延迟和可用性尚未验收”的结论。

这些是 P2A 的本地验收证据，不构成真实模型、外部 provider、Quest-bound RAG 或 P2B 的验收结论。
