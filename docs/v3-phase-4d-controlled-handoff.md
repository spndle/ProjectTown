# ProjectTown v3 Phase 4D：Controlled-write bind-only handoff

> **ENGINEERING VERIFIED / 非产品接受。** 该 additive adapter 仅重新核验既有 4B、3A、3B
> record，并 create-only 发布一个待独立授权的 binding。它不创建 plan/proposal/authorization，不执行
> Apply/restore，也不写 target；Phase 4C 和每次操作的独立授权仍是硬门禁。

## 1. 目标与非目标

4D 的最小目标是：把一个已重新核验、fresh、无 conflict 的 4B `readme` Result 绑定到现有 3A
`ApplyPlan` 和 3B `ExecutableProposal`，同时保持 3C 的 UserAuthorization、Apply、reconcile、receipt
与独立 restore authorization 完全不变。

4D 不支持 plan/report Apply，不把 Markdown/PDF export 当作 post-image，不接受浏览器传入路径，不自动
创建 authorization，不执行真实目标写入，也不修改 4B/3A/3B/3C 的旧 record bytes/hash。

## 2. 复用调用链

```text
4B opaque source IDs
  -> create_authoring_draft
  -> confirm_and_generate
  -> Result + AuthoringBindingV2
  -> verify_authoring_binding
  -> prepare_apply_plan                 # 3A, no write; independently invoked
  -> create_executable_proposal         # 3B, no write; independently invoked
  -> 4D handoff binding (create-only; proposal_bound_awaiting_separate_apply_authorization)
  -> separate create_authorization      # 3C, user-authorized later
  -> apply / reconcile
  -> separate restore authorization
  -> restore / check
```

4B confirmation 只允许生成候选。它没有也不能映射为 3C UserAuthorization。

## 3. Additive handoff record

已实现的 record identity：

- schema：`v3-local-workspace-task-controlled-handoff-v1`；
- hash domain：`projecttown/v3/local-workspace-task-controlled-handoff/v1`；
- 独立 create-only 目录：`controlled-handoffs/`，不得复用 `bindings/` 或 `authoring-bindings/`；
- producer：`projecttown-local-workspace-task-controlled-handoff-v1`；
- state：`proposal_bound_awaiting_separate_apply_authorization`；
- handoff_semantics：`verified-binding-only-no-authorization-no-target-write`。

最小绑定字段：`authoring_binding_hash` 与 `authoring_binding_bytes_sha256`、task ID、work/material/evidence
root identity、catalog hash、Result canonical path/bytes hash/session hash、artifact kind、从
`ResultSession.draft.readme_target` 派生的唯一 target canonical/relative path、3A plan bytes/hash、3B
proposal bytes/hash/post-image identity，以及 handoff record hash。所有 root/path 字段在 parse 时必须为绝对
路径语法，target-relative path 必须是安全、规范的 POSIX relative path。不得存入调用者自报 target、nonce、
authorization 或 permission mode。

CLI 只提供 `create` 和 `check`。`create` 显式接收 work/material/evidence roots、task、binding、plan、
proposal 和 output；`check` 仅接收 work/material/evidence roots 和 handoff，record 内已绑定的 canonical
input paths 是唯一权威。output 必须精确为 `evidence_root/controlled-handoffs/{task_id}.json`。

## 4. 强制验证

1. 完整重新验证 `AuthoringBindingV2` 的 request/intent/receipt/Result/root/source chain；
2. artifact kind 必须精确为 `readme`，Result 必须 fresh、无 unresolved conflict；
3. target 只能从 `draft.readme_target` 派生，并在 material root 下重新安全解析；
4. 3A 必须重新观察 target identity、before hash/size 与 scope；permission mode 与 parent identity 由
   后续 3C authorization 重新观察和绑定，4D 不复制该职责；
