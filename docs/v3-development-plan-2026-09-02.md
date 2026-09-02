# ProjectTown v3 当前开发方案（2026-09-02）

> 状态：**计划与当前状态记录，不是授权**。本文件将 Phase 0–4 收官后的工作拆为可执行的门禁工作包；它不替代既有规范、历史验收记录或 create-only canonical evidence，也不授予版本变更、tag、发布、真实写入、网络或外部能力权限。

## 1. 文档权威性、范围与状态语言

本文件的工作范围是整理当前代码和文档所显示的后续开发顺序。规范性产品方向以 [v3 产品方向与开发章程](v3-product-direction.md) 为准；Phase 3/3E/4 的协议和门禁以 [Phase 3 开发蓝图](v3-phase-3.md)、[Phase 3E Release Candidate](v3-phase-3e.md) 和 [Phase 4 路线](v3-phase-4.md) 为准；历史工程验收以 [Phase 0–4 验收记录](v3-phase-0-4-acceptance-2026-09-01.md) 为准。它们均不被本计划改写。

本文统一使用以下状态分类，避免把计划或旧日志写成当前完成事实：

| 状态 | 含义 | 本文用法 |
|---|---|---|
| **已核验当前（verified-current）** | 2026-09-02 对当前工作树或当前 canonical status 的直接观察 | 明确标注观察范围、命令和局限；不外推为全量回归。 |
| **历史（historical）** | 已有文档记录的先前验收、测试或证据 | 保留其原始日期和范围，不称为当前测试结果。 |
| **计划（planned）** | 尚未获得实现授权的候选工作 | 只描述前置条件、最小改动、验证和回滚。 |
| **需要用户决策（user-decision-required）** | 会改变发布身份、公开范围或跨边界能力的选择 | 在得到明确授权前不得实施。 |

## 2. 已核验当前快照

### 2.1 Phase 0–4 与发布身份

| 区域 | 当前状态 | 结论边界 |
|---|---|---|
| Phase 0、1、2 | 历史验收记录存在；其具体样本、计数和范围见 [验收记录](v3-phase-0-4-acceptance-2026-09-01.md) | 历史结果不是本次重跑结果。 |
| Phase 3A–3D | 3A 只读 preflight、3B create-only proposal、3C disposable-fixture 内核、3D default-off/native-loopback 预授权 binding 均保留 | 不授权真实目标 Apply/Restore，亦不产生浏览器 authorization。 |
| Phase 3E v4 | 当前 Sol 观察为 `blocker_count=0`、`next_action=hold_for_version_gate`；同一 Participant 完成 R1/R2、每轮 `EngineeringAcceptanceV4=PASS`、Summary 与 User RC `ACCEPT` 记录齐全，offline counters 均为 0，status exit 0 | 此状态不授权 VERSION 变更、tag、Apply、Restore、Publish 或 Distribution。 |
| Phase 4A/4B/4C/4D | 4A 只读 Workbench、4B offline create-only authoring/export、4C 只读 checkpoint、4D bind-only handoff 均已完成并通过相应工程验证 | 4D 不等于逐操作 Apply/Restore；4E 保持冻结。 |
| 版本身份 | [VERSION](../VERSION) 为 `1.0.0`；后端默认值、Compose defaults、README、Godot 标题/烟测仍与之关联 | 这是当前身份，不表示 v3 `3.0.0` 已获批准。 |
| Git / Distribution | Git 已配置且 `origin/main` 存在；当前 hosted CI 与两次 Windows visual runs 均为绿色；无 LICENSE、tag、发布或分发授权 | Git/CI 状态不替代独立的用户决策门禁。 |

### 2.2 代码能力与安全边界

- [Settings](../backend/app/config.py) 的 `enable_local_mcp`、`enable_v3_loopback_ui`、`enable_local_workspace_task`、`enable_local_workspace_task_create` 默认均为关闭；默认 API 前缀保留 `/api/v1` 与 `/api/v2`。
- [应用装配](../backend/app/main.py) 仅在相应开关开启时注册 v3 loopback 和 local-workspace routes，因而默认运行不会装配 Phase 4 UI/API。
- [Compose](../docker-compose.yml) 仍是 loopback 端口、只读根文件系统、最小 capability 的本地部署配置；[Compose 校验默认值](../config/compose-validation.defaults) 仍为 `1.0.0`。
- [Godot 客户端说明](../godot/README.md) 和 [API smoke](../godot/tests/api_smoke.gd) 仍含 v1.0 版本身份；Godot 是可选视图，不是 v3 主入口。
- Phase 4 authoring 的 provider、embedding、external MCP、network/egress、paid API 零调用约束是代码/合同可观察计数；它证明受检路径未调用这些能力，**不是**操作系统级网络隔离证明。

