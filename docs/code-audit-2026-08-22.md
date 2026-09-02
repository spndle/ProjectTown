# ProjectTown v2.0 全面代码审查与优化方案（2026-08-22）

## 1. 结论

ProjectTown 当前不是“技术性不足”的普通 Agent Demo。它已经具备 Goal Contract 两阶段确认、事件账本、状态版本 CAS、Checkpoint/回放、Gateway 幂等与模糊副作用协调、独立 Verifier/Evidence、预算与受限重规划、成果预览、WebSocket 去重、确定性 Benchmark、Godot 客户端和加固后的本地 Docker 运行形态。

本轮审查未发现需要停止使用现有单节点开发实例的 P0 数据损坏缺陷。当前主要问题不是缺少更多框架，而是四个“可信边界”尚未完全闭合：

1. 成果审核在现有 `/api/v2` 中仍是 opt-in，和“所有成果必须先审核”的目标不变量存在语义差距；
2. `diff_scope` 与成果归属缺少独立的文件系统基线/差异观察器，证明链仍部分依赖执行期元数据；
3. 20 元总预算是按当前 SQLite 成本账户约束，不是跨数据库、跨供应商账户的全局财务上限；
4. Phase 3A 只验收了 fixture/local MCP Adapter 合约，真实 MCP 子进程的哈希到启动 TOCTOU、文件/网络/进程隔离尚未验收。

这四项中，第一、三、四项需要用户先确认产品或部署边界；第二项需要新增 migration 7 和旧数据库回归，不能以小补丁冒进。

> 收官更新：上述结论是本轮优化前的审查快照。随后已按 Iteration 1 实施 additive migration 7、受限 baseline/final scanner、原子写 observation、成果 shadow provenance、旧 Quest `legacy_unobserved` 恢复和 Godot 只读提示。它闭合了“可观察、可追溯”的旁路记录，但没有把 shadow 升格为独立所有权证明，也没有改变成果审核默认语义；fail-closed 切换仍属于 v3 决策。详见 [`adr-0001-artifact-shadow-provenance.md`](adr-0001-artifact-shadow-provenance.md) 与 [`v2-closeout.md`](v2-closeout.md)。

## 2. 审查范围与方法

本轮重新阅读并以当前代码为准审查了：

- `docs/v2-handoff.md`、`docs/validation-v1.0.md`、`docs/architecture.md`、`docs/limitations.md`；
- `README.md`、`AGENTS.md` 和 v2 Phase 1A–3 文档；
- 当前后端、Godot、测试、Docker、脚本和 Benchmark 代码；
- 运行中的 Docker Settings 模式、SQLite named volume、Godot 4.7.1 和正式 Benchmark 产物。

没有沿用旧会话的通过结论；所有下述状态均在 2026-08-22 当前工作区重新执行。没有调用真实模型 API、没有网络评测，也没有读取或输出 provider 密钥。

当前目录没有 `.git` 元数据，因此无法使用 Git diff、commit 或 tag 作为变更证据。本轮由 Sol 直接检查改动文件、关键符号、SHA-256 和重复测试结果。根目录也没有许可证文件。

## 3. 当前基线证据

### 3.1 源码、依赖与发布状态

| 项目 | 当前证据 | 判定 |
|---|---|---|
| Git | 根目录无 `.git` | 尚不能形成 commit、tag、可追溯 release |
| 许可证 | 根目录无 `LICENSE*` | 必须由用户选择，不能代选 |
| Python | 3.12.13；pip 25.0.1 | 当前开发环境可用 |
| 依赖 | `requirements*.txt` 只有版本区间，无 lock | 可重复构建仍有漂移风险 |
| Docker 基础镜像 | `python:3.12-slim`，未固定 digest | 长期重建不保证字节/依赖一致 |
| 代码体量 | 收官后 `storage.py` 4153 行、`service.py` 2015 行、Godot `main.gd` 2245 行 | migration 7 进一步放大既有模块边界和审查成本；v3 应先 characterization 再串行拆分 |

### 3.2 数据库与 Docker

修改前已使用 SQLite online backup 生成：

`sandbox/tmp/code-audit-db-backup-20260822/projecttown.db`

- 大小：2,191,360 bytes；
- `PRAGMA integrity_check`：`ok`；
- `PRAGMA foreign_key_check`：0 条；
- migration：1、2、3、4、5、6。

