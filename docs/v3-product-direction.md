# ProjectTown v3 产品方向与开发章程

> 决策日期：2026-08-24  
> 状态：用户已批准的规范性产品方向；第 2、7 节明确区分当前实现与未来能力，也不替代 v1/v2
> 的历史验收事实。
>
> 2026-08-30 门槛修订：用户明确认为完整 T001–T010 真人 Study 过于冗长，并批准以 T001、T002
> 两轮代表性真人 Trial 作为范围受限的 Phase 2 收官门槛。该修订不重写旧十任务
> Study/Trial/Summary 合同，也不把跨 profile 证据描述为单一候选验收。

## 1. 权威性与适用范围

本文件是 ProjectTown v3 的规范性产品方向。它取代
[`v3-handoff-prompt.md`](v3-handoff-prompt.md) 中旧的“第六节 v3.0 已知缺口与建议顺序”及其
“建议 Sol 优先判断”的优先级建议；该 Prompt 中的历史事实、v1/v2 兼容护栏、秘密处理规则和
协作规则仍然有效。v1/v2 的已交付事实以
[`v2-closeout.md`](v2-closeout.md)、[`v2-handoff.md`](v2-handoff.md) 与
[`validation-v1.0.md`](validation-v1.0.md) 为准，不因本章程而被改写。

## 2. 当前事实、批准方向与未来实现

| 类别 | 内容 |
|---|---|
| 当前事实 | 现有系统是本地 Quest runtime；其 Godot、Quest、成果审核、离线 RAG 与 fixture-only MCP 边界见既有 v2 文档。Phase 0/1 另有隔离的离线 CLI。Phase 3C 已增加仅在 disposable fixtures 上工程验证的受控写入内核；Phase 3D 又增加默认关闭、native loopback-only 的预授权操作 Web UI/API。当前仍没有 Quest/数据库接线、浏览器 authorization 创建、Docker 3D 或任何真实目标写入授权。 |
| 已批准方向 | v3 定位为本地个人项目/研究资料工作区：用户在一个**本地工作区**中创建**任务**，并为任务显式选择**资料集**；系统基于真实本地文件给出可追溯建议。 |
| 未来实现 | 轻量本地 Web UI 成为主入口；Godot 小镇降为可选的状态/演示视图。任何 API、数据库、文件写入或外部连接仍须按后续获批的实现合同单独设计、实现和验收。 |

## 3. 已观察到的价值缺口与目标用户

当前能力偏向可审计运行时的技术展示，用户尚不能把自己选定的研究资料或项目文件直接组织成一次可复核的工作流。因此首要价值缺口是：从“可运行的 Quest”到“对本地真实资料产生可验证、可带走建议”的距离。

目标用户是单人、在本机整理研究材料或维护代码/文档项目的使用者。其 JTBD 是：**当我需要基于一组本地资料推进一个任务时，我想明确选择资料集，得到标明来源的计划、报告或 README 修改建议，先看差异，再自行下载或明确确认后应用，从而在不失去文件控制权的前提下节省整理与写作时间。**

## 4. 主流程与产品原则

主流程不超过五个主要动作：

1. 在本地工作区新建或打开任务。
2. 显式选择该任务的资料集（真实本地文件与允许的范围）。
3. 选择产物类型：计划、报告或 README 修改建议，并生成带来源依据的结果。
4. 预览建议、差异、来源、哈希和影响范围。
5. 导出/下载；未来如需写入，则由用户对已选目标再次明确确认后 Apply。

产品原则：资料集必须显式；输出必须 source-grounded（能追溯到选中资料）；建议与原文件分离；预览先于写入；默认离线、最小数据暴露；结果与任务可恢复；不把模型、检索或工具文本当作事实/Evidence；以实际用户节省的时间和可采纳结果衡量价值，而非技术栈数量。

这里的 **Apply** 有两个受控含义：一是导出/下载建议产物；二是在用户再次明确确认后，向其选定目标执行写入。后者必须有预览、当前 hash、范围守卫与目标路径确认。Phase 3C 只证明了 disposable fixtures 上的受控写入内核；任何真实目标仍须取得新的逐目标精确授权。