### 2.3 本次观察的验证边界

本次 Sol 已观察到：Phase 3E 状态检查 exit 0；相对交接文本中的旧哈希，三份阶段规范保持不变，`v3-product-direction.md` 与 `v3-phase-0-4-acceptance-2026-09-01.md` 因已提交的 `d3e3fbd` v4/4D 说明更新而发生经核验的哈希漂移；Ruff (`backend/scripts/tests`)、`compileall` (`backend/scripts`) 和 `pip check` exit 0；独立 loopback 聚焦套件 `35 passed`。本工作包将陈旧的文档/合同语义对齐为已核验的 v4 与 4D 状态；其当前测试结果必须以本次命令输出为准。

因此，本文**不得**把历史的全仓 `1266 passed, 18 skipped` 或任何旧验收计数称为当前全量通过。完整历史计数、证据根和适用边界仅见 [验收记录](v3-phase-0-4-acceptance-2026-09-01.md)。

## 3. 不可变边界与非目标

1. 不改写 migration 1–7；任何未来持久化变化必须 additive，并验证旧数据库升级、replay、checkpoint 与恢复。
2. 保持 `/api/v1` 兼容接口及 `/api/v2` 运行时语义；不得因 v3 版本身份更新而机械改名、删改或静默改变其协议。
3. 保留历史 v1/v2 API、schema、fixture、目录、基准名称和证据名称。例如 `formal-v1.0`、`v1.rag`、`/api/v1`、`/api/v2`、`v0.1 compatibility API`、历史 manifest/hash domain 都是协议或历史标识，**不得**做全局字符串替换。
4. Phase 3E v2/v3 为永久只读 `PROTOCOL_HOLD`；v4 canonical chain 是 create-only 证据，不能重写、删除或用新记录替代。
5. 不读取或写入 `.secrets/**`、`.env*` 或外部 canonical evidence；不调用 provider、embedding、external MCP、网络/egress 或付费 API。
6. 不实现、解冻或暗示 Phase 4E；不实现真实目标 Apply/Restore，不创建 target authorization，不发布、不打 tag，也不变更现有 Git 远程、分支或历史。
7. 不以 Phase 4C 通过、Participant `RETAIN`、EngineeringAcceptanceV4 `PASS` 或 User RC `ACCEPT` 互相替代；它们与 VERSION 和 Distribution Gate 逐级独立。

## 4. 已观察缺口与处理原则

| 缺口 | 影响 | 正确处理 |
|---|---|---|
| README 的 3E/4C 摘要 | 已与 v4 Participant-only canonical state 对齐 | A 已保留 VERSION、Distribution 与真实写入边界。 |
| [v3-phase-3.md](v3-phase-3.md) 的 4D 摘要 | 已与 bind-only 4D 代码及 [Phase 4 路线](v3-phase-4.md) 对齐 | A 已明确逐操作 Apply/Restore 仍未授权。 |
| selective-extension 合同 | 已验证 v4/4D/4E 的语义性不变量 | A 不依赖陈旧精确短语，也不向文档填充短语以迎合测试。 |
| Windows symlink/reparse fixture skip | 该平台路径尚未被实测 | 保留 skip，不把 skip 记为通过；可在具备权限的 Windows 环境独立补验。 |
| repo-wide `ruff format --check .` 有历史格式债 | 全仓格式门禁不绿 | 隔离记录，未获明确授权不格式化历史备份或无关文件。 |
| offline-counter 可观察性有限 | 无法证明 OS 防火墙级 egress 阻断 | 继续如实报告为受检路径的计数合同，不扩展结论。 |
| Phase 3E 协议模块规模大且冻结 | VERSION 前重构可能破坏 hash/protocol | 不在收官修复中重构；未来拆分需独立提案、兼容和 evidence 计划。 |

## 5. 严格顺序的工作包

### A. 收官一致性修复（已授权，Sol 已接受）

**目标。** 使用户入口、Phase 3 描述和 Phase 4 文档合同与已核验的 v4 Participant-only/4D bind-only 状态一致，同时不改变任何 canonical evidence 或产品行为。