最终 Docker Settings 实例以最新候选代码经重建后复验：

- `/health` 两次返回 `status=ok`、`version=1.0.0`；
- 容器状态 `healthy`；
- 用户 `10001:10001`；
- root filesystem 为 read-only；
- `cap_drop=ALL`；
- `no-new-privileges=true`；
- 端口只绑定 `127.0.0.1:8000`；
- provider settings 使用独立 `projecttown_local_settings` named volume；
- 未携带会话令牌访问 Settings API 返回 403；
- live SQLite 连续两轮 `integrity_check=ok`、外键违规 0、migration 1–7 checksum 精确匹配。

审查过程中第一次 SQLite 命令使用了不适用于当前容器的 `/data/projecttown.db`，随后依据实际 mount/env 修正为 `/app/data/projecttown.db`；错误命令不计入通过证据。

### 3.3 Python 静态检查与测试

| 检查 | 结果 |
|---|---|
| Ruff：`backend benchmark tests scripts` | exit 0 |
| compileall：`backend benchmark scripts tests` | exit 0 |
| `pip check` | `No broken requirements found` |
| 默认 Pytest，Terra 独立两轮 | 每轮 `605 passed, 12 skipped, 1 warning`，exit 0 |
| 默认 Pytest，Sol 独立复核 | `605 passed, 12 skipped, 1 warning`，exit 0 |
| 覆盖率门禁，独立两轮 | 每轮 `605 passed, 12 skipped`；总覆盖率 `92%`；`fail-under=80` 通过 |

唯一 warning 是现有 Starlette `TestClient`/httpx 弃用提示，不是本轮功能回归。

最终重复门禁的第一次尝试复用了一个已有 pytest basetemp，Windows SQLite 文件锁使清理阶段产生 `326 setup errors`；该轮没有被计入通过。保留失败目录并改用两个全新唯一目录后，Terra 两轮和 Sol 独立一轮均为 `605 passed, 12 skipped`。这属于测试临时目录生命周期限制，不得用失败重试覆盖或隐藏。

敏感字段防御的首次实现把合法的 `max_tokens` 等预算字段一并拒绝，导致首轮全量测试出现 `8 failed, 474 passed, 10 skipped`。Sol 未接受该版本；实现收窄为明确公共 token 计数字段后，两轮全量和覆盖率测试均恢复通过。这一失败记录保留为审查有效性的证据。

### 3.4 Godot 4.7.1 实机验证

引擎：`4.7.1.stable.official.a13da4feb`。

- 编辑器 headless 解析、主场景启动、独立 Uvicorn 和 API smoke：两轮均 exit 0；每轮 smoke Quest 均完成，21 个 events、4 个 evidence；
- 昼夜周期：两轮均输出 `TIME_CYCLE_SMOKE_OK anchors=6 midpoints=3`；
- Docker 上只读 Quest 恢复：两轮均输出 `RESTORE_SMOKE_OK`，恢复 19 个 events、4 个 evidence、1 个成果，并保持 provenance 字段；
- Windows 截图回归：两轮各捕获 7 个 fixture × 3 个 viewport = 21 张；每轮 21/21 通过，`changed_pixel_ratio=0`、`max_channel_error=0`。

报告保留在：

- `sandbox/tmp/v2-closeout-final-godot-20260822-visual-b/report.json`；
- `sandbox/tmp/v2-closeout-final-godot-20260822-visual-c/report.json`。

### 3.5 Benchmark、RAG 与 MCP

formal-v1.0 manifest：seed 1729、4320 rows、`runtime_simulation=true`。当前正式文件和两轮独立重生成均逐项匹配：

| 文件 | SHA-256 |
|---|---|
| `report.md` | `21ba42602336fd34015df50ef2f2bba24f90d0bf8645172c870c6d4418128f0b` |
| `results.csv` | `4f24fac7930945aaa7986e905cf5ac7f6393499fa8bd4caaa61162be6712d1c6` |
| `results.json` | `f8b5d380fc87a37f071b28f38d69988ad8938470da4dbbc3663583111a193d06` |
| `success.svg` | `5181d5fec3434925aa9ea9c85a688c5e169ce82065cb54a16c8509c9910af3d5` |

