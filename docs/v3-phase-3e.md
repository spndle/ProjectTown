# ProjectTown v3 Phase 3E：Release Candidate 两轮验收协议

> **版本状态（2026-09-01）：** v2 与既有 v3 Study 都是只读 **Protocol HOLD** 历史；不得再用
> v3 创建 Round、Summary 或 User RC。additive v4 participant-instance 协议已完成 canonical
> Study→R1/R2→Summary→User RC `ACCEPT` chain；当前 status 为 `hold_for_version_gate`。本文不创建
> 新 Study/Round，不授权真实目标写入，也不构成 VERSION、Git tag、Apply、Restore、Publish 或
> Distribution 授权。
>
> **v4 policy overlay：** v1–v3 的 Independent Study Reviewer 合同只作为历史语义保留。
> 新建流程使用 additive v4：R1/R2 都由同一 Participant 完成真人实例评价；不再设置独立真人
> Reviewer。Sol engineering acceptance 使用独立 schema/hash domain，属于非真人技术门禁；User RC
> 仍是后续独立决定。既有 v3 canonical bytes 不改写。

## 1. 开发目标

Phase 3E 把已工程验证的 3A–3D 组合成一个可复核的 release-candidate 接受协议，回答：

1. 参与者能否在 disposable README fixture 上理解 proposal、preview、明确授权、Apply、post-check、
   reconcile/restore 的边界；
2. 参与者能否从显式资料生成 Report，核对 preview/citation 并导出，且不发生 Apply；
3. v1–v3 的 Independent Study Reviewer 能否满足历史协议；v4 则由 Participant 完成实例评价，
   并由 Sol 另行记录非真人 engineering acceptance；
4. User 能否在工程结果与两轮证据之后独立给出 RC disposition；
5. 系统能否保持 Participant instance outcome、engineering acceptance、User RC、VERSION 与
   Distribution 串行且互不替代。

3E 不开发通用 Agent 平台、完整 Web 任务/资料选择器、真实 provider/MCP、多 Agent、调度、远端执行、
migration 8 或真实用户重要文件写入。

## 2. 固定两轮

| Round | 场景 | 必须绑定 | 禁止 |
| --- | --- | --- | --- |
| `R1-CONTROLLED-APPLY` | 外部 disposable README fixture | material Result、ApplyPlan、complete post-image proposal、exact apply authorization、ledger、apply backup/receipt、reconcile observation，以及独立 restore authorization、restore backup/receipt 和 restore observation；loopback binding 仅在实际使用 3D 时绑定 | 真实用户重要文件、泛化授权、自动 Apply/restore、用任意 JSON 冒充 canonical receipt、用新证据覆盖历史事实 |
| `R2-REPORT-EXPORT` | 显式本地资料→Report | material manifest、Result、preview、citations、export bytes 与参与者实际 evidence path | controlled-write/Apply evidence、无引用结论、把工程导出冒充参与者所见冻结对象 |

两轮都使用新的、唯一、外部、create-only Study/work roots。工程 fixture 不能使用拟议真人路径，也不能
计入真人结果。

## 3. Additive records 与版本

| Record | Schema | Hash domain | 语义 |
| --- | --- | --- | --- |
| Study | `v3-phase3e-study-v1` | `projecttown/v3/phase3e-study/v1` | 绑定 manifest、candidate lineage、roots、两轮及用户确认的 P0 policy |
| Round | `v3-phase3e-round-v1` | `projecttown/v3/phase3e-round/v1` | 按 round kind 绑定现有 canonical records、参与者与 Reviewer 证据 |
| Summary | `v3-phase3e-summary-v1` | `projecttown/v3/phase3e-summary/v1` | 重新校验两轮并只导出工程/criteria/User RC 前 gate |
| User RC decision | `v3-phase3e-user-rc-decision-v1` | `projecttown/v3/phase3e-user-rc-decision/v1` | 仅 User disposition；不能授权 VERSION/tag/distribution |