**前置条件。** 用户已明确授权仅作一致性文档与测试合同修复；current canonical status 为 `hold_for_version_gate`；开始前已记录 [README](../README.md)、[Phase 3](v3-phase-3.md)、本计划与测试文件的状态。

**精确候选路径。** `README.md`、`docs/v3-phase-3.md`、`docs/v3-development-plan-2026-09-02.md`、`tests/contract/test_phase4_selective_extensions.py`。不得改动这四个候选路径以外的任何文件；不得改动 `docs/v3-phase-4.md`、Phase 3E 模块、VERSION、记录或 evidence root。

**步骤。**

1. README 已改为 v4 同一 Participant R1/R2、每轮独立 EngineeringAcceptanceV4、Summary 和 User RC `ACCEPT` 已完成，且仍等待 VERSION Gate。
2. Phase 3 已改为“4D bind-only handoff 已完成”；逐操作 Apply/Restore、真实目标和发布仍未授权。
3. 陈旧测试已改为对 v4 Participant-only、EngineeringAcceptanceV4、无独立 Reviewer/第二 Study、4D/4E/版本门禁未被越权的语义断言。
4. 已重新检查四个文件之间的术语一致性，未变更 canonical docs 的事实边界。

**非目标。** 不更新版本号；不修复全仓格式债；不补 Windows symlink；不创建/运行新 3E record；不修改运行时代码或 4D 行为。

**完成标准。** 陈旧描述均已准确修正；测试不再依赖不存在的精确短语；受影响的文档与测试在两个 fresh roots 均通过，并由 Sol 审核接受；所有改动均限于四个候选路径。