Phase 2 确定性 RAG 两轮成功输出逐字节一致：

- `report.md`：`5023bef80419c381d9e4cab43192225fb23d49f89da3bef8edb6d7a3a24a76c4`；
- `results.csv`：`b34f02a8e6b398e65b317ff35e11a47f43687ecf2d02a7d2a201c7bc7ab92bf4`；
- `results.json`：`4deb8715c45683fcfbe5608b64267a677476e3a1fb5892ecfdcbb6e4f2759bb1`；
- 两轮均声明 `provider_calls=0`、`embedding_calls=0`，不测真实模型延迟。

RAG runner 对越出 `sandbox/tmp/rag-evaluation/**` 的输出和预创建输出目录均以 exit 2 拒绝；这是安全约束生效，不是评测失败。

Focused 测试重复结果：

- Phase 2 Benchmark/RAG：两轮各 `10 passed`；
- Phase 3A MCP：两轮各 `12 passed`；
- Phase 3A 仍仅代表 fixture/local Adapter 合约，不代表真实 MCP server 已验收。

## 4. 已确认且保持成立的核心设计

1. `/api/v1` 兼容面与既有 `/api/v2` 语义仍存在；
2. Goal Contract 使用草稿与明确确认的两阶段流转；
3. Event ledger、state-version CAS、replay、Checkpoint 与恢复链路完整；
4. Gateway 有 allowlist、Sandbox、idempotency key、原子 receipt 和 unknown-effect 协调；
5. Verifier/Evidence 独立于 Agent 自报；
6. Watchdog、步骤/工具/消息/时间预算和受限 replan 已接入执行循环；
7. 启用成果审核时，preview、retain/discard、幂等回执和恢复均成立；
8. WebSocket 客户端按 at-least-once 处理，并按 `(quest_id, sequence)` 去重；
9. migration 1–6 未重写，当前 additive 最新版本为 7；
10. deterministic runtime simulation 与真实模型评测目录/报告分离；
11. late callback、Evidence/Quest ID、atomic Gateway receipt 等历史修复仍在测试覆盖中。

## 5. 缺陷登记与当前处置

### 5.1 本轮已解决并由 Sol 验收

| ID | 缺陷 | 修复 | 验收 |
|---|---|---|---|
| AUD-01 | 只读 `list_directory` 会创建不存在的 workspace，破坏“只读无副作用” | `Sandbox.resolve` 增加 `create_workspace`，目录列表使用 false，缺失目录返回空结果 | focused 13 passed；全量两轮通过 |
| AUD-02 | Quest 在线程池排队时提前写 `started_at` 并占有 lease，排队时间错误消耗预算；长调用无续租 | worker entry 原子 `ExecutionAdmitted` + unique owner nonce + TTL/3 heartbeat + owner-safe renew/release + lost-lease guards + scheduling/shutdown 事件化 | 专项 7 项连续三轮；focused 31 passed；全量/恢复两轮通过 |
| AUD-03 | 公共 model-call JSON 对凭据型 token 字段的防御不完整；粗糙修复又会误伤合法计数器 | 仅允许 8 个明确、非负整数 token counter；其他 token/secret 字段在 dispatch/persistence 前拒绝 | focused 62 passed；全量两轮通过 |
| AUD-04 | 真实模型评测报告无法区分新请求与幂等回放，容易误报“本轮调用” | OpenAI/Qwen runner 增加每项 `idempotent_replay` 和汇总 `dispatch_count`、`replay_count`、`execution_mode` | focused 24 passed；相同 DB 第二轮为 cached、0 dispatch |
| AUD-05 | Phase 1E fixture 数量和 Settings 模式重启说明漂移 | 文档修正为 7 fixture/21 screenshots；Quickstart 分离普通 Compose 与 Settings manager | focused 19 passed；当前 Godot 两轮 21/21 |
| AUD-18 | Git ignore 只覆盖 `.env` 和少数具体 secret 文件，未来初始化 Git 时可能误纳入 `.env.local` 或未知 `.secrets` 文件 | `.gitignore` 与 `.dockerignore` 都忽略 `.env.*` 和整个 `.secrets/`，并显式保留 `.env.example` | secret/release/settings 聚焦 `91 passed, 2 skipped`；源码扫描确认真实凭据 0 |

