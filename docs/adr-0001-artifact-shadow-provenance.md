# ADR-0001：成果文件兼容性影子 Provenance

- 状态：Accepted
- 日期：2026-08-22
- 适用版本：v2.0 收官候选
- 后续复审：v3.0 的 fail-closed 成果审核阶段

## 背景

现有 Verifier 会重新读取成果并生成 Evidence，成果审核也会冻结路径、大小与哈希；但 `created_by_quest` 和 `diff_scope` 尚不能独立证明文件变化确实发生在本 Quest 的工具动作中。复用 workspace、历史 Quest 和模糊副作用恢复会进一步削弱这段归属证明。

直接把新 observer 作为完成条件会改变既有 `/api/v2` 语义，也会让旧 Quest 无法恢复。因此 v2 先建立旁路、可审计、不可伪装成强证明的 compatibility shadow ledger；是否切换 fail-closed 留给 v3 的版本化产品决策。

## 决策

1. migration 7 只新增四张不可变表，不重写 migration 1–6：
   - `v1_workspace_snapshots`；
   - `v1_workspace_snapshot_entries`；
   - `v1_tool_file_observations`；
   - `v1_artifact_provenance`。
2. 新执行在 `ExecutionAdmitted` 和 lease heartbeat 建立后、任何工具动作前保存 baseline。保存要求 live owner、state-version CAS、正确 admission sequence 和 Quest workspace 绑定。
3. 若恢复时已经存在旧工具动作却没有 baseline，只记录 `legacy_unobserved`；不得扫描当前 workspace 后冒充历史基线。
4. scanner 只读取 Sandbox 内 regular files，不跟随 symlink、junction 或 reparse point，并限制文件数、单文件字节和总字节。原始字节使用 SHA-256；不完整扫描产生结构化状态，不改变 Quest 结果。
5. `write_file` 的实际 before/after 哈希、大小和 change kind 与 action receipt、`ToolCommitted` event 在同一 SQLite 事务中提交。模糊副作用尚未协调前不创建 observation。
6. 最终 snapshot、每个成果的 provenance 行、冻结 manifest 和 `ArtifactReviewRequested` 在同一事务中写入，并校验 Quest、workspace、Evidence、action、event、snapshot 与 artifact hash 的交叉绑定。
7. v2 数据库层只允许 coarse 状态 `shadow`、`legacy_unobserved`、`unrecoverable`。`verified` 不在 migration 7 的允许集合中；细分状态只描述观察结果，不能替代 Verifier/Evidence。
8. 旧 manifest 没有 provenance 字段时继续按原 retain/discard 语义工作。新字段只在 Godot 成果区显示只读审计提示，不控制按钮、权限或状态迁移。

## 扫描与分类边界

完整 baseline/final 与匹配的已提交写 observation 可以得到 `shadow_observed_*`；既有未变化文件、未关联变化或外部漂移分别得到对应 shadow 状态。旧 Quest 使用 `legacy_unobserved`。任一快照不完整、最终文件缺失或哈希/大小不一致时标为 `unrecoverable_*`。

这些名称表示“系统观测到了什么”，不表示“成果已被证明安全、正确或应当保留”。用户仍须查看预览并明确选择保留或丢弃；独立 Verifier/Evidence 仍是验收依据。

## 兼容性与恢复

- `/api/v1` 无 schema 或行为变化；现有 `/api/v2` 默认值不变。
- replay 不重新扫描文件、不重新执行工具，只消费已经持久化的 ledger/event。
- baseline 和 side-ledger 表不可 UPDATE/DELETE；旧数据库 0/4/5/6 前缀均须升级、重开和校验通过。
- migration 7 是 additive migration，不做破坏性 down migration。代码回滚时可停止写入/显示新字段并保留新表；数据回退只能停服恢复完整备份。

## 验收标识

migration 1–6 的 checksum 保持不变；migration 7 的当前 checksum 为：

`53eac8c6c4241107c5c1cd3e85e2ef162f9a6322e5d288f706924a785589a7ce`

完整的命令、重复测试、Docker/SQLite 和 Godot 证据记录在 [`v2-closeout.md`](v2-closeout.md)。

## 留给 v3 的决策

- 哪个版本化 Quest 模式把 provenance 作为 fail-closed 条件；
- 所有写入成果是否强制审核，以及无成果 Quest 的完成语义；
- 单 Quest 是否必须独占 workspace，如何处理用户或外部进程并发修改；
- 是否把 action chain、snapshot diff 和 Evidence 导航公开为审计详情 API/UI；
- Quest-bound RAG provenance 使用 migration 8 或更高版本，不能复用或改写 migration 7。
