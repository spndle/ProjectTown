# ProjectTown v3 当前代码审查、Phase 3E 与 Phase 4B 工程验收（2026-08-31）

> **历史快照通知（2026-09-01）：** 本文件的 v2 “awaiting two human rounds” 状态已被后续复核取代。
> v2 Study 现为 Protocol HOLD；additive v3 的当前状态见
> [`v3-phase-0-4-acceptance-2026-09-01.md`](v3-phase-0-4-acceptance-2026-09-01.md)。

## 结论

当前代码已经从 3D 推进到两个互不替代的状态：

`PHASE_3E_V2_STUDY_CREATED_AWAITING_TWO_HUMAN_ROUNDS`

`PHASE_4B_LOCAL_AUTHORING_ENGINEERING_COMPLETE_DEFAULT_OFF`

这表示 P0 已确认，v2 两轮 release-candidate 的 additive records、外部 source snapshot、canonical
verifier、record-only CLI、create-only publication、只读 status projection、正负/恢复样例和全仓库回归已完成，正式 Study 也已 create-only 创建。它不表示任何真人 Round 已完成、
User 已接受 RC、任何真实文件获准写入，或 VERSION/Publish/Distribution 已获授权。
Phase 4B 状态仅表示普通用户可在默认关闭、native-loopback-only 的入口中选择 opaque source IDs，完成
Draft、显式确认、确定性 Result、preview 与 create-only Markdown/PDF 导出；它不表示真人接受，也不增加
Apply、Restore、Retain、Discard 或 Publish 权限。

## 文档与代码缺口及处理

| 审查发现 | 风险 | 本轮处理 |
| --- | --- | --- |
| 3E 只有蓝图，没有独立 Study/Round/Summary/User RC 合同 | 工程结果、Reviewer PASS 与 User/Release gate 容易混淆 | 新增 4 个独立 schema/hash domain 和 procedure manifest；旧 usability/3A–3D records 不变 |
| `verified` 可能被调用方当作输入声明 | 任意 JSON 或错误 lineage 可冒充证据 | canonical records 必须由既有 parser 重载，核验 bytes/hash/type，并交叉绑定 Result→Plan→Proposal→Authorization→Receipt |
| Round 1 的 restore 曾只能绑定普通 observation | 任意 JSON 可冒充真实恢复 | 强制独立 RestoreAuthorization、restore backup 和 canonical restore receipt；实际工程 fixture 执行 apply 后再授权 restore |
| participant count、Reviewer 独立性只写在 policy | P0 看似存在但不能执行 | participant self-reported identity 进入 hash；Summary 核验去重人数与 Reviewer 不同身份；不夸大为认证身份 |
| participant/reviewer/User decision 只保存 evidence path | 文件后续漂移不被发现 | 三类 evidence bytes SHA-256 进入 record hash，check 重读安全普通文件并比较 |
| v3 能力入口分散 | 用户不知道当前 blocker 和下一步 | 新增只读 status projection/CLI；不新增第二套 mutable truth，不扩展浏览器授权表面 |
| 普通用户本地资料入口仍缺失 | CLI 能力存在但任务、preview 与 citation 不易发现 | Phase 4A 提供只读 Workbench；Phase 4B 增加默认关闭的选择资料、Draft、确认、生成、preview 与 create-only 导出闭环，浏览器仍不接收路径、命令或授权动作 |
| MCP stdio 子进程管道依赖对象析构清理 | `-W error` 下产生 ResourceWarning，异常关闭还可能跳过余下管道与 controller 清理 | `_Session.close()` 增加并发幂等锁、逐管道 best-effort close、进程 wait 与不可短路清理；故障注入和全量测试通过 |
| Round 2 外部资料根无法进入 v1 Round | 真实 source provenance 与 work evidence root 被错误混为一处 | 新增 Phase 3E v2 profile/schema/hash domain 和 `source-set-manifest`；外部资料快照与 work-root 产物分离，v1 bytes/hash 不变 |
| 高星 Agent 项目功能容易诱发范围膨胀 | marketplace、多 Agent、调度、provider 集成会扩大风险而未证明价值 | 仅采用“清晰状态/HITL/可恢复/资料与引用连续性”的启示；详细裁决见 `v3-agent-project-landscape-2026-08-31.md` |

## 当前用户价值判断

现有核心价值是 Quest 创建与运行、Evidence/事件、pause/resume、人工 decision、artifact
preview/retain/discard，以及 3A–3D 的精确授权、备份、receipt、reconcile/restore 安全链。它能够解决
“本地任务如何可观察、可恢复并由用户控制”。

普通用户现在可以从一个默认关闭的本地入口完成“显式资料选择→grounded suggestion→preview/export”。
“明确授权写入”仍必须停在独立的 3C/3D 精确 per-operation gate，Phase 4B 不桥接或代填授权。
当前仍不建设默认多 Agent、marketplace、schedule、真实 provider/MCP 或 migration 8。

## 验证结果