本轮关键代码位置：

- `backend/app/tools.py`：`Sandbox.resolve`、`_list_directory`；
- `backend/app/v1/storage.py`：`admit_execution`、`renew_lease`；
- `backend/app/v1/service.py`：`_start_worker`、`_execute_quest`、`_start_lease_heartbeat`；
- `backend/app/v1/model_runtime.py`：`PUBLIC_TOKEN_COUNTER_FIELDS`、`_validate_public_json`；
- `benchmark/real_model_evaluation/runner.py`、`qwen_runner.py`：回放/派发摘要。

执行租约修复的保证范围严格限定为单节点、单进程。heartbeat 不是多进程 fencing token，不能被描述为 HA 或多节点一致性保证。

### 5.2 高优先级、尚不能擅自修改

#### AUD-06：成果审核默认语义不满足目标不变量

证据：`backend/app/v1/models.py` 中 `QuestCreate.artifact_review_required` 默认值为 false；初始投影也默认 false。启用时的审核链路可靠，但默认 Quest 可直接完成。

风险：直接把 `/api/v2` 默认值改为 true 会改变既有客户端语义，并使无成果或自定义 workspace 的 Quest 行为发生变化。

方案：新增版本化行为模式，而不是静默翻转旧默认。需要用户决定：

- 新版是否所有有写入成果的 Quest 强制进入 `waiting_user`；
- 没有成果时是允许直接完成，还是以 `ARTIFACT_MANIFEST_EMPTY` 失败；
- 旧 `/api/v2` 是保留兼容，还是接受破坏性升级。

#### AUD-07：成果归属与 `diff_scope` 缺少独立观测（v2 已完成 shadow 层）

证据：`verifier.py::_diff_scope` 比较 criterion 中的 `allowed_paths` 与 `changed_paths`；`service.py::_freeze_artifacts` 对通过 verifier 的路径写入 `created_by_quest=True`。系统检查路径、哈希、文件类型和 Evidence，但没有持久化的 workspace baseline/diff observer 去独立证明“该文件确由本 Quest 创建或修改”。

风险：这弱化了“不能相信 Agent/调用方自报完成”的最后一段证明链，尤其是在复用 workspace 时。

收官处置：

1. migration 7 只新增四张不可变 side-ledger 表，migration 1–6 checksum 不变；
2. 首次 execution admission 后、工具前保存有 live owner/CAS/admission 绑定的受限 baseline；
3. `write_file` 的实际 before/after hash 与 receipt、`ToolCommitted` 原子提交；
4. final snapshot、manifest、provenance 与 `ArtifactReviewRequested` 原子提交并交叉绑定 Evidence/action/event；
5. 从 migration 0/4/5/6 旧库升级、重开、失败回滚、replay/checkpoint、retain/discard 和恢复均有回归；
6. 旧 action/no-baseline Quest 只记录 `legacy_unobserved`，数据库不允许 v2 provenance 声称 `verified`；
7. v2 仍为 compatibility shadow，不改变 completion 或 review 决策。由 observer 取代现有 `diff_scope`、强制 workspace ownership 和 fail-closed 属 v3。

#### AUD-08：20 元总预算不是跨实例、跨数据库的财务总闸

证据：OpenAI 与 Qwen 使用独立 SQLite cost account，每个账户各有 `max_total=20_000_000` micro-CNY。更换 evaluation DB 或供应商账户会形成新的预算域。

风险：当前实现能防止单个既定账户超支，但不能保证“用户所有真实模型调用合计永远不超过 20 元”。

方案：真实模型调用继续禁用，直到用户确认预算域。之后新增固定、共享、不可由 CLI 任意替换的 cost ledger，并明确：

- 20 元是一次开发阶段总额、自然月额度，还是手工 reset 周期；
- OpenAI 与 Qwen 是共享 20 元，还是各自 20 元；
- reset 权限与审计事件；
- provider 账单与本地 settlement 的对账策略。

#### AUD-09：Phase 3B 真实 MCP 的 TOCTOU 与 containment 未闭合

证据：MCP config 加载时计算 executable/argv file hash，随后 `_Session` 通过路径再次 `subprocess.Popen`；两者之间文件可被替换。当前有固定 argv/cwd/env、allowlist、超时和进程组清理，但没有不可替换句柄执行、Windows Job Object/容器边界或统一文件/网络隔离。

