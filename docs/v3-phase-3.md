# ProjectTown v3 Phase 3A–3E：受控写入开发蓝图

> 状态：3A–3D `ENGINEERING_VERIFIED_ON_DISPOSABLE_FIXTURES`；正式 v2 Study 虽已 create-only
> 创建，但因 R1 contract 缺陷永久处于 `PHASE_3E_V2_STUDY_CREATED_PROTOCOL_HOLD`。additive v3
> records/CLI/manifest 已通过工程终验，状态为
> `PHASE_3E_V3_POLICY_DEPRECATED_PROTOCOL_HOLD`。additive v4 改为两轮 Participant 实例测试加独立
> 非真人 engineering acceptance；v4 Study→R1/R2→Summary→User RC `ACCEPT` 已完成，当前状态为
> `hold_for_version_gate`。3D 仅完成 native loopback、预授权单目标操作的首个纵切，默认关闭；没有写入
> 任何真实用户目标。真实目标仍为 `BLOCKED_PENDING_PER_TARGET_USER_AUTHORIZATION`；工程验证、User RC、
> VERSION、Git、Apply/Publish 与 Release/Distribution 均为独立门禁。
>
> 权威性：本文细化 [`v3-product-direction.md`](v3-product-direction.md) 的 Phase 3 路线。
> 现有 v1/v2 兼容合同、Phase 0–2 历史事实和冻结 Study 记录不因本文改变。

## 1. 当前事实与总流程

Phase 0 已完成；Phase 1 工程闭环已通过，但真人价值证据只覆盖已记录的有限
plan/PDF 场景；Phase 2 已按两轮、跨 profile 的范围受限门槛收官。Phase 3A 已建立并
验证**只读** README ApplyPlan/preflight；Phase 3B 已建立并验证外部、create-only 的完整
post-image proposal。Phase 3C 已建立独立 controlled-write 模块/CLI、逐操作授权、外部
append-only ledger/backup、同目录 staged replace、reconcile 和独立授权 restore。它仅在
disposable fixtures 上执行过，没有修改真实用户文件。Phase 3D 又以 additive、默认关闭的
`/api/v3` 与静态 Web UI 包装这条预授权操作路径；它不能创建 authorization，也不接受浏览器路径。

当前 `ApplyPlan` 绑定 fresh、无冲突 Result 的 bytes/session/artifact/preview 与 README
目标的相对路径、SHA-256、大小、device、inode。其 artifact 只是人类可读建议；它不是
可执行 patch、完整 post-image 或写入授权。`publish_new_file` 也只是外部 create-only
发布，不能被重新解释为覆盖目标的 CAS。

后续严格顺序如下；任一箭头都需要前一阶段 exit gate 和本蓝图注明的用户授权：

```text
3A read-only preflight [verified]
  → 3B versioned executable proposal [verified, still no write]
  → 3C controlled-write core [verified on disposable fixtures]
  → exact per-target UserAuthorization [required for any actual target]
  → one-target Apply / reconcile / separately authorized restore
  → explicit user authorization to unfreeze the UI charter [granted for development]
  → 3D pre-authorized loopback Web UI/API [verified on disposable fixtures]
  → 3E two-round release-candidate human study
  → User RC Acceptance Gate
  → separate VERSION Gate
  → separate Distribution Gate for Git tag/public release
```

本文后续统一将“创建新的下载/导出产物”称为 **Export**，将 **Apply** 专指“修改用户明确选择的
既有目标”。Export、Apply、restore、RC acceptance 和 Distribution 各自需要自己的合同或授权，
不得互相推导。

Phase 4A/4B 后续已作为默认关闭的只读 Workbench 与离线 authoring/export 工程切片获批并实现；
其状态不反向修改本 Phase 3 合同。Phase 4C-4E、provider、embedding、external MCP、
network/egress、付费调用、数据库迁移、直接 Apply、restore 和公开发布权限仍受独立门禁。

## 2. 跨阶段不变量