Candidate profile 为 `projecttown-phase3e-rc-v1`；procedure manifest 为
`examples/v3-phase-3/projecttown-phase3e-manifest-v1.json`。旧 usability、Phase 2 closeout、3A–3D
records 与 canonical bytes/hash 不变。

Summary gate 只能为：

- `engineering_only`
- `criteria_not_met`
- `criteria_met_awaiting_user_rc_acceptance`

User 接受后的最高状态只能为 `rc_accepted_pending_version_gate`。VERSION 与 Distribution 仍需新的显式授权。

### v2 Round 2 provenance binding

v1 records remain immutable and parse under their original schemas/domains.  v2 adds
`projecttown-phase3e-rc-v2`, `v3-phase3e-study-v2`, `v3-phase3e-round-v2`,
`v3-phase3e-summary-v2`, `v3-phase3e-user-rc-decision-v2`, and the create-only
`v3-phase3e-source-set-v1` record.  Its procedure manifest is
`examples/v3-phase-3/projecttown-phase3e-manifest-v2.json`.

The v2 Study binds an external canonical material source root, the exact fixed
Report task, four selected source paths, and a source-set manifest under the work
root.  The source-set entry hashes and root hash are re-read at check time.  A v2
R2 Result must embed the same task and material-manifest entries/root hash; source
drift, source-set tampering, an embedded Result mismatch, root overlap, or any
v1/v2 profile/schema mix fails closed.  Material Result/preview/citation/PDF and
human evidence remain under the work root.

### v3 R1 canonical controlled-write binding

v3 新增而不改写 v1/v2：

- profile：`projecttown-phase3e-rc-v3`；
- manifest：`examples/v3-phase-3/projecttown-phase3e-manifest-v3.json`；
- procedure：`phase3e-release-candidate-v3`；
- schemas：`v3-phase3e-study-v3`、`v3-phase3e-round-v3`、
  `v3-phase3e-summary-v3`、`v3-phase3e-user-rc-decision-v3`；
- 独立 hash domains：`projecttown/v3/phase3e-{study,round,summary,user-rc-decision}/v3`。

Study create 只接受 R1 material root、source paths、exact task、canonical constraints、target、完整
expected post-image 文件、restore executor label 与 identity labels；hash、size、permission mode 和
relative path 均由安全文件读取派生。R1/R2 material roots 必须互不重叠，expected post-image 必须位于
work root，且不能是 target 或 source。未知或混配 profile/schema 一律失败关闭。

最终 R1 只接受现有 controlled-write canonical Apply + Restore 两条完整证据链。Apply 历史链必须合法，
restore 后 Apply live check 精确为 `TARGET_CHANGED_AFTER_RECEIPT`；Restore chain/check 必须为
`COMMITTED`，并将 disposable target 恢复到 Study 绑定的 initial hash、size 与 permission mode。
空 JSON、自由文本 observation、Apply-only 中间态或 transcript 声明不能升级为最终 R1。

## 4. Workspace status projection

3E 提供一个只读 projection，重新读取现有 records 后返回：

- 已存在并通过校验的 Study/Round/Summary/User decision；
- 当前 gate；
- 明确 blocker；
- 下一项可执行动作；
- 离线调用观察。

Projection 不是新的 source of truth，不写数据库或 mutable state，不创建 authorization，不执行
Apply/reconcile/restore，不推断参与者答案，也不自动推进任何 gate。

参与者、Reviewer 和 User RC evidence 都绑定 canonical absolute path 与 SHA-256；Round 1 的 apply/restore
records 由现有 parser 重新载入并交叉核验。`binding_status=verified` 不能由调用者自行宣称。显式
`stale`、`conflict` 或 `missing` 可以作为真实失败记录被校验，但不能升级为 verified。

## 5. 工程与真人边界

### Engineering ready