| 检查 | 终态 |
| --- | --- |
| Phase 3E v2 focused/recovery | exit 0；16 passed、0 failed、0 skipped、1 warning；log `sandbox/tmp/phase3e-closeout-20260831/phase3e-v2-sol-review-2.log` |
| Phase 4A focused | exit 0；7 passed、0 failed、0 skipped、1 warning；log `sandbox/tmp/phase3e-closeout-20260831/phase4a-sol-review.log` |
| 全仓库 pytest | exit 0；1194 passed、17 skipped、1 warning；log `sandbox/tmp/phase3e-closeout-20260831/pytest-full-1.log` |
| 全仓库 coverage pytest | exit 0；1194 passed、17 skipped、1 warning；log `sandbox/tmp/phase3e-closeout-20260831/coverage-full.log` |
| coverage gate | exit 0；87.45%，fail-under 80 passed；TOTAL 14189 statements、1781 missed |
| Ruff repository check | exit 0；`All checks passed!`；临时证据仅排除 `.tmp` 与 `sandbox/tmp` |
| Ruff source format | exit 0；165 files already formatted；74 个历史欠债文件 AST 前后差异为 0 |
| Linux 平台分支 | exit 0；156 passed、0 skipped、1 warning；log `sandbox/tmp/phase3e-closeout-20260831/linux-platform-tests.log` |
| Docker Compose runtime | 两个修复后 fresh roots 均 exit 0；显式 non-dot defaults、local image、no build/pull；health/restart/SQLite integrity/foreign keys 通过 |
| Godot 4.7.1 runtime | 两个 fresh roots 均 exit 0；每轮 21/21 captures passed，两轮 PNG/report SHA-256 一致 |
| Phase 4A/4B focused + recovery | exit 0；150 passed、0 failed、0 skipped；JUnit/日志位于 `sandbox/tmp/phase4b-20260831/` |
| MCP pipe lifecycle focused | exit 0；17 passed、0 failed、0 skipped、0 warnings；`sandbox/tmp/phase4b-final-20260831/pytest-mcp-pipe-final.xml` |
| Phase 4B 后全仓库 pytest | exit 0；1217 passed、17 skipped、0 warnings；`sandbox/tmp/phase4b-final-20260831/pytest-full-final.xml` |
| Phase 4B 后 coverage pytest | exit 0；1217 passed、17 skipped、0 warnings；84.86%，fail-under 80 passed；`sandbox/tmp/phase4b-final-20260831/pytest-coverage-final.xml` |

第一次相关回归命令因引用不存在的 `tests/recovery/test_v3_controlled_write_recovery.py` 而 exit 1，未执行
测试。更正为实际文件集合后 50 passed，再由 423-case 与全仓库两轮结果覆盖；这不是产品测试失败。
在增加“self-consistent 但伪造 Summary gate 也必须拒绝”的最后断言时，一次局部补丁将 import 放错位置，
focused test 曾为 2 failed / 7 passed；修正并格式化后恢复为 9 passed，最终 fresh E/F 均为 13 passed。

## 已知问题与未验证范围

1. 3E P0 已由用户确认；正式 Study 已 create-only 创建，当前 status 为 `engineering_only` 且缺少两个 Round、Summary 与 User RC decision。两轮参与者/Reviewer 证据不得由代理代填。
2. 既有 source 格式债已在外部逐文件哈希备份后机械格式化；备份为
   `D:\ProjectTown-usability\projecttown-v3-closeout-backup-20260831-001`，74 文件核验 0 mismatch，manifest SHA-256 为 `a2961e260dba6e2e55cc35bddbf6d48e7e2257901a6bd0b298979e81786be33b`。格式化后 Ruff、compileall 与全量测试均通过。
3. `pyproject.toml` 将 Ruff 排除精确限制为历史 `.tmp/` 和 `sandbox/tmp/`，并启用 `force-exclude`；这使
   `ruff check .` 不再把故意损坏的临时 fixture 视为 source。其余路径仍受 Ruff 检查。
4. 经用户明确授权后已安装 `httpx2==2.12.0`、`httpcore2==2.12.0` 与 `truststore==0.10.4`；
   TestClient import、聚焦回归、全量与 coverage 均在 `-W error` 下通过，未 suppress warning。
5. 17 个 Windows skip 分为明确关闭的 live-provider gate，以及 Windows 不可用的 symlink/FIFO/
   container-lock 分支。CI 使用 `pytest -ra` 输出每项理由；适用的 Linux 分支已在离线容器中 156 passed，
   live-provider 在零调用政策下必须继续 skip。provider、network 与 paid calls 保持 0。
6. Docker validator 的第一次真实运行正确暴露 `starting` 时序失败；加入 60 秒有界 health wait 后，两个
   fresh roots 通过。Godot 使用固定 SHA-256 的 4.7.1 console/GUI 二进制完成两轮各 21 张真实捕获。
7. 仓库没有可用 Git metadata；用户已明确决定暂时搁置 Git 设置。changed-path 审查依赖受允路径、
   canonical record/hash、测试与时间戳，不能提供 Git diff/commit 证明；VERSION/tag/Publish 不在授权内。
8. Phase 4A/4B 已完成默认关闭的只读与 candidate-authoring 工程闭环，但尚无人因验收；Apply 仍是独立、
   精确授权流程，不能由 Phase 4B 的确认短语或导出动作触发。

## 回滚

删除新增 Phase 3E module、CLI、manifest、3 个 Phase 3E test 文件和 3E 专用文档，并还原 README、
`v3-phase-3.md` 与 `v3-product-direction.md` 的状态段即可。不得删除任何未来真人 records 或既有
3A–3D/Phase 2 证据。由于本轮未改 API、DB/migration、Quest、Godot、VERSION 或真实目标，回滚不需要
数据迁移或目标文件恢复。