| 领域 | 不变量 |
| --- | --- |
| 用户控制 | User 独占目标选择、每目标写入授权、Accept/Retain/Discard、Apply/Publish、restore 与 Release Gate；Verifier、Reviewer、CLI 和 UI 不得自动跨越这些门。 |
| 资料与路径 | 只接受显式选择、canonical、安全的本地范围；重解析点、越界、非普通文件、stale、conflict、lineage/provenance mismatch 或未知状态均失败关闭。 |
| 证据 | 历史证据只读；新工程证据使用新的外部 create-only 根；新结果不得冒充历史事实。每个确定性核心在两个 fresh evidence roots 运行，且覆盖正向、负向、恢复/中断。 |
| 写入语义 | preview/建议、可执行 proposal、授权、实际写入、post-write observation、receipt、Study disposition 和 release authorization 是不同记录和状态。 |
| 兼容性 | 不改写 `/api/v1`、既有 `/api/v2`、migration 1–7、Quest、Godot、旧 records 或其 canonical bytes/hash。任何协议/记录新增均 additive、版本化且 fail-closed。 |
| 离线 | 默认离线；provider、embedding、外部 MCP、network/egress、paid API 调用数均为 0。Phase 3 工具、代理及其工作流不得自行打开、扫描或回显 secrets、`.env*`、Authorization header 或私有 endpoint。 |

上述 `.env*` 限制不改变既有 v1/v2 兼容启动合同：受支持的应用 launcher 可以继续把用户显式配置的 `.env` 作为全局应用配置载入进程环境。该兼容行为不授权 Phase 3 工具或代理读取 `.env*` 文件，也不授权 provider、外部 MCP、网络、Apply 或 Publish；Phase 3 代码只能消费其既有显式配置项，并继续遵守默认关闭与失败关闭边界。

## 3. Phase 3A：已验证的只读基线

### 当前产物与非目标

现有 `backend/app/controlled_apply.py` 的 `prepare`/`check` 与其 CLI 仅生成或重新核验
外部、create-only ApplyPlan。它重新观察 Result 与目标；目标 identity/content 漂移、计划
篡改、不安全文件形态、stale source 或 Result conflict 均会阻断。

3A 不包含 target write、backup、replace、restore、executable proposal、per-target
authorization、receipt、`/api/v3`、Web UI 或 migration。它保留为 3B 的只读输入合同，
不得在原 ApplyPlan 中静默塞入写入语义。

### 3A gate 与回退

3A 的工程事实不等于 Apply 授权。若后续阶段失败，保留 3A 的只读入口即可回退；不删除
目标、历史计划或 Study 证据。

## 4. Phase 3B：已验证的字节级写入提案（仍不写目标）

### 入口、目标与非目标

入口是 3A 的 fresh、conflict-free preflight 通过。实现产生一个可独立核验的、
additive/versioned `ExecutableProposal`，供 3C 在获得新授权后使用。3B 本身不执行 Apply、
backup、restore、API/UI 或数据库迁移，所有成功状态均保持 `write_performed=false`。

现有 Result 的 `artifact_markdown` 仍只是人类可读建议，绝不能被当作写入真值。3B 的独立
producer 从已验证 Result 的结构化 task、citations 和 constraints 确定性构造 managed section；
proposal 内的完整 post-image bytes 才是唯一未来执行真值，display diff 只可由 before/post-image
重新派生，不能反向解析为执行输入。

### 已实现的协议、producer 与入口

1. record schema：`v3-material-executable-proposal-v1`；hash domain：
   `projecttown/v3/material-executable-proposal/v1`；producer：
   `projecttown-readme-append-composer-v1`。
2. `backend/app/executable_proposal.py` 提供 strict canonical serialize/parse/load/create/verify；
   `scripts/run_v3_executable_proposal.py` 只暴露 `create` 与 `check`，输出不回显完整 post-image
   或绝对路径，且离线调用计数为零。
3. proposal 在单一 canonical JSON 中保存严格 canonical Base64 complete post-image，并绑定 3A
   plan bytes/hash、Result session/bytes/artifact/preview、target-before hash/size/device/inode、
   selected/proposed scope、append offset、appended/post-image/diff hash 与 deferred gates。