- 独立 schema/hash domain、canonical bytes、严格 parser/verifier 与 create-only publication 完成；
- 两轮真实工程链在两个 fresh disposable roots 成功执行；
- positive、negative、tamper、stale/conflict、publication recovery、wrong-round、mixed-lineage、
  missing-evidence 与 premature-decision 覆盖；
- Round 2 明确证明无 Apply；
- status projection 无副作用；
- `participant_count` 由两轮 self-reported participant identity 的去重数核验；Reviewer identity 与同轮
  participant identity 相同会阻断 Summary。该标识只证明记录一致性，不是认证身份；
- v1/v2、Phase 2、3A–3D bytes/行为兼容；
- provider、embedding、external MCP、network/egress、paid calls 为 0。

满足这些条件时的历史工程里程碑只可称：

`PHASE_3E_ENGINEERING_READY_AWAITING_P0_AND_HUMAN_STUDY`

截至 2026-09-01，P0 policy 与 additive v3 的三项协议选择已由用户确认，v2 Study 已在外部
create-only root 创建并通过只读 `check/status`；该历史文件及其 hash 保持不变。v2 的准确状态仍为
`PHASE_3E_V2_STUDY_CREATED_PROTOCOL_HOLD`。现有 v3 Study 因 Reviewer policy 被替换而冻结为
`POLICY_DEPRECATED_PROTOCOL_HOLD`；它仍可 parse/check/status，但不得新增 Round、Summary 或 User RC。
additive v4 承载新的 participant-instance 合同。以下“创建前”表述仅记录当时的协议前置状态；现行
v4 Study 已在独立 roots create-only 创建并完成。它可称为本协议范围内的 User RC `ACCEPT`，但不得把
工程通过、Participant `RETAIN` 或 User RC 解释为启动、VERSION、Git、真实目标操作或发布授权。

### v4 current closeout

v4 已由用户授权全新 Study/work roots，并由同一名 Participant 本人完成 R1/R2 两个实例测试，逐轮提供
disposition、elapsed、actions、notes、timestamp、control rating、citation usable 与 structural rewrite。
Sol 的另行哈希非真人 engineering acceptance 没有替代 Participant；User 随后单独给出 RC disposition。
工程代理不得代填、改写或继承历史评分。

## 6. 启动前 P0 policy

真人 Study 创建前必须固定；以下项目已在 2026-08-31 的 v2 Study 创建前由用户确认：

1. 最低 `control_rating`；
2. participant 数量与两轮角色安排；
3. disposable Round 1 backup/ledger/receipt 的保留策略与责任人；
4. User 在 RC Gate 阅读的 release evidence 格式；
5. 隐私最小化的 participant identity 与 engineering verifier label；
6. Round 1 restore 由 participant、Verifier 或两者执行；
7. 唯一 Study/work roots、精确 fixture target 和每次 authorization。

该 Study 的已确认值为：同一参与者完成两轮（`participant_count=1`）；`control_rating >= 4/5`；
backup/ledger/receipt 保留到明确授权清理；release evidence 使用 create-only canonical JSON 与 SHA-256
清单；v4 Participant 与 engineering verifier 使用隐私最小化 label，且 verifier 不作为真人 Reviewer；
Round 1 使用 disposable README target，
restore 执行者及 apply/restore 每次操作均需独立明确授权；Round 2 绑定显式 material source root 与固定
Report 任务。

未确认任一 mandatory P0 时，Study create 必须失败关闭。上述确认只适用于当前已创建 Study，不是对
任何 Apply、restore、Round、Summary、User RC、VERSION 或 Distribution 的授权。

### v3 的三项用户决定

v2 的已确认 P0 不足以覆盖可执行 R1。用户于 2026-09-01 同意以下三项推荐方案：

1. R1 使用 disposable README target；exact task、complete post-image、source 与 constraints 在新 Study
   create 时从真实输入路径派生并写入 canonical contract，不接受裸 hash 或代理推断；