5. 3B 的 complete post-image 是唯一执行真值；preview/export 不能替代；
6. duplicate、stale、tamper、root overlap、reparse、non-regular target 一律失败关闭；
7. 4D handoff、3A plan、3B proposal 均 create-only，且 `write_performed=false`；
8. 3C Apply 与 restore 分别等待之后的独立精确授权；本 record 的
   `authorization_included=false` 仅描述 record 本身，不能推断系统不存在 authorization。

## 5. 权限分层

- **实现授权：** 允许新增独立 adapter module、CLI、record 与测试；不包含任何 Apply/restore。
- **disposable operation 授权：** adapter 产生精确 before/post binding 后，针对每一个命名 fixture 的
  Apply 与 restore 分别授权。
- **真实目标授权：** 未来另行绑定 exact canonical target、current hash/size/mode、完整 post-image、
  operation scope；不能从工程授权推导。
- **身份语义：** bind-only 4D record 不包含身份字段。现有 3C 继续诚实声明
  `explicit-local-caller-v1`，它不是密码学人类身份；若未来要求更强身份，只能作为独立、versioned
  user-confirmation evidence 协议另行设计和授权，不能回填到本 v1 record。

## 6. 实施改动范围

本轮新增：

- `backend/app/local_workspace_task_controlled_handoff.py`；
- `scripts/run_v3_local_workspace_task_controlled_handoff.py`；
- 对应 unit/integration/recovery tests；

不修改 `local_workspace_task_authoring.py`、`controlled_apply.py`、`executable_proposal.py`、
`controlled_write.py`，也不增加 API/UI、migration、Quest、Godot 或生产依赖。

## 7. 工程验证矩阵

- 两个 fresh roots：valid 4B README Result -> 3A plan -> 3B proposal -> 4D handoff，root 内 canonical
  bytes/hash 可重复核验；由于 absolute paths 与 inode 进入绑定，不能错误要求跨 root handoff bytes 相同；
- plan/report kind、tamper、stale、conflict、target mismatch、unsafe path/root、duplicate output：拒绝；
- publication interruption/attention：保留现场并聚焦恢复；
- v1/v2 bindings、4B records、3A/3B/3C canonical bytes/hash 不变；
- provider、embedding、external MCP、network/egress、paid calls 为 0；
- disposable Apply/restore 测试仅在分别取得操作授权后运行，且不能冒充 4C 或真人接受。

## 8. 当前阻断

bind-only adapter 已完成 Sol 差异复核、39 项新增测试和两个 fresh roots 的独立工程验证。它不能改变
4C、逐操作 authorization 和真实目标 authorization 的顺序与边界，也不构成 Phase 4、真人或产品接受。

独立终态证据位于 `sandbox/tmp/phase4d-final-tester-20260901/`：新增 unit/integration/recovery suite
exit 0，`38 passed, 1 skipped`；4B/3A/3B 无目标写入回归与 release-artifact contract exit 0，
`91 passed, 3 skipped`。accepted roots `fresh-E`、`fresh-F` 均完成 4B public core/API → 3A CLI →
3B CLI → 4D CLI；两根 Result SHA-256 均为
`5c27336ed844dfdae7fdb65513157a02ea081a06ccf42209e51c1b8eb6ef6fe0`，post-image 均为 408 bytes、
SHA-256 `439f44098189a4bde0a15a2f7777359d3de8daf8a6103794641efa4b92e37bff`，target bytes 前后
SHA-256 均为 `c69549fad6595a39053daee5559308d47214155313df7c2ba879544a2b270494`。跨根 plan、proposal 与
handoff bytes 因 absolute paths/inode identity 绑定而预期不同。

仓库没有 4B authoring creation CLI；工程链使用现有 public core/API 形成 canonical 4B records，不能
把该证据误报为全 CLI 工作流。全仓库套件含未经本轮逐操作授权的 Phase 3C Apply/restore 测试，故本轮
没有运行；这项权限边界必须保留为剩余验证范围。