4. 原目标 bytes 必须是 post-image 的逐字前缀。首版只允许受控 append，保留一个 leading UTF-8
   BOM、拒绝 internal BOM/control/bidi，纯 CRLF 继承 CRLF，纯 LF/无 newline 使用 LF，mixed/
   bare CR 失败关闭；原 bytes 不规范化，生成 section 使用 NFC，并有唯一 managed marker pair。
5. proposal 只可发布到 material root 外的安全、普通、非 reparse、single-link create-only 文件。
   stale/conflict、target/plan/Result/source drift、binding/hash/scope mismatch、unsafe path、marker
   冲突、非 canonical JSON/Base64 或 size 超限均失败关闭。

### 验证、exit gate 与回退

工程验证覆盖 proposal 正向 create/load/check、canonical parse/hash/Base64、before/after/diff、
BOM/newline/Unicode/末尾换行/大小边界、篡改与重算 hash、非法 scope、target/plan/Result/source
drift、stale/conflict、unsafe path、create-only collision、publication rollback/attention，以及旧 3A
兼容；确定性核心在两个 fresh evidence roots 重复。该 exit gate 只把 3B 推进到“可请求 3C
授权”，不产生授权、backup、目标写入、receipt、restore 或用户接受。

回退是禁用 3B producer/CLI 入口并保留 create-only records；绝不改写 3A 或删除证据。

## 5. Phase 3C：单目标受控 Apply（内核工程验证；真实目标仍阻断）

### 授权与威胁模型

用户已授权 3C 代码与新建 disposable fixture 的开发/验证，但未指定任何真实写入目标。
每个真实目标都必须通过新的、精确绑定 proposal/target/before/after/ledger/backup/receipt/nonce 的
`UserAuthorization`；布尔开关或泛化的 `allow_apply=true` 不构成授权。首版仅限单个普通
UTF-8 README 目标，不做批量写入、Publish 或自动 Apply。

安全声明限于可信同用户、本地单 writer：operation lock、二次观察、same-directory temp 与
atomic replace 只能降低同一受控进程内的 TOCTOU，**不是**对非合作进程的严格 CAS。检测到
external drift 必须 `EXTERNAL_DRIFT_BLOCKED`。

### 计划记录、状态与执行包

不得使用一个可变单 JSON journal。3C 使用严格 canonical、extra-forbid、独立 hash domain 的
append-only/create-only 外部 operation ledger：`UserAuthorization`、`PreflightObservation`、
`PreIntentRecovery`、`BackupManifest` 及 backup bytes、`ExecutionIntent`、`DispatchStarted`、
`PostWriteObservation`、`AttentionRecord` 和 `WriteReceipt`。restore 另需独立
`RestoreAuthorization`；其 receipt 复用同一严格 schema，但 action 与链不同。

`UserAuthorization` 必须逐操作绑定 operation ID、proposal hash、target canonical path、
before identity/hash、after hash、允许动作和 single-use nonce；泛化的 `allow_apply=true` 不构成
授权。ledger 事件须以 operation ID、严格递增 sequence、前序 event hash 与独立 hash domain
形成可重放链；记录一旦发布不得原地改写。

授权、preflight、recovery、manifest、dispatch、post 和 receipt 都绑定 Python 可观察的
`stat.S_IMODE` permission bits。该合同不声称能认证 Windows effective ACL、owner/group、
xattr、ADS 或 timestamp；如果后续需要这些元数据，必须建立新的版本化协议。

最少状态是 `PROPOSED`、`AUTHORIZED`、`PREFLIGHT_PASSED`、`BACKUP_READY`、`DISPATCHING`、
`OBSERVED`、`VERIFIED`、`COMMITTED`、`FAILED_NO_EFFECT`、`ATTENTION_RECONCILE`、
`EFFECT_PRESENT_ATTENTION`、`EXTERNAL_DRIFT_BLOCKED`。未知 commit outcome 不得报成功，
也不得盲目自动 restore。