2. restore executor label 固定为 `Verifier`，Apply 与 restore 仍分别需要独立明确授权；
3. privacy-minimized identity label 使用 `self_attested_privacy_label_v1`，仅证明记录一致性，不声称
   密码学认证身份。

三项决定不等于具体 Study root、fixture path、post-image bytes、Apply 或 restore 授权。新 Study create 前
仍须绑定全新唯一 roots、具体文件和 exact task；未绑定时 R1 保持 `BLOCKED`。不得用空 observation、
泛化 fixture 描述或旧 v2 handoff 补足这些字段。

## 7. 回退

工程回退只移除新的 Phase 3E module、CLI、manifest、docs 和 tests；不修改或删除既有 records。
真人 `REVISE`/`FAIL` 保留现场并回到暴露缺陷的工程阶段。User RC 未接受时，保留只读/导出能力，
不自动改变 VERSION、tag、包或公开渠道。

## 8. 终态工程证据

### 2026-09-01 v4 participant-instance protocol

v4 是 additive 协议：R1 与 R2 都保留为同一 Participant 的真人实例测试；独立真人 Reviewer 被移除，
Sol technical acceptance 使用独立 schema/hash domain 的 `EngineeringAcceptanceV4`，不能冒充真人证据。
既有 v3 canonical records 冻结为 policy-hold 历史：parse/check/status 保留，新 v3 Round、Summary 和
User RC 创建以 `PROTOCOL_HOLD` 失败关闭。详见
[`v3-phase-3e-v4-study-handoff.md`](v3-phase-3e-v4-study-handoff.md); it is
start-prompt-only and does not authorize Study creation.

- profile/procedure：`projecttown-phase3e-rc-v4` / `phase3e-release-candidate-v4`；
- manifest SHA-256：`24ce4fef9e069a92026790ca9fa859fca9480b3beb696d90caefb36a66521aa4`；
- Study/Round/Summary/User RC 分别使用 additive v4 schema 与独立 `/v4` hash domain；
- `EngineeringAcceptanceV4` 使用 `v3-phase3e-engineering-acceptance-v4` 与
  `projecttown/v3/phase3e-engineering-acceptance/v4`，其 acceptance hash 与 evidence bytes hash
  均进入 Round；
- 两个最终 focused fresh roots 各 exit 0、`30 passed`：
  `sandbox/tmp/phase3e-v4-sol-i-final` 与 `sandbox/tmp/phase3e-v4-sol-j-final`；
- 全仓回归 exit 0：`1265 passed, 18 skipped`；
- coverage 收集为 84.37%，`coverage report --fail-under=80` exit 0。coverage pytest 曾有一个既有 MCP
  thread-race failure（`1264 passed, 18 skipped, 1 failed`），同一测试随后独立 recovery rerun exit 0、
   `1 passed`；不得把该次 coverage pytest 写成全套通过。

### 2026-09-01 v4 canonical Study closeout（当前状态）

外部 create-only root 为 `D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v4-20260901-002`，其
sibling work root 用于工程与 User RC evidence。所有 canonical `check`、`status`、binding 与 hash
核验均 exit 0，offline provider/image/embedding/MCP/network/paid counters 均为 0。当前
`blocker_count=0`、`next_action=hold_for_version_gate`。