**本次验证与证据根。** 本工作包使用两个独立根执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=sandbox/tmp/t03-final-a tests/contract/test_phase4_selective_extensions.py tests/contract/test_release_artifacts.py
.\.venv\Scripts\python.exe -m pytest -q --basetemp=sandbox/tmp/t03-final-b tests/contract/test_phase4_selective_extensions.py tests/contract/test_release_artifacts.py
git diff --check
```

两个命令均 exit 0，各 `12 passed, 0 failed, 0 skipped`；`git diff --check` exit 0。这只验证本工作包的文档/合同语义；它不替代 T04 的完整回归或历史验收。

**回滚。** 若任一修复不符合语义或验证失败，只回退这四个文件中的本工作包 hunks 至开始前记录的内容；不删除或改写任何外部/canonical record。

**决策所有者。** 用户授权范围；Sol 审核 diff、验证证据并接受；Terra 仅执行已签发的最小路径合同。

### B. VERSION Gate 决策包（需要用户决策）

**目标。** 形成让用户能够明确批准或拒绝版本身份变更的决策包，而非直接改版本。

**前置条件。** 工作包 A 已接受，且当前 status 仍为 `hold_for_version_gate`。用户须先确认版本语义、兼容承诺和是否授权 C。

**精确候选路径。** 只读梳理候选：`VERSION`、`README.md`、`backend/app/config.py`、`backend/app/main.py`、`docker-compose.yml`、`config/compose-validation.defaults`、`godot/README.md`、`godot/scripts/main.gd`、`godot/tests/api_smoke.gd`、`tests/contract/test_release_artifacts.py`，以及这些文件的现有引用测试。决策包本身应置于**用户另行指定并授权**的路径；本计划不创建它。

**步骤。**

1. 给出候选版本 **`3.0.0`** 的语义理由、兼容影响和替代方案。`3.0.0` 仅是推荐/决策输入，绝非已批准版本。
2. 建立逐文件身份替换清单，并逐项标记“产品发布身份”或“不得替换的历史/协议标识”。
3. 明确 `/api/v1`、`/api/v2`、migration 1–7、历史 schema/hash/manifest、`formal-v1.0` benchmark 路径、v1/v2 test/fixture 名称必须保留。
4. 说明现有 Git `origin/main` 基线下版本身份的可审计记录方法、证据清单、SHA-256、回滚内容及未解决的 LICENSE/公开范围。
5. 请求用户以明确版本值、允许路径、验证范围和“仅身份更新、不 tag/不发布”形式批准或拒绝。

**非目标。** 不编辑版本文件、不新建或重新关联 Git 远程、不创建 tag、不选许可证、不发布、不变更 API、schema 或 benchmark。

**完成标准。** 用户收到可审计的候选路径清单、不可替换清单、测试计划、风险和明确批准问题；未获批准时停在 Gate。

**验证。** 对候选路径执行只读搜索，确认每个 `1.0.0` 的上下文归类；对 `formal-v1.0`、`/api/v1`、`/api/v2`、migration、schema、manifest、hash domain 执行反向搜索，确认机械替换会触及的风险面。不得将搜索结果说成修改已完成。

```powershell
rg -n "1\.0\.0|v1\.0|3\.0\.0|formal-v1\.0|/api/v1|/api/v2|migration [1-7]|hash domain|schema" VERSION README.md backend/app/config.py backend/app/main.py docker-compose.yml config/compose-validation.defaults godot/README.md godot/scripts/main.gd godot/tests/api_smoke.gd tests/contract/test_release_artifacts.py
```

**回滚。** N/A（仅决策材料）；若生成草案，仅删除经授权创建的草案，不触碰现有文件。

**决策所有者。** 用户是版本值和授权所有者；Sol 形成并审查决策包。

### C. 获批的版本身份更新（计划，必须在 B 后）

**目标。** 在用户明确批准的版本值与路径范围内，原子地更新产品发布身份，并证明兼容协议未被误替换。

**前置条件。** 用户书面批准精确版本值（候选可为 `3.0.0`，但不能假定）、精确允许路径、B 的分类清单、A 的接受结果和完整回滚方式；仍无 Git/tag/publish 授权。

**精确候选路径。** `VERSION`、`README.md`、`backend/app/config.py`、`backend/app/main.py`、`docker-compose.yml`、`config/compose-validation.defaults`、`godot/README.md`、`godot/scripts/main.gd`、`godot/tests/api_smoke.gd`、`tests/contract/test_release_artifacts.py`。若反向搜索发现其他产品身份位置，必须停下请求扩展授权，不得自行扩大范围。

**步骤。**

1. 先保存每个批准文件的 SHA-256、版本位置与协议保留清单。
2. 仅替换经分类为“产品发布身份”的值；保持历史 benchmark、API/schema/migration/fixture/hash 名称原样。
3. 同步更新 release-artifact contract 与 Godot health smoke 的批准版本断言。
4. 生成人工可读的版本变更表、文件哈希和验证日志；不生成 tag、不推送、不发布。

**非目标。** 不改变 API prefix、数据库 schema、Compose 安全默认值、4D/4E 状态、Git、LICENSE 或 Distribution；不“顺手”格式化无关文件。

**完成标准。** 所有批准的身份源一致；受影响 contract/smoke/文档通过；反向检查证明受保护 v1/v2 API/schema/benchmark 标识未被替换；变更与回滚清单完整。

**精确验证与证据根。** 使用两个新鲜根各跑一次受影响测试，随后再跑完整回归：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q --basetemp=sandbox/tmp/version-a-YYYYMMDDHHMMSS tests/contract/test_release_artifacts.py
& '.\.venv\Scripts\python.exe' -m pytest -q --basetemp=sandbox/tmp/version-b-YYYYMMDDHHMMSS tests/contract/test_release_artifacts.py
& '.\.venv\Scripts\ruff.exe' check backend scripts tests
& '.\.venv\Scripts\python.exe' -m compileall -q backend scripts
& '.\.venv\Scripts\python.exe' -m pip check
& '.\.venv\Scripts\python.exe' -m pytest -q --basetemp=sandbox/tmp/version-full-YYYYMMDDHHMMSS
```

并复用 B 的精确 `rg` 检索覆盖批准路径和相关合同，人工核对没有机械替换。若实际环境可运行 Godot，再在新日志根运行现有 `scripts/validate_godot_v1.ps1`；不可运行时如实报告为未核验，不能称通过。

**回滚。** 以批准前逐文件备份/哈希恢复**仅** C 的批准路径，重跑 release-artifact contract；不通过 destructive Git 命令回滚。

**决策所有者。** 用户批准版本值与范围；Sol 接受；Terra 仅在精确合同内修改。

### D. Distribution Gate（需要用户决策）

**目标。** 在版本身份稳定后，单独决定项目的公开范围、许可证、版本控制与发布渠道。

**前置条件。** C 被接受；用户明确选择许可证、公开材料范围、Git 目标、首发渠道、tag/发布权限和撤回策略。