实现顺序为：核验 proposal/authorization/target → 在 material root 外创建并 hash/fsync backup
（create-only、non-reparse、single-link、受限存储权限）→ create-only `ExecutionIntent` → 同目录写入并 fsync 临时
post-image → 在 lock 下二次核验目标 → create-only `DispatchStarted` → atomic replace →
尽力 fsync 目录 → post-write reopen、hash、scope/diff observation → create-only receipt。任何
interrupt/unknown outcome 转入 attention
并通过 reconcile 决定下一步；restore 必须独立授权，且 current target 仍匹配 expected-after。

首个 `ExecutionIntent` 之前的 attention 只能重试原 apply/restore 命令；重试会重新核验全部
inputs/target/parent/mode/backup 并 append `PreIntentRecovery`。一旦 intent 存在，apply/restore 不得
再次 dispatch，只能 reconcile：exact desired 收敛为 `COMMITTED`，exact before 收敛为
`FAILED_NO_EFFECT`，第三状态或不可读状态保持 attention。

### 验证、exit gate 与回退

已完成验证：缺失/错绑 authorization、proposal/target/bytes/mode drift、目录替换、锁竞争、
temp/replace/post-observation/receipt 各时点中断、backup 完整性、reconcile、幂等、已知安全
restore、unknown outcome attention 和 post-write reopen/hash/diff。每种可恢复失败必须有一次
恢复后复跑，确定性路径使用两个 fresh roots。

3C fixture-only 工程 gate 已通过：修订后的两个 fresh roots 分别通过 34 项聚焦测试，
3A–3C 相关回归为 95 passed / 3 skipped，全量为 1133 passed / 17 skipped，coverage
84.65%。有效双根是 `projecttown-phase3c-final2-20260831-b` 与 `-c`；首个 `-a` 因
pytest basetemp 父目录命令错误产生 34 个 setup errors，已保留且不计入通过。

这只证明受控写入内核在 disposable fixtures 上的工程合同。任何真实目标仍需新的
逐目标授权与写后用户确认。回退优先停止入口、保留 ledger/backup/receipt；仅在
独立 `RestoreAuthorization` 及 expected-after 匹配时执行恢复，禁止自动覆盖未知状态。

## 6. Phase 3D：loopback Web UI/API（首个纵切已工程验证）

### 入口、目标与非目标

用户已明确授权推进 3D。已实现的首版是 additive、feature-flag 默认关闭、native
`127.0.0.1` only 的 `/api/v3` adapter 与轻量静态 HTML/CSS/ES modules；REST 是权威状态，
UI 不直接写文件。records 位于显式、外部 work root，不引入 migration 8 或 Node 生产依赖。
Docker/container 启用仍未授权。

3D 不改变 `/api/v1`、既有 `/api/v2`、migration 1–7、Quest 或 Godot；Godot 仍是可选演示视图，
不是 v3 的必经路径。

### 已实现接口、记录与安全边界

首版故意收窄为**预授权操作 adapter**：独立 CLI 只能在验证既有 3C `UserAuthorization` 或
`RestoreAuthorization` 后，以 create-only 方式发布 opaque operation binding；浏览器不能创建
authorization、提交任意路径或控制测试注入参数。API 提供 session、binding 列表、operation
inspection/check，以及 apply/reconcile/restore；实际参数从已验证 authorization 派生。所有 mutation
使用 POST、CSRF 与 persistent idempotency intent/result，GET 无业务副作用；intent-only 或 result
发布不确定状态拒绝自动重派并进入 attention。API 只调用 3C 的 `load_record`、`check`、`apply`、
`reconcile` 与 `restore`，不复制 ledger 或路径守卫。

记录版本为 `v3-loopback-operation-binding-v1`、`v3-loopback-idempotency-intent-v1` 与
`v3-loopback-idempotency-result-v1`，各有独立 hash domain、canonical bytes、extra-forbid 与
create-only 验证。idempotency 记录上限固定为 512，自动清理不在本阶段；记录保留期限仍待用户决定。

安全包已包括 exact loopback peer、Host/Origin、forwarded-header 拒绝、memory-only bounded session、
HttpOnly/SameSite=Strict cookie、CSRF、JSON/body/idempotency bounds、CSP、canonical safe-path、opaque
IDs、无 wildcard CORS、no-store 与最小错误披露。UI 只显示相对目标与 path hash，并要求逐动作精确
确认；加载、刷新或创建 session 都不会 dispatch。进程重启使 session 失效，浏览器端不保存 token。

