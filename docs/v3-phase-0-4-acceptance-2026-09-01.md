# ProjectTown v3 Phase 0-4 工程验收记录（2026-09-01）

## 结论

| 阶段 | 工程结论 | 非工程门禁 |
| --- | --- | --- |
| Phase 0 | `ENGINEERING_ACCEPTED` | 无新的产品价值声明 |
| Phase 1 | `ENGINEERING_ACCEPTED` | 真人证据只覆盖有限 plan/PDF，不覆盖 report/README |
| Phase 2 | `SCOPE_LIMITED_CLOSEOUT_VERIFIED` | 不验证 v10/report/README，不授权 Apply |
| Phase 3A-3D | `ENGINEERING_VERIFIED_ON_DISPOSABLE_FIXTURES` | 每个真实 Apply/restore 仍需独立精确授权 |
| Phase 3E | `V4_CANONICAL_CHAIN_ACCEPTED_HOLD_FOR_VERSION_GATE` | v2/v3 永久只读 HOLD；v4 同一 Participant 的 R1/R2、每轮 EngineeringAcceptanceV4、Summary 与 User RC `ACCEPT` 已完成；不授权 VERSION/Git/Apply/Restore/Publish/Distribution |
| Phase 4A/4B + 4D bind-only + 4C | `PHASE_4C_VERIFIED_HOLD_FOR_VERSION_GATE` | 4C 只读七项均通过；4D operation、Phase 4 整体解冻、VERSION/Git/Release/Distribution 均未完成；4E 新扩展按决策保持冻结，不是必补工程缺口 |

## 分阶段终态命令

- Phase 0 focused：exit 0；`32 passed, 1 skipped`；
  `sandbox/tmp/phase01-acceptance-20260901/phase0-pytest`。
- Phase 1 focused：exit 0；`124 passed`；publication recovery：exit 0；`2 passed`；
  `sandbox/tmp/phase01-acceptance-20260901/`。
- 当前 worktree 的 Phase 0/1 聚焦复验已补齐可独立读取的双根证据：c/d 两轮均 exit 0，
  各 `156 passed, 1 skipped`，并分别保存完整日志、JUnit 与 exit-code 文件；证据位于
  `sandbox/tmp/phase01-current-audit-20260901-c/` 与
  `sandbox/tmp/phase01-current-audit-20260901-d/`。首次恢复尝试因 PowerShell 目录创建参数错误只留下
  空的 `phase01-current-audit-20260901-a`，pytest 未启动，该目录不计入验收；b 未创建。
- Phase 2/3 focused 两轮：每轮 exit 0；`302 passed, 4 skipped`；
  `sandbox/tmp/phase23-acceptance-20260901/phase23-junit.xml` 与
  `phase23-rerun-junit.xml`。
- Phase 4A/4B focused：exit 0；`151 passed`；
  `sandbox/tmp/phase4-acceptance-20260901/pytest-phase4.xml`。
- Phase 4A/4B 在 Phase 3E v3 之后独立复验：exit 0；`151 passed, 0 failed, 0 skipped`；
  16 个相关 Python 文件 Ruff check/format check 与 7 个产品模块 compileall 均 exit 0；
  `sandbox/tmp/phase4-ab-revalidation-20260901/`。首次使用裸 `ruff` 因不在 PATH 未完成，随后改用
  `.venv` 内可执行文件通过；失败尝试保留在同一证据根。
- Phase 4D bind-only 新增 unit/integration/recovery：exit 0；`38 passed, 1 skipped`；4B/3A/3B
  无目标写入回归与 release-artifact contract：exit 0；`91 passed, 3 skipped`；Ruff check、5 文件
  format check、compileall 与 pip check 均 exit 0；证据位于
  `sandbox/tmp/phase4d-final-tester-20260901/`。全仓库 suite 因包含未获本轮逐操作授权的 Phase 3C
  Apply/restore 测试而未重跑，不能用上述聚焦结果替代该范围。
- Phase 3E v3 focused 两轮：每轮 exit 0；`20 passed`；controlled-write 回归 exit 0；
  `34 passed`；`sandbox/tmp/phase3e-v3-final-tester/`。
- 全仓库 recovery full：exit 0；`1217 passed, 17 skipped`；
  `sandbox/tmp/phase0-4-final-20260901/pytest-full-recovery.log`。