**精确候选路径。** 待用户决策后才确定；可能涉及 `LICENSE`、`.gitignore`、README 发布说明、CI/release workflow、Git 元数据及外部发布目标。由于这些路径/系统尚未被授权，当前不得创建、修改或访问。

**步骤。**

1. 先做只读 release inventory：秘密排除、第三方 notices、许可证兼容性、公开示例、构建产物和撤回面。
2. 由用户批准许可证与公开范围后，单独签发 Git 分支/提交、tag、包/镜像或渠道发布的独立合同。
3. 每个外部动作都需有发布前核对、目标确认、发布后验证与可执行撤回步骤。

**非目标。** VERSION Gate 不包含 Distribution；不得因为 C 完成就创建 Git、tag、LICENSE 或公开任何内容。

**完成标准。** 只有所有外部目标、授权、验证和撤回方案得到用户批准并逐项完成后，Distribution 才可声称完成。

**验证。** 由被批准的目标渠道定义；至少保存本地 release inventory、授权记录、构建/校验和、发布后可访问性与撤回演练（若适用）。未发生外部发布时状态保持 `user-decision-required`。

**回滚。** 用户批准的渠道级撤回/撤销方案；本地仅撤销 Distribution 合同新增的文件或提交，绝不删除历史 evidence。

**决策所有者。** 用户。

### E. 未来产品工作（冻结，须独立提案）

**目标。** 在不把现有兼容能力误说成新功能的前提下，为未来 Phase 4E 或真实目标写入建立正确的提案入口。

**前置条件。** 每一个候选能力都需要独立的真实需求证据、最小范围、风险评审、默认关闭设计、数据/egress 说明、验收样本和用户明确授权。

**精确候选路径。** 当前无可写路径。未来提案必须先列出独立的代码、测试、文档、迁移和 evidence 路径，再获得限定合同。

**步骤。** 对每一个选项（新的 RAG variant、provider、external MCP、多代理、scheduler、benchmark 扩展、真实 Apply/Restore）单独提交：问题与用户价值、数据流、授权模型、fail-closed 路径、兼容/migration 方案、正向/负向/恢复样本、两个新鲜 evidence roots 和回滚。

**非目标。** 不解冻 4E；不把 retained `v1.rag`、legacy provider adapter、fixture-only local MCP、legacy routing/simulation 或 deterministic v1 benchmark 描述为已授权的新 Phase 4 扩展；不实施真实目标写入。

**完成标准。** 未经独立提案与用户授权，E 始终冻结；获批的最小单项才可成为新的工作包。

**验证。** 提案评审先于实现；实现后必须在两个新鲜证据根跑正向、负向和恢复样本，并按能力额外验证零调用/最小授权/回滚。真实 provider 或 egress 不可用离线测试替代授权。

**回滚。** 每项提案必须先给出可验证的代码、数据和外部副作用回滚；没有该方案不得开始。

**决策所有者。** 用户；涉及跨子系统、迁移、协议或不可逆风险时由 Sol 进行高风险决策审查。

## 6. 依赖与门禁流

```text
当前：3E v4 / 4C / 4D bind-only 已核验当前，next_action=hold_for_version_gate
  |
  +--> A 收官一致性修复（已授权，实施完成，待 Sol 接受）
  |
  +--> Sol 接受 A 后的 T04 工程验收
         |
         +--> B VERSION Gate 决策包（只读；用户决定）
         |
         +-- 用户拒绝/暂缓 --> 保持 hold_for_version_gate
         |
         +-- 用户批准精确版本和范围 --> C 仅版本身份更新
                                              |
                                              +--> 新 hash/evidence + Distribution Gate
                                                                         |
                                                                         +-- 用户另行批准 --> D

E = 独立提案队列；不由 A–D 自动触发。
```

## 7. 验证矩阵

| 验证对象 | 最低检查 | 通过证据 | 不能证明 |
|---|---|---|---|
| A 文档/合同一致性 | 两 fresh roots 的 Phase 4/release contract；Ruff/compileall/pip check；当前全量 pytest | 实际 exit、pass/fail/skip、根路径、日志、哈希 | OS 级 egress 隔离、Windows reparse 行为。 |
| B 决策包 | 候选版本引用的逐处分类与受保护名称反向搜索 | 用户可审阅的路径/风险/授权问题 | 版本已变更或可发布。 |
| C 版本身份 | 两 fresh roots 的 release contract、静态检查、当前全量 pytest、可运行时 Godot smoke | 一致版本源、未变受保护标识、SHA-256 | Git/tag/公开发布、4E 解冻。 |
| D Distribution | 用户批准的渠道检查、构建校验和、发布后验证、撤回方案 | 明确外部动作及结果 | 未批准渠道的安全/合规性。 |
| E 单项未来能力 | 独立批准的正向/负向/恢复样本、双 fresh roots、授权/零调用检查 | 该单项的最小合同 | 对其他冻结能力的授权。 |