更广的资料/任务选择、Result/preview/citation 浏览、authorization 创建、Docker 暴露、WebSocket、
数据库持久化和真实目标操作没有随首版解冻，不能从“3D 已工程验证”推导。

### 验证、exit gate 与回退

工程验证已覆盖 API contract/compatibility、v1/v2 replay、migration 1–7 回归、loopback/Host/
Origin/CSRF/CSP/path/idempotency、GET 无业务副作用、binding/authorization tamper、apply/
attention/reconcile/独立 restore、并发不同 key 至多一次 replace、session expiry/restart、静态 UI
no-auto-dispatch/accessibility 合同和离线零调用。确定性跨阶段回归在两个新的外部 roots 各完成
`179 passed / 3 skipped`；实际浏览器只读交互用于补充视觉与按钮状态检查，不构成真人接受。

回退是撤出 `/api/v3` 路由和静态入口、保留外部 records；不写 migration 8，也不改 v1/v2。

## 7. Phase 3E：最终两轮 release-candidate 实例验收（v2/v3 HOLD；v4 current closeout）

3E 只在 3B–3D 稳定后进行一次最终**两轮** Study，而不是每一个子阶段一轮：

| 轮次 | 固定场景 | 必须观察的边界 |
| --- | --- | --- |
| Round 1 | disposable README fixture | proposal → preview → 明确授权 → Apply → post-check，并覆盖受控 reconcile/restore；不得使用真实用户重要文件。 |
| Round 2 | Report | 资料选择 → preview/citation → export；无 Apply。 |

T001/T002 plan 的既有证据只提供历史、范围受限背景，不能替代这两轮。Study、Trial、Result、
PDF/导出与 Summary 必须在新的、唯一、create-only Study/work roots 中产生；参与者亲自提供
disposition、时间、notes、evidence path 和 actions。工程检查、代理视觉检查或 Study PASS 不等于
User acceptance。

3E exit gate 至少要求两轮都能复核其 frozen binding、citation 可用、无 structural rewrite、无
blocking safety/recovery defect，并取得用户对 release-candidate 的明确接受。P0 已固定为同一用户
两轮、participant_count=1、control_rating>=4/5、backup/ledger/receipt 保留到明确授权清理、
create-only canonical JSON + SHA-256 release evidence，以及隐私最小化 identity labels。

失败或 REVISE 时，形成新的 versioned candidate 并回到适当工程阶段；不得改写原 Study。回退只
保留历史证据和只读/导出能力，不自动卸载或删除用户 records。

工程协议的 schemas、hash domains、canonical binding、status projection、P0 和回退规则见
[`v3-phase-3e.md`](v3-phase-3e.md)。v2 历史现场只能从
[`v3-phase-3e-study-handoff.md`](v3-phase-3e-study-handoff.md) 只读复核；新的 v3 真人会话只能使用
[`v3-phase-3e-v3-study-handoff.md`](v3-phase-3e-v3-study-handoff.md)，且该文件仍是不可执行模板。
CLI
`scripts/run_v3_phase3e_release_candidate.py` 仅创建/核验 records，不提供 Apply、restore、
VERSION 或 Publish 子命令。

### 2026-09-01 v4 canonical closeout

v4 Study `projecttown-v3-phase3e-rc-v4-20260901-002` 已以 create-only records 完成：同一
`participant-user-01` 的 R1/R2 均为 `retained`、`control_rating=4`、`citation_usable=true`、
`structural_rewrite=false`；两轮独立 `EngineeringAcceptanceV4` 均为 `PASS`，Summary 精确为
`criteria_met_awaiting_user_rc_acceptance`，随后 User RC 为 `ACCEPT` / `rc_accepted_pending_version_gate`。
Study/R1/R2/Summary/User RC 的精确 record/file hash 与只读 check/status 证据见
[`v3-phase-0-4-acceptance-2026-09-01.md`](v3-phase-0-4-acceptance-2026-09-01.md)。当前
`hold_for_version_gate` 不授权 VERSION、Git、Apply、Restore、Publish 或 Distribution。