- 全仓库 recovery coverage：exit 0；`1217 passed, 17 skipped`；TOTAL 16,782 statements、
  2,543 missed、84.85%，`--cov-fail-under=80` 通过；
  `sandbox/tmp/phase0-4-final-20260901/pytest-coverage-recovery.log`。
- Phase 4 相关 Ruff、15 文件 format check、compileall、pip check：均 exit 0。
- Phase 4E selective-extension static boundary contract：exit 0；新增检查 `3 passed`，与 release-artifact
  contract 联跑总计 `10 passed`。Sol 将边界扩展到 4A/4B 并加入 `backend.app.runtime` 禁止直接接线后
  再次复跑仍为 exit 0、`10 passed`。该检查只覆盖 Settings 默认值、Phase 4 authoring 的直接 import 边界和 material
  workflow 的确定性/零调用数据模型；它不证明所有进程路径的普遍零 egress。证据位于
  `sandbox/tmp/phase4e-boundary/pytest-temp` 与 `sandbox/tmp/phase4e-sol-final-20260901/`。
- Phase 4C 对齐 v4 Participant-only 门禁后，Phase 4A/4B、4D bind-only、4E boundary 与 4C 文档合同
  聚焦复验在两个全新根均 exit 0，各 `71 passed, 1 skipped`；证据位于
  `sandbox/tmp/phase4-current-audit-20260901-a/` 与
  `sandbox/tmp/phase4-current-audit-20260901-b/`。Phase 3E v4 合同另在两个全新根复验，均 exit 0、
  各 `30 passed`；证据位于 `sandbox/tmp/phase3e-v4-current-audit-20260901-a/` 与
  `sandbox/tmp/phase3e-v4-current-audit-20260901-b/`。
- PREPARE_ONLY 后的 Phase 4 完成度聚焦复验首次因 `--basetemp` 的父目录尚不存在而 exit 1，终态为
  `103 passed, 97 setup errors`；该失败没有触及外部真人或真实目标。显式创建新的 sandbox 证据根后完整
  recovery 重跑 exit 0，`199 passed, 1 skipped`，JUnit 位于
  `sandbox/tmp/phase4-completion-audit-20260901/pytest-recovery.xml`。唯一 skip 仍是当前 Windows 权限下的
  symlink/reparse fixture，不计作通过。
- 较早的仓库静态快照曾为 `ruff check .`、171 文件 `ruff format --check`、`compileall`、`pip check`
  均 exit 0。加入后续证据与历史副本后，当前复核终态为：`ruff check .` exit 0；`compileall` exit 0；
  `pip check` exit 0；repo-wide `ruff format --check .` exit 1，`249 files already formatted`、3 个既有文件
  would be reformatted（两个 `.codex/backups/v1.0-20260807-1208` 历史副本及
  `docs/project-development-v2.0.md` 代码块）。本任务未批量改写这些无关历史文件；4D/4E changed Python
  files 的聚焦 format check 均 exit 0。当前静态证据位于 `sandbox/tmp/phase4-static-final-20260901/`。

Phase 3E v3 变更后的全仓库终态另为：pytest exit 0，`1221 passed, 17 skipped`，180.92 秒；
coverage pytest exit 0，`1221 passed, 17 skipped`，228.67 秒；TOTAL 29,340 statements、2,640 missed、
91%，`--fail-under=80` 通过。v3 changed Python files 的 Ruff check、format check、compileall 与 pip check
均 exit 0。证据位于 `sandbox/tmp/phase3e-v3-final-tester/full/`、`coverage/` 及根目录静态日志。

当前 v4/4C 文档对齐后的全仓库终态为：pytest exit 0，`1266 passed, 18 skipped`，199.79 秒；
完整日志、JUnit 与 exit code 位于 `sandbox/tmp/phase0-4-current-full-20260901/`。随后
`ruff check .`、`compileall -q backend scripts` 与 `pip check` 均 exit 0。这里“尚未创建”的表述仅是
全量回归当时的历史快照；后续 v4 Participant 实例 Study、Summary 与 User RC 已按下列 canonical 状态创建并复核。

skip 均来自 Windows 当前权限下不可创建 symlink/reparse fixture；适用的非 symlink 负向路径已通过，
不得把 skip 写成通过。

## 双根与确定性

Phase 0/1：

- `D:\ProjectTown-usability\projecttown-v3-phase01-engineering-20260901-a`
- `D:\ProjectTown-usability\projecttown-v3-phase01-engineering-20260901-b`