为消除后续执行语义的歧义，[Phase 3A–3E 开发蓝图](v3-phase-3.md) 将“创建新的导出/下载产物”统一
称为 **Export**，将 **Apply** 专指“在逐目标授权后修改用户选定目标”。Export 的成功不能推导
Apply 授权。

## 5. 能力取舍矩阵

| 决策 | v3 处理 | 原因与边界 |
|---|---|---|
| 保留 | v2 的本地/loopback、单用户、任务状态与恢复、Gateway/Sandbox、独立 Verifier/Evidence、hash-frozen preview、预算与审批护栏 | 仅在服务于本地资料工作流时复用；不扩大其既有承诺。 |
| 合并 | Quest 的可追溯/恢复思想与资料集、建议、预览流程 | 面向用户统一称为“本地工作区中的任务”，不强迫用户理解 runtime 内部术语。 |
| 冻结 | `/api/v3` 中超出预授权单目标 adapter 的任务/资料选择能力、migration 8、archive 扩展、Quest-bound RAG、provider registry/全局预算、真实 MCP、multi-agent/branching 主张、formal benchmark 扩展 | 在新的边界和价值得到批准前不实现、不宣称。 |
| 退居次要 | Godot 小镇 | 保留为可选状态/演示视图，不再是主产品入口。 |
| 不做 | 无授权的真实 provider、embedding、外部 MCP 或数据出境 | 不得以本章程视为 live-call 授权。 |

### Phase 4E 冻结的选择性边界

Phase 4E 冻结的是新的跨边界扩展，不是移除兼容保留的本地确定性功能：现有 `v1.rag` 仍可作为离线 material workflow 的确定性检索实现；legacy provider adapters/live evaluation 继续由其独立门禁控制而不接入 Phase 4 authoring；local MCP fixture 默认关闭且不等于 external MCP；legacy role routing/simulation 与 deterministic v1 benchmark 均为既有兼容能力，不能被表述为新的 Phase 4 multi-agent 或 benchmark 扩展；Phase 4 没有 scheduler。故 4E 冻结时不存在必须补齐的工程功能，且没有任何 4E option 被授权。任何未来新 variant 都须有独立需求证据、版本化提案、风险审查、默认关闭设计及针对最小运行的用户明确授权。

## 6. 架构边界与兼容规则

v3 的主产品边界是：本地 Web UI → 任务与显式资料集 → 本地受控读取/解析 → source-grounded 建议 → 预览/导出 →（未来）明确确认的受控写入。REST 对账为主，WebSocket 只在确有实时状态价值时作为可选增强；不得把 WebSocket 当作唯一事实来源。

- `/api/v3` 仅含默认关闭的 native loopback 预授权操作 adapter；不新增 migration 8，migration 1–7 不得改写。
- `/api/v1` 继续保留，直到确认不存在外部消费者；既有 `/api/v2` 语义不得静默改变。
- 未来持久化或协议变化只能以 additive migration 和版本化行为进行，并需旧数据库升级、replay、checkpoint 与恢复回归。
- 资料内容、输出与日志默认留在本机；路径选择、读取范围、写入范围、哈希、预览和审批均须可审计。符号链接/重解析点、越界路径、空文件与不支持格式必须失败关闭或明确提示。
- 任何 provider/egress 必须默认关闭。只有用户针对一次最小运行重新明确授权供应商、模型、预算、允许出境的数据、保留规则与目标后，才可进行一次真实调用；授权前只能离线模式。

## 7. 分阶段路线图