风险：真实 MCP server 比 fixture 多出任意本地代码执行和外部副作用面。

方案：Phase 3B 继续默认关闭。选定一个真实 server 后，为它建立固定 executable identity、只读镜像或受控副本、OS/容器级进程限制、文件 allowlist、网络策略、工具风险分级和模糊副作用恢复测试。不能只凭 Phase 3A 的 12 个测试宣称可上线。

### 5.3 中优先级工程债

| ID | 现状/风险 | 优化方案 |
|---|---|---|
| AUD-10 | telemetry exporter 超时后，任意不可取消 callable 的 daemon helper 可能继续存活；circuit 只限制每个 telemetry 实例最多遗留一个 | 要求 exporter cooperative cancellation，或改为可终止的独立进程；增加 close 后线程/进程枚举测试 |
| AUD-11 | `_env_bool` 对非法字符串静默返回 false，配置拼写错误不可见 | 改为严格枚举，非法值启动失败；先加兼容说明和启动测试 |
| AUD-12 | provider contract 有抽象，但注册、目标 allowlist、模型与价格仍是 OpenAI/Qwen 精确分支 | 引入 provider manifest/registry；密钥解析、adapter、pricing、outbound policy 分层；新增供应商仍需单独授权 |
| AUD-13 | Quest 历史已有搜索、多状态筛选、分页、失败摘要，但缺 archive/unarchive 和从失败原因跳转到事件/Evidence/恢复动作 | 先定义归档语义，再以 additive 字段/事件和 Godot UI 实现；不删除 ledger |
| AUD-14 | `storage.py`、`service.py`、Godot `main.gd` 过大，增加并发修改和审查风险 | 先用 characterization tests 固定行为，再按 storage repositories、execution coordinator、history/settings/artifact UI 分拆；禁止一次性重写 |
| AUD-15 | 依赖无 lock，Docker base 无 digest | 生成可审查 lock/constraints 与 SBOM；CI 做 clean install；固定镜像 digest并制定更新流程 |
| AUD-16 | Quest projection 的 `budget_usage.tokens` 初始化为 0，但执行循环只更新 step/tool/message/replan；真实模型 token 在独立 model attempt/cost ledger | API/UI 明确区分 Quest runtime budget 与 model cost usage，或建立只读聚合投影；不得把 0 展示为“真实模型没有消耗” |
| AUD-17 | 无 Git/许可证/tag/演示媒体 | 用户选择许可证后初始化 Git、形成签名/可审查 tag 和演示证据；不得擅自公开发布 |

## 6. 分阶段优化路线图

### Iteration 0：可信执行补洞（本轮完成）

范围：AUD-01 至 AUD-05。特点：不改公共 API schema、不新增 migration、不调用真实 provider、可按文件回滚。状态：已由 Sol 接受。

### Iteration 1：独立成果 provenance（v2 compatibility-shadow 已完成）

本轮在不选择模型供应商、许可证或多节点架构的前提下，只观察 Quest Sandbox workspace；不声明对外部项目目录或并发进程拥有所有权。

实际最小可回滚实现：

1. ADR、migration 7、bounded scanner、Gateway atomic observation 与 Service atomic review 已落地；
2. observer 只覆盖 Sandbox 内 regular files，不跟随 symlink/reparse point，并设置文件数/字节上限；
3. 旁路记录和 Godot 只读提示不改变 completion、retain/discard 或旧 manifest；
4. 旧数据库升级、崩溃恢复、replay/checkpoint 和成果审核回归纳入收官门禁；
5. 失败时可回退代码/UI 并保留 additive 数据；不得手工删除 migration 7；
6. fail-closed、workspace 独占和 observer 替换 `diff_scope` 延后到 v3 版本化行为模式。

### Iteration 2：版本化成果审核与历史归档

前置：用户决定 AUD-06 的兼容策略和无成果语义，并定义 archive/unarchive。

交付：版本化 Quest 创建模式、强制成果审核状态机、archive/unarchive 事件、搜索过滤、失败原因到事件/Evidence/恢复控制的导航、Godot 视觉 fixture。

### Iteration 3：统一 provider registry 与全局成本账本

前置：用户决定预算域、reset、供应商与模型；真实调用仍需明确授权。