Windows symlink/reparse skip 必须单列为 `skipped`，不可计入通过。每个工作包的确定性核心需要两个全新证据根；失败用例在针对性恢复后还应重跑一次。测试启动、旧根复用或未报告 exit code 均不构成验收证据。

## 8. 风险登记

| 风险 | 触发信号 | 缓解 / 停止条件 | 所有者 |
|---|---|---|---|
| 文档修复被误作协议重写 | 要修改 record/hash/schema 或授权语句 | 停止 A，保持 canonical evidence 不变 | Sol / 用户 |
| 版本机械替换破坏兼容 | `v1`、`v2`、benchmark、schema 或 API 名称被无分类替换 | B 的反向搜索与 C 的逐处审批；范围外即停 | Sol |
| 当前测试被历史绿灯掩盖 | 报告中出现旧全量计数作为当前结果 | 每次报告写日期、命令、exit 和实际计数 | Sol |
| Windows 路径安全被高估 | symlink/reparse 仍 skip | 标为未核验，转独立 Windows 权限验证 | 用户 / Sol |
| egress 结论过强 | 仅有零计数就宣称无网络 | 限定为受检路径观测，不声称 OS 隔离 | Sol |
| 冻结协议重构 | VERSION 前请求整理 3E 大模块 | 需独立提案、兼容测试和 evidence 迁移计划 | 用户 / Sol |
| 发布动作越过门禁 | Git/tag/LICENSE/渠道行动被建议为随 C 执行 | D 独立授权，未批准即不行动 | 用户 |

## 9. 决策登记与精确下一动作

| 编号 | 决策 | 当前状态 | 所需决定者 |
|---|---|---|---|
| D-01 | A 的四文件一致性修复 | 已授权，Sol 已接受 | 用户 / Sol |
| D-02 | VERSION 是否变更；若变更，具体值为何 | 待决定；`3.0.0` 只是推荐输入 | 用户 |
| D-03 | C 的精确允许路径及是否要求 Godot live smoke | 待决定 | 用户 |
| D-04 | LICENSE、Git、tag、公开渠道与撤回策略 | 未开始，独立 Distribution Gate | 用户 |
| D-05 | 4E 任一能力或真实 Apply/Restore | 冻结；须单独提案 | 用户 |

**精确下一动作：** Sol 接受 A 后执行 T04 工程验收；在 T04 完成前保持 `hold_for_version_gate`。不得因为 Git/CI 已配置或 A 实施完成而修改 VERSION、创建 tag、选择 LICENSE、发布或授权真实 Apply/Restore。

## 10. 后续交接模板

```text
工作包与授权编号：
状态分类（已核验当前 / 历史 / 计划 / 需要用户决策）：
目标与非目标：
允许读写路径；明确未触及路径：
当前 canonical / VERSION / Gate 状态：
实际变更及逐文件 SHA-256：
保留的 v1/v2 API、schema、benchmark、migration、hash 标识：
命令、fresh evidence root、完成 exit code、pass/fail/skip：
provider / embedding / MCP / network / paid 调用观察：
Windows symlink/reparse 与其他未核验项：
回滚方式及是否已演练：
需要用户的下一个明确决定：
```

## 11. 相关资料

- [v3 产品方向与开发章程](v3-product-direction.md)
- [Phase 3 开发蓝图](v3-phase-3.md)
- [Phase 3E Release Candidate](v3-phase-3e.md)
- [Phase 4 路线](v3-phase-4.md)
- [Phase 0–4 工程验收记录](v3-phase-0-4-acceptance-2026-09-01.md)
- [当前代码审查快照（2026-08-31）](v3-current-code-audit-2026-08-31.md)
- [Release artifact contract](../tests/contract/test_release_artifacts.py)
- [Phase 4 selective-extension contract](../tests/contract/test_phase4_selective_extensions.py)