两根 Phase 0 中文/Python manifest、Phase 1 plan/report/README Result 与导出字节一致。Plan PDF
SHA-256 为 `7971c5c170544d83f2b2bd62db209d30548a3036ce678deeaec566adc36feefc`，2 页；
pypdf/Poppler 和 Sol 逐页检查通过。

Phase 4：

- `D:\ProjectTown-usability\projecttown-v3-phase4-engineering-20260901-a-work` + sibling `-a-materials`
- `D:\ProjectTown-usability\projecttown-v3-phase4-engineering-20260901-b-work` + sibling `-b-materials`

两根 plan/report/README 的 Draft、Result、Markdown、PDF 字节分别一致：

- plan PDF：`19adb850ef4bb8e7f38744cb86c741a0206632a04f5c6df6a0851cc9dcf17f34`，2 页；
- report PDF：`523be73807ee84c78cd692c0e5146b2aa078457ceee40a9c1cb860c0b6f4bcd6`，1 页；
- README PDF：`004f13910ccc099ec30761edbc9cc452d2f7b141203574510e4bda46ed8a0fbf`，1 页。

全部 PDF reopen、文本提取与 Poppler render 通过；无 NUL/replacement character、裁切、重叠、缺字、
黑块或空白页。Plan 第二页较稀疏，但包含有效 citations/offline boundary，不是空白页。

浏览器真实 loopback 验收另使用：

- `D:\ProjectTown-usability\projecttown-v3-phase4-browser-20260901-c-work`
- sibling material root `D:\ProjectTown-usability\projecttown-v3-phase4-browser-20260901-c-materials`

页面完成 session/catalog、opaque source selection、Draft contract、exact confirmation、离线 Report 生成、preview、
Markdown/PDF create-only export 与 download；业务请求均 HTTP 200，浏览器 console 无 warning/error。导出 Report PDF
SHA-256 为 `3f2d8bdba304609538f7d500176b2707b51b599b426f0f5c9e7e34eb8552df72`，1 页、
提取 1062 字符，彩色/灰阶 Poppler 页面均无裁切、重叠、缺字或黑块。

Phase 4D bind-only 使用：

- `sandbox/tmp/phase4d-final-tester-20260901/fresh-E`
- `sandbox/tmp/phase4d-final-tester-20260901/fresh-F`

两根均完成现有 4B public core/API → 3A CLI → 3B CLI → 4D CLI；Result SHA-256 均为
`5c27336ed844dfdae7fdb65513157a02ea081a06ccf42209e51c1b8eb6ef6fe0`，proposal post-image 均为
408 bytes、SHA-256 `439f44098189a4bde0a15a2f7777359d3de8daf8a6103794641efa4b92e37bff`。target
README 前后 SHA-256 均为 `c69549fad6595a39053daee5559308d47214155313df7c2ba879544a2b270494`；
没有 authorization、ledger、backup 或 controlled-write receipt。handoff bytes 因 absolute paths/inode
identity 绑定而跨根不同，符合合同；仓库没有 4B creation CLI，不能把该链描述为全 CLI。

## 正向、负向与恢复

- Phase 0/1：中文/Python、plan/report/README、explicit confirmation、create-only export 正向通过；
  inside-root/existing target 拒绝；stale `check` 以 `freshness=stale_or_unavailable` 观察，export exit 2；
  conflict 停在 `needs_user_decision`；publication rollback/attention 通过。
- Phase 2/3：canonical closeout/Study check 通过；tamper、stale、invalid pairing、unsafe path、remote/proxy、
  premature Summary/decision 拒绝；controlled-write reconcile、publication rollback、idempotency 与 3E
  recovery 通过。
- Phase 4：nested roots 触发 `ROOT_SEPARATION_REQUIRED`；duplicate 触发
  `CREATE_ONLY_CONFLICT`；source mutation 和 Result tamper 进入 `attention`；restart 与 v1/v2 binding
  isolation 通过。
- Phase 4D：duplicate handoff exit 2 / `PUBLICATION_CONFLICT`；renamed handoff、target/source drift
  exit 2 / `HANDOFF_BLOCKED`；wrong plan/proposal mix exit 2；恢复原 fixture 后 focused check exit 0 /
  `HANDOFF_VERIFIED`。所有 status 均不回显绝对路径，离线计数为 0。

## Canonical 外部状态

- Phase 2 closeout file SHA-256：
  `d6b11f9d9730956027c455b5957504dab9953e5334b84204630046797249ba51`；check exit 0。
