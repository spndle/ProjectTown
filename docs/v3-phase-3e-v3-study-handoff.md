# ProjectTown v3 Phase 3E v3 真人 Study 启动模板

> **R1 APPLY + RESTORE CHAIN COMMITTED / AWAITING HUMAN ROUND EVIDENCE。** PREPARE_ONLY、
> create-only Study、R1 preparation、一次受控 Apply 与一次独立 Restore 均已完成；当前状态仍为
> `engineering_only`。不得据此自动创建 Round、Summary 或 User RC decision；不得重复执行 Apply/restore。

## 固定协议

- candidate profile：`projecttown-phase3e-rc-v3`
- manifest：`D:\pycharmproject\ProjectTown\examples\v3-phase-3\projecttown-phase3e-manifest-v3.json`
- procedure：`phase3e-release-candidate-v3`
- Study/Round/Summary/User RC schemas：各自 `v3`
- participant count：`1`，同一用户完成两轮
- control rating gate：`>= 4/5`
- restore executor label：`Verifier`
- identity mode：`self_attested_privacy_label_v1`
- participant label：`participant-user-01`
- Independent Reviewer label：`independent-reviewer-01`
- backup/ledger/receipt：保留至用户明确授权清理
- release evidence：create-only canonical JSON + SHA-256 清单
- provider/embedding/external MCP/network/paid calls：`0`

## 已绑定的启动值

- new unique Study ID：`projecttown-v3-phase3e-rc-v3-20260901-001`
- canonical Study root（已 create-only 创建）：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001`
- prepared sibling work root：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-work`
- Round 1 disposable material root：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-fixture`
- Round 1 target：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-fixture\README.md`
- Round 1 exact task：`仅为 disposable README.md 追加一段带来源依据的 Phase 3E 受控 Apply 与 Restore 演示说明；不得修改其他文件。`
- Round 1 complete expected post-image file：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-work\expected-post-image.md`
- Round 1 source policy：`--round1-no-external-sources`，无 `--round1-source-entry`
- Round 1 canonical constraints：空列表，不传 `--round1-constraint`
- Round 2 material source root：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-materials`
- Round 2 fixed Report task：`生成一份 release-candidate 状态报告，说明已完成能力、当前阻断、工程证据、用户门禁和回滚步骤；关键结论必须有引用，不执行 Apply。`
- Round 2 fixed source paths：`docs/v3-current-code-audit-2026-08-31.md`、`docs/v3-phase-3.md`、
  `docs/v3-phase-3e.md`、`docs/v3-product-direction.md`
- 每次 Apply authorization：`<SEPARATE_EXACT_USER_AUTHORIZATION>`
- 每次 restore authorization：`<SEPARATE_EXACT_USER_AUTHORIZATION>`

Study、work、R1、R2 路径均已 canonical、存在且满足 root separation；Study root 仅含 canonical
`study.json`。expected post-image 由安全读取派生 hash/size，不能用调用者手填
摘要代替。R1 与 R2 material roots 不得重叠；target 不得是真实用户重要文件。

## 已演练并准备的 Round 1 绑定

以下值只在仓库内 `sandbox/tmp/phase3e-v3-r1-rehearsal-20260901/c` 与 `d` 完成了两次独立、无写入
工程演练；它们不是外部 Study、Round、Apply 或 restore 授权：

- disposable README 初始内容（UTF-8、LF、150 bytes）：

  ```text
  # ProjectTown Phase 3E Disposable Fixture

  This file contains no user data. It exists only for one separately authorized Apply and restore rehearsal.
  ```

- 初始 SHA-256：`7368b9d293f1bf5e81e019af1eded15b7f9db94324ca94369369c3330afb0482`
- exact task：`仅为 disposable README.md 追加一段带来源依据的 Phase 3E 受控 Apply 与 Restore 演示说明；不得修改其他文件。`
- source policy：不使用外部来源；只允许 disposable `README.md` 自身作为 `S001`
- canonical constraints：空列表
- deterministic complete post-image：608 bytes
- post-image SHA-256：`1ff1768b9a12da02a644d4220c93815a9b6239c4d5eef0de5e07e53718c4eeac`

不得把 exact task 写成 `Apply/Restore`：该 slash 形式会被现有安全展示层解释成类似本地路径并替换为
`[local-path-redacted]`。失败候选 A/B 已保留；修正后的 C/D 均执行 material draft/generate/check、
controlled-apply prepare/check、executable-proposal create/check，所有命令 exit 0，且
`write_performed=false`。实际外部 fixture/material/work 已在用户明确 `PREPARE_ONLY` 授权后完成；
该授权不包含 Study create、Apply 或 restore。