| 阶段 | 目的 | 可验收产物 | 不包含 |
|---|---|---|---|
| Phase 0：发现与边界（已完成，2026-08-24） | 用真实用户样本确认任务、资料集、产物和安全范围 | [`Phase 0 离线资料集发现基础`](v3-phase-0.md)、样本、成功标准、路径/格式策略与低保真流程 | 新 API、迁移、真实 provider。 |
| Phase 1：首个纵切（工程验收已通过；plan/PDF 价值仅获范围受限接受） | 完成离线、真实本地文件的建议闭环 | 选择 UTF-8 `.md`、`.txt`、`.json`、`.py` 资料，生成 source-grounded 计划/报告/README 建议、预览、导出/下载与恢复 | 直接 Apply、二进制解析、`/api/v3`、migration 8。 |
| Phase 2：可用性验证（2026-08-30 范围受限收官） | 用代表性真人结果决定是否开始下一步 | 旧十任务记录工具继续保留；新增两轮、跨 profile、create-only closeout receipt | 声称单一 candidate、v10、report 或 README 已获真人接受。 |
| Phase 3：受控写入（3A–3D 工程验证；3E v4 canonical chain 已完成） | 按 [Phase 3A–3E 开发蓝图](v3-phase-3.md) 从只读 preflight、可执行提案和单目标受控写入内核，推进到默认关闭的 loopback 预授权操作 UI，再完成两轮 Participant 实例 Study 与独立 User RC | 3A ApplyPlan、3B complete-post-image proposal、3C authorization/backup/ledger/replace/reconcile/独立 restore、3D versioned loopback binding/API/UI，以及 3E v4 Study→R1/R2→Summary→User RC `ACCEPT`；3E current state 为 `hold_for_version_gate` | 任何未授权真实目标写入、自动 Apply/Publish、VERSION、Git tag、Docker 3D、migration 8 或 Release/Distribution。 |
| Phase 4：本地工作区主入口与选择性扩展（4A/4B、4D bind-only 与 4C 已验证） | 先完成默认关闭的只读与离线 authoring/export，再逐项决定后续门禁 | [`Phase 4 路线`](v3-phase-4.md)、4A verified-task Workbench、4B opaque-source authoring/export、4C 对 Phase 3E v4 的只读 checkpoint（七项）、4D create-only verified handoff binding | 4D 的逐操作 Apply/restore 授权、VERSION/Git/Release/Distribution，以及 4E 新 provider/MCP/RAG/多代理等扩展解冻。 |

Phase 4A 已形成一个默认关闭、native loopback-only 的 Local Workspace Task Workbench 工程切片：它只
投影预注册且重新核验的 canonical Draft/Result、preview 与 citations；浏览器不接收文件系统路径，不生成
资料、不创建 authorization，也不执行 Apply/Restore/Publish。该切片尚未获得真人可用性接受，不能被描述
为完整普通用户工作流或 Phase 4 整体解冻。

Phase 4B 在不改变上述 Phase 4A 只读入口的前提下，新增默认关闭的本地候选 authoring 工程切片：用户只能
从服务器给出的 opaque catalog IDs 选择资料、提交任务/产物类型/受限 constraints、查看 Draft hash 与固定
确认短语、显式确认生成、预览并创建 create-only Markdown/PDF copy。它不接收路径、命令、generator 或任何
发布/写入授权输入；真实用户可用性、目标写入与更广 Phase 4 范围仍未获接受。详见
[`v3-phase-4b-local-workspace-task.md`](v3-phase-4b-local-workspace-task.md)。

当前 Phase 0 已完成，Phase 1 的隔离离线工程闭环已通过工程验收。2026-08-30，用户以参与者负担为
书面理由，将原十任务门槛修订为 T001/T002 两轮。冻结的 T001 v3 与 T002 v9 Trial 均为 retained、
无需结构性重写、引用可用且离线调用为零，因此 Phase 2 形成
`scope_limited_longitudinal_cross_profile_acceptance`。两条均为 plan 且来自不同 profile；这不验证
v10、report、README 或旧 Summary。Phase 3 当前已建立并验证 3A 的无写副作用 README
ApplyPlan/preflight，以及 3B 的版本化外部 `ExecutableProposal`。3B 记录内嵌 canonical Base64
complete post-image，重新绑定 before/after/scope/Result/plan/diff，并保持 `write_performed=false`。
3C 已新增版本化受控写入记录、外部 create-only ledger/backup、同目录 staged replace、
fail-closed reconcile 与独立授权 restore；3D 新增默认关闭、native loopback-only 的预授权操作
binding/idempotency records、API 和静态 UI；这些仅在新建 disposable fixtures 上工程验证。
真实目标仍为 `BLOCKED_PENDING_PER_TARGET_USER_AUTHORIZATION`。3E 的 v2/v3 Study 均作为历史证据
永久处于 `PROTOCOL_HOLD`；现行 `3E v4 additive Study/Round/Summary/User RC records` 已完成同一 Participant 的 R1/R2 实例评价、每轮 EngineeringAcceptanceV4 均独立为
`PASS`、Summary 与 User RC `ACCEPT`，终态为
`hold_for_version_gate`。工程验证、Participant 实例评价、EngineeringAcceptanceV4、Summary criteria、
User acceptance、VERSION 决定与 Git tag/公开发布授权逐级独立，互不替代，详见
[`v3-phase-3.md`](v3-phase-3.md) 与
[`v3-phase-0-4-acceptance-2026-09-01.md`](v3-phase-0-4-acceptance-2026-09-01.md)。