- Historical Phase 3E v2 Study file SHA-256：
  `ca7dc1ab971f460ec113d366b3e045b81899929f472265786d0789b806c3f76c`；record hash
  `a5c8e1a3074a8098212daaa2acb3e7546cf144722e1fb0541c3010d4cf107279`；check exit 0。
- Historical v2 Phase 3E status：`engineering_only`；rounds `[]`、Summary false、User RC decision false。2026-09-01
  复核发现 v2 `Phase3ERoundV2` 的 R1 validator 未要求 `restore_observation`，且 R1 任务、post-image、
  source/constraints 与执行者未冻结；空 observation 可作为路径绑定。故 v2 为 protocol HOLD，禁止创建真人 R1；
  现有 Study file/record hash 保持上述值不变。当时 additive v3 尚待用户对精确任务/post-image/source/constraints、
  restore executor、以及 identity label 的 self-attestation 或 cryptographic signature 三项决定；决定现已在下段记录。

用户随后同意三项推荐方案：新 v3 Study 必须从真实路径绑定 disposable README 的 exact task、完整
post-image、source/constraints；restore executor label 为 `Verifier`；identity mode 为
`self_attested_privacy_label_v1`。additive v3 使用独立 profile/manifest/schema/hash domain，并以现有
controlled-write canonical Apply + Restore chain 代替自由文本 observation。该同意不是 Study create、
Apply、restore、Round、Summary 或 User RC 授权。

additive v3 manifest SHA-256：
`daf25fc1812a987961667d82f625ff53f60e6a94314b69cbc3e5ffa05751f587`。v3 两轮 engineering
fresh roots 为 `sandbox/tmp/phase3e-v3-final-tester/focused-a` 与 `focused-b`；两轮各执行真实 disposable
Apply→Restore canonical fixture chain 并通过最终复合绑定验证。v2 Study 的前后 SHA-256 均为
`ca7dc1ab971f460ec113d366b3e045b81899929f472265786d0789b806c3f76c`，前后 `check/status` 均 exit 0。

Round 1 候选另在 `sandbox/tmp/phase3e-v3-r1-rehearsal-20260901` 完成无写入演练。A/B 使用
`Apply/Restore` 时被安全展示层替换为 `[local-path-redacted]`，因此保留为失败候选且不得用于真人绑定；
C/D 改用 `Apply 与 Restore` 后，各自七条 material/controlled-apply/executable-proposal CLI 均 exit 0。
C/D 初始 README SHA-256 均为
`7368b9d293f1bf5e81e019af1eded15b7f9db94324ca94369369c3330afb0482`，Result bytes 一致，完整
post-image 均为 608 bytes、SHA-256
`1ff1768b9a12da02a644d4220c93815a9b6239c4d5eef0de5e07e53718c4eeac`，且无脱敏占位符；proposal
hash 因独立目标 inode 绑定而不同。两根均为 `write_performed=false`，没有 authorization、ledger、
receipt、backup、Study、Round 或 Summary。这只证明建议任务与 post-image 的工程确定性；外部
当时 `PREPARE_ONLY`、Study create、Apply 和 restore 仍分别待用户授权。

用户随后明确授权并完成 Phase 3E v3 `PREPARE_ONLY`。Study root
`D:\ProjectTown-usability\projecttown-v3-phase3e-rc-v3-20260901-001` 保持不存在；新建的 sibling
work、fixture、materials 三根仅包含 6 个批准输入。fixture README 为 150 bytes、SHA-256
`7368b9d293f1bf5e81e019af1eded15b7f9db94324ca94369369c3330afb0482`；expected post-image 为
608 bytes、SHA-256 `1ff1768b9a12da02a644d4220c93815a9b6239c4d5eef0de5e07e53718c4eeac`；四份
R2 sources 与当时仓库源逐字节一致。独立 read-only tester 验证 roots canonical/non-reparse/single-link、
严格 inventory、UTF-8/LF、registered source order 与禁止产物均通过，证据位于
`sandbox/tmp/phase3e-v3-prepare-only-final-tester-20260901/`。没有创建
`source-set-manifest.json`、Study、Round、Summary、User RC、Result/PDF、proposal、authorization、
ledger、backup 或 receipt，也未执行 Apply/restore；在该 PREPARE_ONLY 终态，Study create 仍需下一项
独立授权。

