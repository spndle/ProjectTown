# ProjectTown v3 Phase 3E 真人 Study 启动 Prompt（v2 protocol HOLD）

> **不得启动。** 本文件记录 `v3-phase3e-study-v2` 的历史 handoff 与冻结 binding，现为 **Protocol HOLD**，不是可执行启动 Prompt。不得使用它创建真人 `R1-CONTROLLED-APPLY`、Round、Summary 或 User RC decision；也不授权 Apply、restore、VERSION、tag、Publish 或 Distribution。
>
> 原因：v2 的 `Phase3ERoundV2` R1 validator 漏掉 `restore_observation`，而 R1 的精确任务、complete post-image、source/constraints 和执行者未被冻结，因此空 observation 可作为路径绑定。不得通过补写 v2 文本、代理推断或现有 fixture 来修补该协议。
>
> additive v3 已另行实现；新的非执行型启动模板见
> [`v3-phase-3e-v3-study-handoff.md`](v3-phase-3e-v3-study-handoff.md)。本文件及其 v2 paths 仍永久只读。

## 冻结的 v2 历史 binding（只读）

已确认配置如下，正式 Study 已 create-only 创建。仅可对它执行只读 `check` 并核对以下哈希；这不是任何 Round、Apply 或 restore authorization：

- Study ID：`projecttown-v3-phase3e-rc-20260831-001`
- Study root：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-20260831-001`
- sibling work root：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-20260831-001-work`
- Study file SHA-256：`ca7dc1ab971f460ec113d366b3e045b81899929f472265786d0789b806c3f76c`
- Study record hash：`a5c8e1a3074a8098212daaa2acb3e7546cf144722e1fb0541c3010d4cf107279`
- source-set manifest SHA-256：`5956096a03a74fc34f44832eb293b52bef14c35b22fb5b9e832446c401bf226b`
- initial release evidence inventory SHA-256：`b1e72819751bb95bc48d98bd61b2072f054c4f55824250c84141c27feac0c0d1`
- control_rating 最低阈值：`4/5`
- participant arrangement / count：same user completes both rounds / `1`
- participant identity label：`participant-user-01`（privacy-minimized）
- Independent Study Reviewer identity label：`independent-reviewer-01`（必须不同）
- backup/ledger/receipt retention：until explicit cleanup authorization
- release evidence format：create-only canonical JSON plus SHA-256 list
- Round 1 fixture root：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-20260831-001-fixture`；其 disposable `README.md` 仅是历史 binding，**禁止用于 v2 R1**。
- Round 2 material source root：`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-20260831-001-materials`；固定任务仅为历史 binding，不能单独使 v2 两轮 Study 可启动。

## v2 历史协议摘要（不可执行）

1. 历史 profile 为 `projecttown-phase3e-rc-v2`、manifest 为
   `examples/v3-phase-3/projecttown-phase3e-manifest-v2.json`，以及独立 schema：
   `v3-phase3e-study-v2` / `v3-phase3e-round-v2` / `v3-phase3e-summary-v2` /
   `v3-phase3e-user-rc-decision-v2`。CLI create 会在 work root create-only 写入
   `source-set-manifest.json`，并重验外部 Round 2 source snapshot 与 Result 内嵌 manifest；不能混用 v1。
2. 历史上设计的 Round 1 = R1-CONTROLLED-APPLY 不得执行；其 observation 合同缺口不能由文件存在性、
   空 observation 或本 handoff 推断补足。
3. Round 2 = R2-REPORT-EXPORT：参与者从显式资料生成 Report，检查 preview/citations 并导出；
   不得出现 controlled-write 或 Apply evidence。参与者 evidence path 必须是本人实际查看的 export。
4. 每轮由参与者本人提供 identity label、disposition、active elapsed、actions、notes、带时区 timestamp
   和 evidence path；代理不得推断、改写或补填。每个 evidence path 及 bytes SHA-256 必须进入记录。
5. Independent Study Reviewer 使用固定四问：哪些可重跑；哪些历史证据不可重造；哪些决定只属于
   User；当前状态与下一步是什么。记录 executability、readability、control、citation traceability
   四项 1-5 分、PASS/REVISE/FAIL、notes、actions、timestamp 与 evidence path。任一固定问题无法回答
   或存在 blocking safety/recovery defect 时不得 PASS。
6. 两轮完成后才创建 Summary。自动 gate 最高为
   criteria_met_awaiting_user_rc_acceptance。工程通过或 Reviewer PASS 不能代替 User RC decision。
7. User 另行提供 ACCEPT/RETAIN/REVISE/DISCARD/STOP。ACCEPT 最高只到
   rc_accepted_pending_version_gate；不得修改 VERSION、创建 tag 或公开发布。
8. 所有 publication create-only；stale、conflict、missing、tamper、path mismatch、hash/provenance
   mismatch 或身份/人数不符合 P0 时失败关闭并保留现场。
9. provider、image、embedding、MCP、network/egress 和 paid API 调用保持 0；不得读取 secrets、
   .env*、Authorization header 或私有 endpoint。
10. 当前 `status` 仍为 `engineering_only`，Study 存在但两个 Round、Summary 与 User RC decision 均不存在；
    但 protocol 状态为 HOLD。不得从本文件推断任何 Round、Apply 或 restore authorization。

## additive v3 前的用户决定

新的 v3 protocol/manifest、全新 Study root 与 sibling work root 必须先得到以下三项明确用户决定：

1. R1 的精确任务、complete post-image、source 与 constraints；
2. restore executor；
3. privacy-minimized identity label 是 self-attestation 还是 cryptographic signature。

在三项写入新的 canonical v3 contract 并经工程验证前，R1 状态为 `BLOCKED`。后续 v3 handoff 必须重新编写；
不得复制本 v2 历史 binding 作为启动指令。
