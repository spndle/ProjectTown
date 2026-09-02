# ProjectTown v3 Phase 4：本地工作区主入口与选择性扩展路线

> 当前状态：`PHASE_4C_VERIFIED_HOLD_FOR_VERSION_GATE`（2026-09-01）。4A/4B 已在默认关闭、
> native-loopback、离线条件下完成工程验收，4D bind-only adapter 已通过独立双根工程验证，4C 已对
> Phase 3E v4 canonical chain 七项只读核验通过。这不表示 Phase 4 整体解冻、真实目标写入、
> VERSION、Git、provider/MCP 解冻或发布授权。

## 1. 目标与总边界

Phase 4 的基线目标是让普通用户在一个本地入口中完成：

```text
选择显式资料 -> 查看 Draft 合同 -> 明确确认生成 -> 查看 grounded preview/citations
-> create-only Markdown/PDF 导出 -> 下载
```

生成确认只授权生成候选。它不授权 Apply、restore、Retain、Discard、Publish 或替换既有 final。
所有功能默认关闭；浏览器不接收本地路径、命令、generator/provider 选择或授权记录。

## 2. Phase 4A-4E

| 子阶段 | 目的 | 当前状态 | Exit gate / 后续权限 |
| --- | --- | --- | --- |
| 4A Verified-task read-only Workbench | 投影预注册且重新核验的 Draft/Result、preview 与 citations | 工程验收完成 | 默认关闭、loopback-only；无生成、路径输入或 mutable endpoint |
| 4B Bounded offline authoring/export | opaque source IDs -> Draft -> exact confirm -> deterministic Result -> preview -> create-only MD/PDF | 工程验收完成 | 双 fresh roots、正负/恢复、UI/PDF、离线零调用通过；不含目标写入 |
| 4C Human/operational acceptance checkpoint | 只读消费既有 Phase 3E v4 两轮实例结果，判断普通用户流程是否可接受 | 已通过：v4 Study→R1/R2→Summary→User RC `ACCEPT` canonical chain | 不创建第二套 Study 或 adapter；Participant、EngineeringAcceptanceV4、Summary 与 User RC 分离；当前 `hold_for_version_gate` |
| 4D Optional controlled-write handoff | 将已验证的 4B Result、既有 3A plan 与 3B proposal 绑定为 create-only handoff | bind-only 工程基线已由 Sol 验收 | 不创建 plan/proposal/authorization，不写 target；3C、4C 与每次 Apply/restore 仍需独立门禁 |
| 4E Selective extensions | 对 RAG、provider、external MCP、多代理、scheduler、benchmark 逐项裁决 | 全部冻结 | 每项需已证明需求、独立提案、风险审查、默认关闭实现和新的明确授权 |

### 4E 选择性扩展边界

4E 的冻结只禁止**新增**跨边界或选择性扩展；它不要求移除仍受兼容合同保护的既有确定性能力。当前分类如下：

| 选项 | 当前分类 | Phase 4 结论 |
| --- | --- | --- |
| 既有确定性 `v1.rag` | 保留的本地实现；供离线 material workflow 使用 | 不是新增 RAG 变体，继续受 provider/embedding/MCP 计数为 0 的合同约束 |
| legacy provider adapter / live evaluation | 与旧运行时分离、另有门禁 | 不接入 Phase 4 authoring；不得作为离线模式或默认路径 |
| local MCP fixture | 默认关闭的本地 fixture 支持 | 不等同 external MCP；不接入 Phase 4 authoring |
| legacy role routing / simulation | 保留的 v1 runtime 兼容能力 | 不接入 Phase 4 authoring，不作新的 multi-agent 主张 |
| scheduler | 当前没有 Phase 4 scheduler | 不新增或启用 scheduler |
| deterministic v1 benchmark | 保留的本地 benchmark 能力 | 不扩展为新的 formal benchmark 或 Phase 4 价值结论 |

因此，在 4E 冻结期间不存在必须实现的 Phase 4 工程缺口，也没有任何 4E option 获得实现或运行授权。未来若要引入新的 RAG 变体、provider、external MCP、多代理、scheduler 或 benchmark，必须先取得独立的需求证据、版本化提案、风险审查、默认关闭设计和用户对该最小运行的明确授权；不得由 4A–4D、4C 或旧兼容能力推导。

4C 是对现行 Phase 3E v4 canonical `check/status` 的只读路线检查点，不创建第二套 4C Study、adapter
或任何新记录。冻结的 v2/v3 Study 均永久处于 `PROTOCOL_HOLD`；v3 只可作为 v4 R1 的 immutable
predecessor evidence，不能被重新执行、改写或充当 v4 人类评测。4C 只接受以
`projecttown-phase3e-rc-v4`、gate model
`participant_instance_plus_engineering_acceptance_plus_user_v1` 创建并完整校验的
Study → R1/R2 → Summary → User RC decision chain。该链现已在独立 v4 roots 中完成并通过七项 4C
核验；如果未来需要单独的 4B 真人 Study，必须由用户
重新授权新的 versioned protocol 和全新 roots。