已准备的外部路径：

- Study ID：`projecttown-v3-phase3e-rc-v3-20260901-001`
- Study root：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001`
- sibling work root：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-work`
- Round 1 fixture root：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-fixture`
- Round 2 material source root：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-materials`

2026-09-01 `PREPARE_ONLY` 终态：Study root 仍不存在；work、fixture、materials 三根为 canonical、
非 reparse、互不重叠的普通目录，递归 inventory 精确为 6 个批准文件。Round 2 fixed task 由
`Round2SourceContractV2.fixed_task` 的 Literal 合同约束；上述四个 source paths 沿用 v2 已注册
source set，已从当前仓库逐字节复制到外部 material source root。在该 PREPARE_ONLY 终态，只有后续
单独授权的 `study-create` 才能在 work root create-only 发布 `source-set-manifest.json`，并在新建空
Study root 发布 `study.json`；PREPARE_ONLY 本身没有提前创建这两个 canonical records。

准备输入的检查值：

- fixture README：150 bytes，SHA-256
  `7368b9d293f1bf5e81e019af1eded15b7f9db94324ca94369369c3330afb0482`
- expected post-image：608 bytes，SHA-256
  `1ff1768b9a12da02a644d4220c93815a9b6239c4d5eef0de5e07e53718c4eeac`
- R2 source-set read-only preview root hash：
  `07efe20c8c27277f9b31955b1082213d272452d6b1f7767a250f743e04cc7c35`
- R2 source-set read-only preview record hash：
  `7bcf5283d0be6d8763cf57b2d2c308aeb10ef0a1341b0de0838fd43e966a6cd9`

PREPARE_ONLY 阶段的 preview hash 只来自内存只读计算；其独立证据位于
`sandbox/tmp/phase3e-v3-prepare-only-final-tester-20260901/`。

2026-09-01，用户随后独立授权并完成一次 create-only `study-create`：

- `study.json`：2910 bytes；file SHA-256
  `c7605e6d43852dcd8ba9a0a6d00412e04820ceb4e6a3943f13f9e76262aeaa33`；Study hash
  `a5176c5191da35bdae36bd23d58c57f77e2b872d89a79d06a6fb1739101e7629`
- `source-set-manifest.json`：1101 bytes；file SHA-256
  `80a26436c79b65e2773c3332f95cb34f7d324cd19808ab9b213e2fb7e715e491`
- CLI `check --record study` 与 `status` 均 exit 0；状态为 `engineering_only`，3 个 blocker，
  `next_action=record_missing_rounds`。
- 独立终验证据：`sandbox/tmp/phase3e-v3-study-create-final-tester-20260901/`。

实际执行 Round 1 Apply 前，必须为 exact target/post-image/operation 取得独立 Apply 授权。该授权不得
同时覆盖 restore；Apply 完成后仍须再次请求独立 restore 授权。Study 创建本身不是任一操作授权。

在请求 Apply 授权前，还需单独授权一次**无目标写入的 R1 preparation**，create-only 生成并校验
`r1-draft.json`、`r1-result.json`、`r1-apply-plan.json` 与 `r1-executable-proposal.json`。该 preparation
只运行 material draft/generate/check、controlled-apply prepare/check 与 executable-proposal create/check；
不得创建 authorization、ledger、backup、receipt 或 Round，也不得修改 fixture README。建议授权原文：

```text
授权仅为 Study projecttown-v3-phase3e-rc-v3-20260901-001 的 R1-CONTROLLED-APPLY
执行无目标写入 preparation：读取并保持
D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-fixture\README.md
不变；在
D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-work
create-only 创建并校验 r1-draft.json、r1-result.json、r1-apply-plan.json 和
r1-executable-proposal.json。仅执行 material draft/generate/check、controlled-apply
prepare/check、executable-proposal create/check；任务、初始 README、expected post-image、
无外部 sources 和空 constraints 必须与现有 Study binding 一致。不得创建 authorization、
ledger、backup、receipt、Round、Summary 或 User RC；不得执行 Apply 或 Restore；不得修改
README 或其他目标文件；provider、embedding、MCP、network/egress 和 paid 调用必须为 0。
```