用户随后独立授权并完成一次 v3 create-only `study-create`。命令 exit 0，`STUDY_CREATED`、
`publication_state=committed`；`study.json` 为 2910 bytes，file SHA-256
`c7605e6d43852dcd8ba9a0a6d00412e04820ceb4e6a3943f13f9e76262aeaa33`，Study hash
`a5176c5191da35bdae36bd23d58c57f77e2b872d89a79d06a6fb1739101e7629`；
`source-set-manifest.json` 为 1101 bytes，file SHA-256
`80a26436c79b65e2773c3332f95cb34f7d324cd19808ab9b213e2fb7e715e491`。独立 Terra 与 Sol 的
`check --record study`、`status` 均 exit 0；终态为 `engineering_only`、3 个 missing-round blockers，
没有 Round、Summary、User RC、Apply 或 restore。证据位于
`sandbox/tmp/phase3e-v3-study-create-final-tester-20260901/`。

R1 无目标写入 preparation 随后按独立授权完成。material draft/generate/check、controlled-apply
prepare/check、executable-proposal create/check 共七条命令均 exit 0；Draft、Result、ApplyPlan、Proposal
四份 create-only record SHA-256 依次为 `02d584...90b1`、`ceba10...d951`、`33f0c1...a5d4`、
`37b6dd...d911`。Proposal post-image 与已绑定 608-byte expected post-image 逐字节一致；fixture README
仍为 150 bytes、SHA-256 `7368b9d...0482`。没有 authorization、ledger、backup、receipt、Round、Apply
或 restore；独立证据位于 `sandbox/tmp/phase3e-v3-r1-preparation-final-tester-20260901/`。

R1 受控 Apply 随后按独立授权执行。authorize/pre-check/唯一一次 apply/post-check 均 exit 0，唯一
dispatch `COMMITTED`，没有 reconcile。README 从 150-byte 初始 SHA `7368b9d...0482` 变为批准的
608-byte post-image SHA `1ff1768b...c4eeac`；immutable backup、五个 ledger events 与 receipt 均保留。
Apply authorization SHA-256 为 `f4592290...d71ad`，receipt SHA-256 为 `616c05f9...836b`。独立
controlled-write check exit 0 / `COMMITTED`；此时 material freshness 为预期
`stale_or_unavailable`，必须在另行授权 Restore 后恢复。没有 Restore、Round、Summary 或 User RC；
证据位于 `sandbox/tmp/phase3e-v3-r1-apply-final-tester-20260901/`。

R1 Restore 随后按另一项独立授权执行。`restore-authorize`、pre-check、唯一一次 restore 与 post-check 均
exit 0；唯一 dispatch `COMMITTED`，没有 reconcile。README 恢复为 150 bytes、mode `0o666`、SHA-256
`7368b9d...0482`，`pre-restore.bin` 保留 608-byte post-image SHA `1ff1768b...c4eeac`。Restore authorization
SHA-256 为 `7cef9a63...dd54`，receipt SHA-256 为 `9a206510...5e6f`，receipt event hash 为
`ee19477a...d783`。旧 Apply check 按合同返回 native exit 3 / `TARGET_CHANGED_AFTER_RECEIPT`；Restore check、
Study check/status 与 material Result check 均恢复通过，Study 仍为 `engineering_only`、缺两个 Round。

用户随后明确移除独立真人 Reviewer：真人评价只存在于 R1/R2 实例测试。由于 v3 已把 Reviewer 写入
canonical Study/round/summary hash 语义，现有 v3 bytes 保持冻结并以 `PROTOCOL_HOLD` 拒绝新 Round、
Summary 和 User RC；未静默改写。additive v4 使用 profile `projecttown-phase3e-rc-v4`、procedure
`phase3e-release-candidate-v4`、manifest SHA-256
`24ce4fef9e069a92026790ca9fa859fca9480b3beb696d90caefb36a66521aa4`。Participant 在两轮分别提供
控制感、引用可用性和结构重写判断；Sol engineering acceptance 使用独立 schema/hash domain，不能冒充
真人。两套最终 focused roots 各 exit 0、`30 passed`；全仓 exit 0、`1265 passed, 18 skipped`。coverage
为 84.37%，report fail-under 80 exit 0；coverage pytest 的一个既有 MCP race 首轮失败后单测 recovery
exit 0。该句记录的是 v4 engineering pre-study 的历史快照：当时 v4 Study 尚未创建，Participant
证据也未发布；它不描述下列 current v4 canonical closeout。

### Current Phase 3E v4 / Phase 4C canonical closeout