Phase 2 的最小工程增量是外部、create-only 的 Study→Trial→Summary records：Study 固定 T001–T010
和 artifact-kind 预注册并绑定 committed candidate manifest hash；后续 manifest drift 必须拒绝。Trial
只能由用户实际执行并评分后创建，Summary 只计算完整 records 的门槛，缺项时拒绝创建而非生成不完整结果。
它不保存任务/路径/资料内容或自由文本，不引入 DB/API/migration/Web/Apply/provider/RAG/MCP/network。合成
fixture 只证明工程合同；即使 Summary 数字满足，自动状态最多为
`criteria_met_unanchored_awaiting_user_acceptance`，仍需用户明确接受。详见
[`v3-phase-2.md`](v3-phase-2.md)。

## 8. 首个纵切与验收样本

首个纵切必须使用用户真实选定的本地文件：在一个本地工作区建立任务、显式资料集，生成带来源依据的计划、报告或 README 修改建议，展示建议与原文件的预览/差异，并允许导出/下载。仅根据任务 brief 生成的确定性文本可作为管线 smoke，但**不能**满足产品价值验收。直接 Apply 留给后续、明确确认的阶段。

首批资料格式固定为 UTF-8 `.md`、`.txt`、`.json` 与 `.py`。每条来源依据至少包含资料集内的相对路径、内容哈希以及可复核的行号或段落定位；绝对路径不得写入导出产物。单文件/资料集大小上限应在 Phase 0 依据解析性能和失败体验确定，但不得通过静默截断伪造完整分析。

| 样本/负例 | 必须验证的结果 |
|---|---|
| 中文研究材料 | 来源可定位，中文内容与引用/依据显示正确。 |
| Python 项目 README | 生成 README 修改建议与可读差异，不改写原文件。 |
| 冲突约束 | 明确暴露冲突、停在需用户决定处，不伪造结论。 |
| 不安全/越界路径 | 拒绝或隔离，不能读取/写入资料集外路径。 |
| 中断与重启恢复 | 恢复任务、资料集、已生成建议与预览状态，不重复产生副作用。 |
| Apply/review 前 hash 变化 | 检测变化、使旧预览失效并要求重新审阅；未来写入不得继续。 |
| 空文件/不支持文件 | 明确、可操作的错误或跳过说明，不把空内容当作来源。 |
| 无 provider 离线模式 | 完整离线运行且 provider/egress 调用为零。 |

重复次数按风险而不是口号确定：确定性核心在两个全新临时根目录各跑一次；恢复与每个失败用例各一次，失败后再做一次聚焦复跑；Web UI 出现后才对固定视觉场景做两次；真实 provider 评估只在取得新的明确授权后做一次最小批准运行。

## 9. 可衡量的价值门槛

### 每阶段测试样例工程门槛

每个 Phase 在声称“工程完成”前，必须先为该 Phase 生成并成功执行多种代表性测试样例。样例至少覆盖正向路径、负向路径，以及在存在可恢复状态或副作用时的恢复/中断路径；确定性核心必须在两个全新的证据目录各执行一次，每个失败用例在聚焦恢复检查后再执行一次。每条命令都要记录完成状态、exit code、通过/失败/skip 计数和新证据目录；仅启动、部分完成、复用旧目录或未报告结果的测试均不构成通过。