| Record | Canonical hash | File SHA-256 |
| --- | --- | --- |
| Study | `8f89d1f684be23509943fe30c6f74b6891922d9719b84fc1327fc518b530ca40` | `7aefdd7fea08af52a607c1ec774c5583e12fd246bb856edbf704ba513cc555e9` |
| R1-CONTROLLED-APPLY | `d77f963e39a25848f1bb5ead9d55fb3ddfc76ef56bff57d112ac772ab67b0c2d` | `da19f96dc9398333f7899e21fa89cf580889ed5424d3a2f0b1aa529b59ea7940` |
| R2-REPORT-EXPORT | `b2178eabd0e60caef1f3092da717355f3b89392d3969cb291ddd66199d7c3995` | `521119bf6a4706364e0c7ccc6db7a5c1bd1d150a7d82bbcb3cdd6dda7d44cd40` |
| Summary | `64906e1a9a53ca67563e2114cf9784a40b981187cb5424e71b57a52d66c9dd32` | `a4451b8dab2bf39b96c04a39ae470266ed7e76cc60272a9d6c57bd94a197658b` |
| User RC decision | `13cbe4fa04d48c555a6d98566bdc136198410b2f014d059ede6ad30868d32073` | `f0200ad8a748870f8cce6f8e0d9b1545e523dd10d8c38fd8819de3a64b1c3051` |

R2 participant PDF SHA-256 为 `f78db05f533179b655cab3c2e806c26598fce6ac2140bcf4c644b1dace8d5021`；
User RC evidence SHA-256 为 `cc05c696099594e4d6cee9f54b117b1331e80947276908570aff9cfc61f6012c`。
R1/R2 Participant 均为 `retained`、`control_rating=4`、`citation_usable=true`、
`structural_rewrite=false`；两项 `EngineeringAcceptanceV4` 均为 `PASS`。Summary gate
`criteria_met_awaiting_user_rc_acceptance` 与随后 User `ACCEPT` / outcome
`rc_accepted_pending_version_gate` 必须保持为不同的记录状态。

### 2026-09-01 additive v3 终态

- manifest SHA-256：`daf25fc1812a987961667d82f625ff53f60e6a94314b69cbc3e5ffa05751f587`；
- v3 focused 两个 fresh roots：各 exit 0，`20 passed`；
- controlled-write 回归：exit 0，`34 passed`；
- 全仓库：exit 0，`1221 passed / 17 skipped`；
- coverage：exit 0，`1221 passed / 17 skipped`，TOTAL 29,340 statements、2,640 missed、91%，
  `--fail-under=80` 通过；
- changed Python Ruff check、format check、compileall 与 pip check：均 exit 0；
- v2 frozen Study 前后 SHA-256 均为
  `ca7dc1ab971f460ec113d366b3e045b81899929f472265786d0789b806c3f76c`，只读 check/status 均 exit 0；
- provider、image、embedding、external MCP、network/egress 与 paid calls：均 0。

证据根：`sandbox/tmp/phase3e-v3-final-tester/`。17 个 skip 是 Windows symlink/reparse 权限、Linux
container lock、FIFO 与显式关闭的 live-model 分支，不得写成通过。

### 2026-08-31 历史终态

- v2 focused/recovery：`16 passed / 0 failed / 0 skipped / 1 warning`，exit 0；
- 全仓库默认测试：`1194 passed / 0 failed / 17 skipped / 1 warning`，exit 0；覆盖率复跑结果相同，
  TOTAL 14189 statements、1781 missed、87.45%，`--fail-under=80` 通过；
- `ruff check .`、165 个 source 文件的 `ruff format --check`、`compileall` 与 `pip check` 均 exit 0；
- 74 个历史格式欠债文件已在外部逐文件 SHA-256 备份后机械格式化，AST 对比 0 mismatch；
- Linux 离线容器的平台分支 `156 passed`；Docker Compose 两个修复后 fresh roots、Godot 两轮各
  21/21 captures 均通过；
- 唯一 pytest warning 是既有 Starlette TestClient/httpx deprecation；provider、image、embedding、
  MCP、network/egress 与 paid calls 均为 0。

以上为 2026-08-31 的历史收官证据，日志位于 `sandbox/tmp/phase3e-final/`。2026-09-01 的当前复验见
[`v3-phase-0-4-acceptance-2026-09-01.md`](v3-phase-0-4-acceptance-2026-09-01.md)。工程测试不会使本节
升级为真人接受。