上述 preparation 已于 2026-09-01 按独立授权完成，七条 CLI 均 exit 0；fixture README 保持 150 bytes、
SHA-256 `7368b9d293f1bf5e81e019af1eded15b7f9db94324ca94369369c3330afb0482`。create-only records：

- `r1-draft.json`：1247 bytes；SHA-256
  `02d584885c9fed8414d8122a3c8c58c5bfcc8d6b1186bd72124e07d1688d90b1`
- `r1-result.json`：5658 bytes；SHA-256
  `ceba1047a55ea642d2e19eaa6c73aaa6650251e8f9bfd162e8b440d99671d951`
- `r1-apply-plan.json`：1120 bytes；SHA-256
  `33f0c149c2f3199720b1ee8fb21bc65de32d482c7ae365be1d723aecb276a5d4`
- `r1-executable-proposal.json`：3092 bytes；SHA-256
  `37b6dd7aa119fff21c01512c83edbbf8458093c430a50f98de3b01b2d8d1d911`

Proposal 为 `proposal_complete_awaiting_apply_authorization`；其 complete post-image 与
`expected-post-image.md` 逐字节相同（608 bytes，SHA-256
`1ff1768b9a12da02a644d4220c93815a9b6239c4d5eef0de5e07e53718c4eeac`）。没有 authorization、
ledger、backup、receipt、Round、Apply 或 Restore。独立证据位于
`sandbox/tmp/phase3e-v3-r1-preparation-final-tester-20260901/`。

下一项 Apply 已只读规划但尚未授权：operation ID `r1-apply-20260901-001`，nonce
`6cb572b4b9a0d7e4f12c8a3659d0be71`，authorization 为 work root 下
`r1-apply-authorization.json`，ledger root 为 `r1-apply-ledger`。允许范围必须只覆盖 ledger root 创建、
authorize、pre-check、一次 apply、post-check，以及仅在该次 apply 返回 `attention_required` 时对同一
operation 执行 reconcile；不得重新 dispatch。Restore 必须等 committed Apply receipt 与 backup 实际
存在后另行授权。

建议 Apply 授权原文：

```text
授权仅对 Phase 3E v3 Study projecttown-v3-phase3e-rc-v3-20260901-001 的
R1 disposable fixture 执行一次受控 Apply。唯一目标为
D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-fixture\README.md；
当前必须精确为 SHA-256
7368b9d293f1bf5e81e019af1eded15b7f9db94324ca94369369c3330afb0482、
150 bytes、mode 0o666。唯一 Proposal 为
D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-work\r1-executable-proposal.json，
SHA-256 37b6dd7aa119fff21c01512c83edbbf8458093c430a50f98de3b01b2d8d1d911，
proposal hash e06ea4d83f91d2750082c94e64a1790607f1690469ecf6bf75505ce8e7cb48ef；
唯一预期 post-image 为 SHA-256
1ff1768b9a12da02a644d4220c93815a9b6239c4d5eef0de5e07e53718c4eeac、
608 bytes。允许创建 work root 下 r1-apply-ledger、r1-apply-authorization.json 以及
operation r1-apply-20260901-001 的 create-only backup、ledger、receipt；nonce 为
6cb572b4b9a0d7e4f12c8a3659d0be71。允许仅执行该操作的 authorize、check、一次 apply
和一次 post-apply check；若该 apply 返回 attention_required，允许仅对相同
authorization/operation 执行 reconcile，不得重新 dispatch 或修改任何其他目标。
不得 Restore、不得创建 Round/Summary/User RC、不得删除任何 evidence，且 provider、embedding、
MCP、network/egress 和 paid 调用必须为 0。
```

上述 Apply 随后按独立授权完成：`authorize`、pre-check、一次 `apply`、post-check 均 exit 0，唯一
dispatch 已 `COMMITTED`，没有执行 reconcile。当前 README 为批准的 post-image（608 bytes，SHA-256
`1ff1768b9a12da02a644d4220c93815a9b6239c4d5eef0de5e07e53718c4eeac`）；immutable backup 为原始
README（150 bytes，SHA-256 `7368b9d293f1bf5e81e019af1eded15b7f9db94324ca94369369c3330afb0482`）。

- Apply authorization SHA-256：`f4592290d5254ebc009c623aa44d8e0769754d632eaa7dde02b7cc1f8edd71ad`
- Apply receipt SHA-256：`616c05f93915fa2b22fcbb13d4214eceebf64432a479cc5c1b0d5a28410b836b`
- event chain：preflight → backup → intent → dispatch → post → receipt
- 独立证据：`sandbox/tmp/phase3e-v3-r1-apply-final-tester-20260901/`