该工程门槛不降低本节的人工价值门槛。标为
`synthetic_engineering_fixture` 的样例只能证明工程合同；标为
`human_usability` 的真实候选任务必须由用户执行、评分和确认后才可形成价值证据。自动测试、模拟输出或技术样本不能替代用户对“无需结构性重写即可采用”的判断。

以下是 2026-08-24 确立的原始广泛价值样本门槛；旧十任务 Study/Summary 工具仍按它计算，不得
静默改写其 schema、hash domain 或历史记录：

- 完成 10 个使用真实资料的任务，覆盖计划、报告和 README 建议三类产物；每类至少 2 个任务。
- 至少 7/10 个产物由用户判断为“不需要结构性重写即可采用”，并实际导出或保留；自动测试不能替代这项人工价值判断。
- 用户能在不超过“选资料集 → 确认任务 → 生成 → 预览 → 导出”五个主要动作内完成主流程。
- 100% 来源引用都能回到所选资料的相对路径、内容哈希和行号/段落；越界读取与越界写入均为 0。
- 冲突约束、空/不支持输入、hash 改变与中断恢复均给出可操作结果，不产生未声明副作用。
- 离线验收中的 provider、embedding、MCP 与其他 egress 调用数均为 0，并分别报告。

Phase 0 可增加样本或提高门槛，但不得在没有用户明确同意和书面理由时降低上述初始门槛。用户已于
2026-08-30 以“完整 T001–T010 真人 Study 过于冗长”为理由明确批准两轮替代门槛。该修订先允许
范围受限地开始 Phase 3A preflight；用户随后又明确授权推进 Phase 3B proposal、Phase 3C
内核与 Phase 3D 首个 loopback 纵切的 disposable-fixture 开发。这些授权没有指定任何真实写入目标。
后续持续授权已允许默认关闭的 Phase 4A/4B 工程切片和不含目标写入的 4D bind-only handoff；其后
Phase 3E v4 与 Phase 4C 已按各自门禁完成，但这没有解冻 container 3D、任何 4D Apply/restore 操作、
VERSION/Git/Release/Distribution 或任何 4E 新扩展能力。
真实写入仍需新的逐目标授权、写前 identity/hash/mode 二次核验、备份、同目录原子
replace、receipt 与恢复合同；该本地单 writer 方案不得宣称能对非合作进程提供严格 CAS。

## 10. 数据、安全、回滚与开放决策

数据与安全：资料集仅包含用户显式选择的路径；显示来源时避免泄露未选择文件；导出默认创建新产物而非覆盖来源；未来 Apply 仅能写入用户选定目标，并在写入前校验 preview/hash/scope guard 和二次确认。敏感资料分类、保留期限、支持格式、最大大小、解析器隔离以及结果是否保存，均须先形成可测试的本地策略。

回滚：每个阶段保持功能开关或可移除的独立入口；若新能力不可用，回退到只读资料集、已生成预览或纯离线模式，不删除用户来源文件。涉及数据库时仅用 additive migration；数据级回退通过停服恢复已验证完整备份，绝不手工删除 migration 或改写 migration 1–7。

尚待 Sol 与用户决定：外部 ledger/backup/receipt 的保留与清理期限；是否以及何时引入 OS-backed
身份或签名来认证真实目标授权；Windows ACL/owner/xattr 等是否需要新版元数据协议；Web UI 的最小交互；
冲突消解体验；真实 Apply 目标选择；以及达到整体价值门槛后，何种单项证据足以解冻每一项 Phase 4 能力。

## 11. 后续交付报告模板

每次 v3 交付均应报告：

```text
目标与本章程对应阶段：
用户样本与显式资料集范围：
当前已实现 / 未实现（避免把目标写成事实）：
变更路径与未触及范围：
来源依据、预览、hash 与写入/导出行为：
API、数据库、恢复、兼容、安全、Godot 影响：
provider/egress 授权状态与实际调用数：
验证命令、退出码、通过/失败计数与证据路径：
价值门槛数据与结论：
回滚方法、剩余风险与需要用户决定的事项：
Sol 复核与最终验收结论：
```