交付：provider-neutral registry、统一连接配置 schema、共享成本账户、prompt/model parameter/version/token/cost summary 分层、无完整 Prompt/Response 持久化检查。确定性与真实评测继续分别报告。

### Iteration 4：真实 MCP Phase 3B

前置：用户指定第一个 MCP server、固定 executable/argv/cwd、允许的工具、文件范围、网络范围和风险等级。

交付：消除 hash-to-spawn TOCTOU 的部署方式、进程 containment、原子 tool receipt、unknown-effect 故障注入、关闭/超时/恢复和多轮实机测试。默认仍为 off。

### Iteration 5：可维护性与可重复发布

交付：模块分拆、strict config、telemetry cancellation、依赖 lock、镜像 digest、SBOM、Git/tag、许可证和演示媒体。认证、多用户、多节点另立架构阶段；不得直接把 SQLite 和进程内锁扩展到多节点。

## 7. 各子系统影响

| 子系统 | 本轮影响 | 下一高优先级影响 |
|---|---|---|
| API | 未改公开 schema；修复只改变内部执行 admission 和评测报告字段 | 成果审核必须版本化；历史归档需新增事件/API |
| 数据库 | migration 1–6 未改；migration 7 新增 immutable shadow provenance tables | Quest-bound RAG 只能使用 migration 8+；不得改写 1–7 |
| 恢复 | 排队不再耗时预算；lease loss、submit failure、shutdown 均有安全退出/事件化路径；baseline/final/observation 可恢复 | fail-closed ownership 与外部并发归责仍需版本化决策 |
| 成果审核 | 现有 opt-in retain/discard 继续通过；migration 7 shadow ledger 与 Godot 只读提示已落地 | 默认强制审核和强 ownership proof 尚待决策/实现 |
| 安全 | 补强敏感字段拒绝、只读工具无写副作用、owner-safe lease | MCP containment、strict env、telemetry cancellation 待做 |
| Benchmark | formal 哈希与两轮重生成一致；真实评测显示 fresh/cached/mixed | 全局成本域确定后才能进行付费实测 |
| Godot | 两轮解析/联调/恢复/昼夜与两轮 21 张零差异通过 | 新归档、失败导航、强制审核需新增 deterministic fixtures |

## 8. 需要用户决定的重大事项

在继续高风险改动前，只需要依次回答以下事项：

1. 成果审核：选择新增版本化强制模式，还是允许破坏现有 `/api/v2` 默认语义；无成果 Quest 是直接完成还是失败？
2. 预算：20 元是跨 OpenAI/Qwen 的整个开发阶段共享总额，还是按供应商/周期分别计算；谁可以 reset？
3. MCP：第一个真实 MCP server 是什么；固定 executable、argv、cwd、工具、文件与网络范围分别是什么？
4. 归档：archive 是仅从默认历史隐藏、禁止继续执行，还是两者同时；unarchive 是否需要二次确认？
5. 发布：许可证选择、是否初始化 Git、tag 名称和是否允许公开发布。

此外，曾经在会话中明文出现过的 provider API key 应在任何真实调用前撤销并轮换。当前审查没有读取或重复该密钥；Docker 中现存配置不能消除会话暴露风险。

## 9. Sol 验收结论

本轮 AUD-01 至 AUD-05、AUD-18 的代码与文档修复接受；AUD-07 的 compatibility-shadow 迭代也在严格“不声称 verified、不改变旧完成语义”的范围内接受。理由是：变更范围可界定；migration 1–6 未重写，migration 7 为 additive；公开 API 保持兼容；Terra 两轮全量和两轮覆盖率、Sol 独立 605 项全量、Docker/SQLite、Godot 实机、formal Benchmark、RAG 和 MCP focused 验证全部完成。

AUD-06、AUD-08、AUD-09，以及 AUD-07 从 shadow 切换为 fail-closed 的部分，不接受以“顺手修改”的方式推进。它们必须按上述决策或独立 migration/安全阶段实施。当前版本可继续作为单用户、本地、单节点、单进程开发和演示基线；不能宣称已经具备多用户、多节点、真实 MCP 任意代码隔离、强成果 ownership 或跨实例财务总预算保证。完整最终证据见 [`v2-closeout.md`](v2-closeout.md)。