### 4C 验收清单

以下各项必须全部有 canonical record 与当前 `check/status` 证据；缺失、stale、conflict、tamper、
lineage/profile/schema 混配或不可重载均为 BLOCK：

1. v4 Study 使用新的唯一 Study/work roots，并绑定 `projecttown-phase3e-rc-v4`、v4 manifest 与
   `participant_instance_plus_engineering_acceptance_plus_user_v1`；
2. `R1-CONTROLLED-APPLY` 将冻结的 v3 Apply + Restore chain 作为 immutable predecessor evidence
   绑定，并由 Participant 完成实例评价；它不重新执行、替代或授权该历史 operation；
3. `R2-REPORT-EXPORT` 使用注册的固定 Report task、显式 source set、Result、preview、citations 与
   Participant 实际查看的 export，且没有 Apply evidence；
4. 同一 Participant identity label 完成 R1/R2；每轮都有 Participant notes、timestamp、evidence path 与
   bytes binding，并提供 `control_rating >= 4/5`、`citation_usable=true` 与
   `structural_rewrite=false`；
5. 两轮各自具有一个非人类、独立哈希的 `EngineeringAcceptanceV4`：`PASS`、citation traceable/usable、
   且没有 blocking defect。它是 Sol 技术证据，不能替代 Participant 实例评价；
6. 两轮 `binding_status=verified`，且 Summary 重新校验后精确为
   `criteria_met_awaiting_user_rc_acceptance`；
7. User 另行给出 `ACCEPT`，User RC decision 精确为 `rc_accepted_pending_version_gate`。

Participant `RETAIN`、EngineeringAcceptanceV4 `PASS`、Summary criteria met 与 User `ACCEPT` 是不同状态。
`RETAIN` 可以保留候选，但不能代替 4C 所需的 RC acceptance；4C 通过也不授权 4D、真实 Apply/restore、VERSION、Git、
Publish 或 Distribution。

## 3. 4A/4B 工程合同

- native loopback、exact Host/Origin、拒绝 forwarded headers；process-local session、CSRF 和 64 KiB
  mutation body 上限；
- work root 与 material root 必须 canonical、安全、现存且互不包含；
- catalog 只返回 display-safe relative names 和 opaque IDs，拒绝 hidden/reparse/symlink/hardlink、
  unsupported type、深度和数量越界；
- request、intent、receipt、binding 使用独立 canonical JSON schema/hash domain，create-only 发布；
- 每次 preview/download 重新核验 root identity、Draft/Result bytes、lineage、receipt chain 与 source
  freshness；
- v1 `bindings/` 与 v2 `authoring-bindings/` 分离；restart/recovery 只从完整已验证链恢复；
- provider、embedding、external MCP、network/egress、paid API 默认并实际为 0。

## 4. 4D 前置架构门禁

4D bind-only adapter 已按以下已审查字段映射实现；它不把 4B confirmation 映射为 UserAuthorization，
不携带 permission mode，也不执行 controlled write。后续 3C/Apply 仍必须满足：

1. Phase 4B Result 到 Phase 3B complete post-image proposal 的精确字段映射；
2. additive schema/hash domains 与旧 bytes/hash 兼容；
3. target identity、before/after bytes、freshness 和 scope guard；
4. authorization identity 与 one-operation scope；
5. idempotency、unknown-outcome reconcile、backup/ledger/receipt；
6. restore 的独立授权；
7. 两个 disposable fresh roots 的正向、负向、中断和恢复样例。

即使该 binding 协议已存在，4B candidate 仍不能被直接重新解释为 `ExecutableProposal`，4B
confirmation 也不能跨越 UserAuthorization；4D 只绑定已经由独立 3A/3B 合同生成并核验的 records。

已完成的架构审查、additive adapter 合同和工程证据见
[`v3-phase-4d-controlled-handoff.md`](v3-phase-4d-controlled-handoff.md)。该实现不构成 Phase 4C、
disposable Apply/restore、真实目标或浏览器写入授权。

## 5. 验收、回滚与保留

4A/4B 的工程验收结果见
[`v3-phase-0-4-acceptance-2026-09-01.md`](v3-phase-0-4-acceptance-2026-09-01.md)。
真人/产品价值仍以 Phase 3E v4 的两轮 Participant 实例评价、每轮独立 EngineeringAcceptanceV4 和
独立 User RC decision 为门禁。

4A/4B 回滚只关闭 `enable_local_workspace_task_create` 或整个 Local Workspace Task feature flag；4D
是未接入 API/UI 的显式 CLI，代码回滚可移除其新增 module/CLI/tests，但不能删除已经 create-only 形成的
canonical handoff evidence。所有 exports、backup、ledger 与 receipt 均保留到用户明确授权清理；不得用
回滚删除来源或历史证据。