外部 create-only Study 为 `projecttown-v3-phase3e-rc-v4-20260901-002`；其 sibling work root 保存
engineering 和 User RC evidence。Study、R1、R2、Summary、User RC 的 canonical `check`、`status`、binding
和 hash 核验均 exit 0；offline provider/image/embedding/MCP/network/paid counters 均为 0。当前
`blocker_count=0`、`next_action=hold_for_version_gate`。

4C 仅只读核验 Phase 3E v4 canonical check/status；v4 Participant 两轮、每轮 EngineeringAcceptanceV4 与 User RC 均为相互独立的只读证据。

| Record | Canonical hash | File SHA-256 |
| --- | --- | --- |
| Study | `8f89d1f684be23509943fe30c6f74b6891922d9719b84fc1327fc518b530ca40` | `7aefdd7fea08af52a607c1ec774c5583e12fd246bb856edbf704ba513cc555e9` |
| R1-CONTROLLED-APPLY | `d77f963e39a25848f1bb5ead9d55fb3ddfc76ef56bff57d112ac772ab67b0c2d` | `da19f96dc9398333f7899e21fa89cf580889ed5424d3a2f0b1aa529b59ea7940` |
| R2-REPORT-EXPORT | `b2178eabd0e60caef1f3092da717355f3b89392d3969cb291ddd66199d7c3995` | `521119bf6a4706364e0c7ccc6db7a5c1bd1d150a7d82bbcb3cdd6dda7d44cd40` |
| Summary | `64906e1a9a53ca67563e2114cf9784a40b981187cb5424e71b57a52d66c9dd32` | `a4451b8dab2bf39b96c04a39ae470266ed7e76cc60272a9d6c57bd94a197658b` |
| User RC decision | `13cbe4fa04d48c555a6d98566bdc136198410b2f014d059ede6ad30868d32073` | `f0200ad8a748870f8cce6f8e0d9b1545e523dd10d8c38fd8819de3a64b1c3051` |

R2 participant PDF SHA-256：`f78db05f533179b655cab3c2e806c26598fce6ac2140bcf4c644b1dace8d5021`；
User RC evidence SHA-256：`cc05c696099594e4d6cee9f54b117b1331e80947276908570aff9cfc61f6012c`。
R1/R2 Participant 均为 `retained`、`control_rating=4`、`citation_usable=true`、
`structural_rewrite=false`；两轮 `EngineeringAcceptanceV4=PASS`。Summary gate
`criteria_met_awaiting_user_rc_acceptance` 与 User `ACCEPT` / outcome
`rc_accepted_pending_version_gate` 是不同状态。Phase 4C 的七项只读验收均通过；这不授权
VERSION、Git、Apply、Restore、Publish、Distribution，且 4E 仍冻结。

所有验收路径的 provider、image、embedding、external MCP、network/egress 与 paid API 调用均为 0。

## 失败与恢复记录

两次测试器假设错误均保留：Phase 0/1 首轮把 stale `check` 错误预期为 exit 2；Phase 4 首轮把所有
PDF 错误预期为 1 页。按现有 CLI/layout 合同修正 harness 后复跑通过；它们不是产品失败。Phase 4
最初把 material root 嵌套于 work root，正确触发 root-separation 拒绝，随后使用 sibling roots 通过。

最终全仓库首轮未指定 `--basetemp`：exit 1，`505 passed, 1 skipped, 728 setup errors`；coverage 仅
37.77%。728 个 error 均为 pytest 在系统 `%TEMP%\pytest-of-21787` 扫描时触发 `WinError 5`，不是测试
断言失败。失败日志原样保留。随后只改变 pytest 临时根到仓库证据目录，full 与 coverage 均得到上述
完整通过终态，证明恢复路径针对的是宿主临时目录 ACL。

## 剩余风险

- Windows symlink 权限缺失分支尚需在具备权限或 POSIX 环境复跑；
- PDF 字节确定性只证明当前机器、字体与 ReportLab 环境，不证明跨机器精确一致；
- Phase 3E v4 与 Phase 4C 已在各自严格范围内完成；Phase 4A/4B 与 4D bind-only 仍未形成更广 Phase 4
  普通用户流程接受，也没有解冻真实目标操作或发布；
- Git 设置按用户决定暂时搁置，无 Git diff/commit 证据；
- R1 disposable fixture 的一次 Apply 与一次独立 Restore 已分别获精确授权并完成；任何后续 Apply/restore、
  VERSION、tag、Publish、Distribution 均未获授权。