## 8. 跨阶段 gate、权限与版本策略

| Gate / 状态 | 可由谁推进 | 必需条件 | 不代表 |
| --- | --- | --- |
| 3A preflight verified | 工程验证器 | fresh Result、目标 identity/hash、无 conflict | proposal、授权或写入许可 |
| 3B proposal verified | 工程验证器 | canonical complete post-image、before/after/scope/hash 通过 | 3C authorization |
| 3C core verified on disposable fixtures | 工程验证器 | 双 fresh roots、正负/中断/恢复、receipt/check、全量兼容 | 任何真实目标授权、真人接受或 3D 解冻 |
| 3C authorized / committed | User / 受控执行器 | 明确 per-target authorization；receipt/post-check | Study PASS、Publish 或 Release |
| 3D pre-authorized UI verified | 工程验证器 | 默认关闭；双 fresh roots；API/UI 安全、幂等、恢复与兼容通过 | 真实目标授权、更广任务/资料 UI、Docker 或全局 API 升级 |
| 3E v4 instance criteria met | Participant + Sol engineering acceptance | 同一 Participant 完成两轮实例评价；Sol 另行验证技术证据且不得冒充真人 | User RC acceptance、VERSION/tag/公开发布 |
| User RC Acceptance Gate | User | 阅读 Study 与工程证据后明确接受候选 | 修改 `VERSION`、创建 tag 或公开发布 |
| VERSION Gate | User | 确认版本语义、兼容清单与 release checklist | Git tag、安装包或公开分发授权 |
| Distribution Gate | User | 明确授权 Git tag/安装包/指定公开渠道 | Phase 4 解冻或自动 Publish |

版本策略：3B/3C/3D 的 records、hash domains、CLI/API/profile/manifest 仅能 additive、明确命名
并注册；旧 3A、v1/v2 records 保持原 bytes/hash。任何候选 bytes、生成器、展示或协议变化都产生
新的 lineage，不能复用已冻结 identity。`VERSION=3.0.0` 决定与 Git tag/安装包/公开发布授权是
两个后置且独立的 User Gates，不是 3E 或 RC acceptance 的自动结果。

## 9. 预期变更面、P0 开放项与 Phase 4

3B 已新增独立 proposal 模块/CLI、版本化 record/serializer/verifier 与聚焦 unit/integration/
recovery tests。3C 已新增 `backend/app/controlled_write.py`、`scripts/run_v3_controlled_write.py`
及相应 tests。3D 新增独立 loopback records/service/API、binding CLI 与 repository-static UI，
并只在 feature flag 打开时注册；这些阶段复用既有 safe-file 与外部 create-only 原语，没有新增
生产依赖，也未修改 database/migration、Quest 或 Godot。其后新增的 Phase 4A/4B 是独立、默认关闭
的 Local Workspace Task 工程切片，不改变 3A-3E records 或授权语义。

任何真实 3C 操作前仍需回答的 P0 项：精确 README target 与 proposal；逐操作授权的真实
身份/确认形式；ledger/backup/receipt 的保留与清理策略；可接受的合作式单 writer 威胁
模型；reconcile 与独立 restore 的责任人。external records 当前按用户决定保留到明确清理授权；
container 模式仍需另行审查。v3 已冻结为历史 policy hold；v4 保留两轮同一 Participant 实例测试，
不再要求独立真人 Reviewer，且 Participant evidence、Sol engineering acceptance、Summary 与 User RC 已
形成 canonical chain；Release Gate 前仍须确认 version/tag/license/公开范围，且尚未获得 VERSION、Git 或
公开分发授权。

Phase 4A/4B 已形成并工程验收；权威路线见 [`v3-phase-4.md`](v3-phase-4.md)。4C 只引用既有
Phase 3E 人类门禁，4D controlled-write handoff 尚未授权/实现，provider、MCP、扩展 RAG、多代理、
scheduler、benchmark 等 4E 能力保持逐项冻结。