Apply 后 material freshness 暂时为 `stale_or_unavailable` 是合法中间态；只有独立 Restore 将 README 返回
Study 初始绑定后才恢复。Restore operation 固定为 ID `r1-restore-20260901-001`、nonce
`4c6b3ef9183c00bd158496f826a3d994`、ledger `r1-restore-ledger` 与 authorization
`r1-restore-authorization.json`。实际授权原文：

```text
我授权对 D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001-fixture\README.md
执行一次独立、create-only、可审计的 R1 Restore operation，operation ID 为
r1-restore-20260901-001，nonce 为 4c6b3ef9183c00bd158496f826a3d994。
授权范围仅包括：在 work root 创建新的 r1-restore-ledger；创建
r1-restore-authorization.json；执行授权前后只读核验；恰好一次将 README 从当前 SHA-256
1ff1768b9a12da02a644d4220c93815a9b6239c4d5eef0de5e07e53718c4eeac
恢复为已验证原始备份 SHA-256
7368b9d293f1bf5e81e019af1eded15b7f9db94324ca94369369c3330afb0482；
以及仅在该 operation 返回 attention 时执行同一 operation 的一次 reconcile 和终态核验。
不授权新的 Apply、其他目标写入、Round/Study/Summary 创建、发布、清理或删除。
```

上述 Restore 随后按独立授权完成：`restore-authorize`、pre-check、唯一一次 `restore` 与 post-check 均
exit 0，唯一 dispatch 已 `COMMITTED`，没有执行 reconcile。README 已恢复为 Study 初始 binding
（150 bytes、mode `0o666`、SHA-256
`7368b9d293f1bf5e81e019af1eded15b7f9db94324ca94369369c3330afb0482`）；`pre-restore.bin` 保留批准的
608-byte post-image（SHA-256 `1ff1768b9a12da02a644d4220c93815a9b6239c4d5eef0de5e07e53718c4eeac`）。

- Restore authorization SHA-256：`7cef9a6371fbb56dbcb4772d48f42751d54417ad3e1d434bb0a8a14cf349dd54`
- Restore receipt SHA-256：`9a206510dd7b92877b1fbc05bcd7a3be7dc3751e23729aff97cb94759f355e6f`
- Restore receipt event hash：`ee19477ad87eec5fd33ab189ec7f74194b95240430f056bae1122a0d0d6ed783`
- 旧 Apply check：native exit 3 / `TARGET_CHANGED_AFTER_RECEIPT`；Restore check：exit 0 / `COMMITTED`
- Study `check` 与 `status`：均 exit 0 / `self_consistent`；material Result freshness：`fresh`
- provider、image、embedding、external MCP、network/egress 与 paid API：均为 0

## 新会话工作指令

1. 完整阅读 `AGENTS.md`、`.agents/skills/sol-terra/SKILL.md`、
   `docs/v3-phase-3e.md` 与本文件。
2. 先只读复核 v3 manifest SHA-256、当前 focused/full/coverage 终态以及 v2 frozen Study 前后 hash。
3. 逐项复核上述已绑定值与 canonical Study/source-set records。任一 hash、root、source、task/constraint
   或 lineage 漂移时输出 `BLOCKED`，不得创建 Round 或执行操作。
4. 不得重复执行 `study-create`；既有 records 为 create-only 现场。Study create 不是 Apply/restore 授权。
5. Round 1 必须使用现有 controlled-write canonical Apply + Restore 两条完整 chain；Apply-only、空 JSON、
   transcript 或自由文本 observation 不能形成最终 Round。
6. 既有 Apply 与 Restore 均已完成且不得重复 dispatch；旧 Apply check 已进入预期
   `TARGET_CHANGED_AFTER_RECEIPT`，README 与 Study 已恢复初始 binding。不得重新使用 standalone proposal
   check 作为新的派发授权；Round 只绑定既有 canonical proposal、两份 authorization、两份 receipt 与完整
   event chains。
7. Round 2 使用固定 Report task 和显式资料；不执行 Apply。参与者本人提供 evidence，Independent
   Reviewer 独立记录固定问题、评分和 disposition。
8. 两轮完成后才能创建 Summary；自动 gate 最高为
   `criteria_met_awaiting_user_rc_acceptance`。User RC、VERSION、tag、Publish、Distribution 分别需要后续授权。

Git 设置按用户决定暂缓，不是本 Study 的工程或真人 gate。
