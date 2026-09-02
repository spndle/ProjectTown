# v3 Phase 1：离线资料建议纵切

> 状态：离线工程纵切已通过工程验收。用户于 2026-08-30 将完整十任务门槛明确修订为 T001/T002
> 两轮；两条 retained plan/PDF 证据只形成跨 profile、范围受限接受，不验证 report/README。
> Phase 3A 只读 ApplyPlan/preflight 已开始，直接 Apply 仍未实现或授权。

> Phase 2 的外部 Study/Trial/Summary record tooling 已通过离线工程验收；它不会把 Phase 1 fixture、
> preview、export 或自动测试变成真人采纳。它绑定固定 candidate manifest hash，并将 records 保持为
> external self-consistent、unanchored。见 [`v3-phase-2.md`](v3-phase-2.md)。

## 1. 目标与边界

Phase 1 为单人本地资料工作流提供一个可移除的离线纵切：用户显式选择 UTF-8
`.md`、`.txt`、`.json`、`.py` 文件，创建待确认合同，再生成带来源定位的计划、报告或
README 修改建议，预览冻结结果并导出为新文件。

本阶段不接入 Web UI、Quest、REST API、数据库或 migration 8；不调用 provider、embedding、
外部 MCP 或网络；不实现直接 Apply，也不覆盖任何来源文件。Godot、Docker、`/api/v1`、
`/api/v2`、migration 1–7 及 v2 的 Evidence/恢复语义均不改变。

## 2. 五步命令流

入口为 `scripts/run_v3_material_workflow.py`，所有会话和导出目标都必须是资料根之外、尚不存在的
绝对规范路径。以下 `$root` 与 `$outside` 仅为 PowerShell 示例变量：

```powershell
$root = (Resolve-Path examples\v3-phase-1\research-plan-cn).Path
$outside = (Resolve-Path sandbox\tmp).Path

.\.venv\Scripts\python.exe scripts\run_v3_material_workflow.py draft `
  --root $root `
  --file brief.md --file methods.txt --file milestones.json `
  --task "制定中文田野访谈研究计划，优先安排样本招募和匿名化检查。" `
  --artifact-kind plan `
  --constraint execution=offline `
  --draft-out (Join-Path $outside "phase1-draft.json")
```

`draft` 的 JSON 状态会给出 `contract_hash`。用户必须在另一次命令中显式提供该值；CLI 不会
自动读取、复制或默认确认：

```powershell
.\.venv\Scripts\python.exe scripts\run_v3_material_workflow.py generate `
  --root $root `
  --draft (Join-Path $outside "phase1-draft.json") `
  --confirmation-hash "<上一步显示的 64 位哈希>" `
  --result-out (Join-Path $outside "phase1-result.json")

.\.venv\Scripts\python.exe scripts\run_v3_material_workflow.py check `
  --root $root --session (Join-Path $outside "phase1-result.json")

.\.venv\Scripts\python.exe scripts\run_v3_material_workflow.py preview `
  --root $root --result (Join-Path $outside "phase1-result.json")

.\.venv\Scripts\python.exe scripts\run_v3_material_workflow.py export `
  --root $root --result (Join-Path $outside "phase1-result.json") `
  --export-out (Join-Path $outside "phase1-plan.md")
```

`preview` 省略 `--root` 时只恢复并展示已冻结结果，并明确报告 freshness 未检查；带 `--root`
时会重新读取当前显式资料集并验证 freshness。`export` 始终要求当前资料仍匹配已确认合同，且
不存在未解决的结构化冲突。

CLI 把规范 JSON、哈希、lineage、结构关系和确定性产物重算全部通过报告为
`integrity=self_consistent`，而不是“已认证”。`generate` 只对当前调用报告
`confirmation_provenance=explicit_current_invocation`；从外部文件执行 `check`、`preview` 或
`export` 时报告 `unanchored_external_session`。

### 用户可读 PDF（版本化候选）

为回应 T001 真人反馈，当前生成的新 Draft 默认冻结为
`deterministic-grounded-plan-v2` / `segmented-deterministic-rag-v2` /
`markdown-block-v2`。其中 `plan` 以执行摘要、流程、分阶段事项、交付物、验收标准、依赖与精确短引用
组织；它不是 JSON 或“逐个查看来源”的清单。旧的
`deterministic-grounded-template-v1` / `segmented-deterministic-rag-v1` /
`utf8-raw-240k-v1` 仍按其原有生成器重算和验证，不能让 v2 模板重算旧 Result。

`preview` 是普通用户的文本阅读面；Result JSON 只承载冻结会话、lineage 和 hash。新增的 PDF 是派生
产物，不写回 Result，也不改变原有 Markdown `export` 的字节语义：

```powershell
.\.venv\Scripts\python.exe scripts\run_v3_material_workflow.py pdf-export `
  --root $root --result (Join-Path $outside "phase1-result.json") `
  --pdf-out (Join-Path $outside "phase1-plan.pdf")
```

`pdf-export` 要求资料仍 fresh、没有未解决冲突且目标路径尚不存在；失败时保持 fail-closed，不覆盖来源、
旧 export 或旧 Study。它使用离线 ReportLab 4.x 和仓库内 Fusion 中文字体（OFL-1.1）；`pypdf` 6.x
仅用于开发/测试中的 reopen、文本提取与渲染检查。PDF 明确标示其为 deterministic、离线的冻结会话
派生结果，provider、embedding、MCP 和网络调用均为零；它不是模型结论，更不是产品接受。

详见 [`v3-t001-pdf-product-fix.md`](v3-t001-pdf-product-fix.md)。

### PDF 视觉候选 v2（待重新进行真人评价）

`v3-material-pdf-export-v1` / `projecttown-reportlab-pdf-v1` 是已冻结的文字优先呈现对；
不可改写、重算为新字节，或与已存在的 v2 真人 Trial 混用。视觉优化候选使用明确的新呈现对
`v3-material-pdf-export-v2` / `projecttown-reportlab-pdf-v2`。它只改变由同一冻结 Result 派生的 PDF
呈现，不改变 material Result 的 schema、generator、artifact、preview 或 Markdown export。

v2 呈现以离线、确定性的 ReportLab 向量元素表达计划：流程图、阶段/里程碑卡片、P0/P1/P2 标签、
行动/交付物/验收标准的稳定样式，以及依赖、阻断项、决策点和未知项的信息框。颜色从不是唯一信号：
每个状态同时带文字标签和形状/图标。它不需要也不会调用文生图、provider、embedding、MCP 或网络；
若将来需要 image provider，必须先取得单独的架构、隐私、成本、离线降级与密钥管理授权。

PDF 继续是 create-only 的资料根外新文件；freshness、冲突、非规范路径或已存在目标仍会失败关闭。工程
生成、文本提取、渲染与视觉检查只能证明候选的工程性质，不能推断真人采用。真人复评必须使用独立
`projecttown-human-pdf-v3` Study 与其 v3 manifest，且不得改动任何旧 Study、Trial、Result 或 PDF。
执行说明见 [`v3-t001-pdf-visual-study-handoff.md`](v3-t001-pdf-visual-study-handoff.md)。

## 3. 资料与会话合同

资料策略继承 Phase 0 的 `v3-material-set-v1`：最多 100 个文件，单文件最多 1 MiB，合计最多
10 MiB；不支持的后缀、无效 UTF-8、空白内容、越界路径、重解析点、不稳定读取和硬链接均失败
关闭。结果不会静默省略资料；coverage 明确记录读取、索引、引用、不可检索及未引用来源。

Draft 与 Result 都是严格、规范 JSON 的冻结会话，最大 2 MiB。解析拒绝重复键、非有限数、
未知字段、过深嵌套、非规范字节、错误 lineage 及不一致的合同/会话/产物/预览哈希。结果会话
保存结构化任务、manifest、分段与引用摘要，不保存整个资料集内容，也不保存绝对资料根。

这里的“冻结”和 `self_consistent` 只表示应用不会原地修改会话，并能发现非一致修改或损坏；
外部会话中的哈希没有密钥或独立 receipt 锚点。拥有该外部目录写权限的进程可整体替换一组自洽
会话，因此 Phase 1 不声称认证原始用户确认。若未来威胁模型需要该证明，必须另行设计受保护的
append-only receipt 或本地签名密钥，不能把当前哈希升级描述为认证 provenance。

会话和导出使用同目录随机临时文件、`fsync` 和无覆盖硬链接发布；平台不支持该原子新建语义时
失败关闭，不退回覆盖写入。普通成功只会在临时链接已删除、最终文件经单硬链接稳定读取且字节
一致后返回。

## 4. 来源、检索与冲突

现有 provider-free lexical RAG 只作为确定性候选排序信号。它的内部 citation 不是 Evidence，
也不会直接出现在结果引用中。每条外部引用由 Phase 1 独立绑定：资料集内相对路径、原始文件
SHA-256、1-based 原始行范围，以及保留 CRLF/终止换行的精确原始行跨度 SHA-256。

每个不超过 240 KiB 的原始 UTF-8 分段独立索引；每段 `top_k=3`，跨分段按固定键合并并最多
保留 8 个 lexical 命中。无查询 token、零命中或不可索引内容会诚实退回 structural 引用，不会
伪报 lexical 命中。所有输出都标明这是确定性的离线提取/建议，不把检索文本或生成器自报当成
事实证明。

只识别声明过的结构化行：`constraint|requirement|约束|要求`，并接受半角或全角冒号/等号。
同一 key 存在多个值且未被已确认 Draft 约束消解时，结果进入 `needs_user_decision`；预览包含
逐行 conflict 引用，导出被阻断。解决冲突需要创建并重新确认新 Draft，不能修改旧会话。

## 5. 恢复、安全与失败语义

冻结结果的完整性验证与当前资料 freshness 验证是两个动作。结果完整性、解析和 rootless preview
不调用生成器、RAG、工具或网络；带 root 的检查和导出只重新捕获、比较原始文件/分段/引用，
不重新生成。资料改变或消失会使 rooted preview/export 失败，但不会破坏原冻结会话。

资料捕获在同一 observation map 中覆盖根、祖先、文件 identity、单硬链接、descriptor 读取和
最终完整 manifest；这是可信本地根上的竞态缓解，不是事务性快照或对抗性进程沙箱。所有状态
输出使用稳定代码，不打印原始异常或绝对资料根；preview/artifact 只包含受限引用片段。控制字符、
双向控制符、本地绝对路径形态和 Markdown 控制字符在系统生成的 Markdown 中被惰性化或脱敏。

创建式发布有三个可区分结果：

- 正常提交返回 exit 0；最终文件已经过单硬链接与精确字节复核，可立即恢复。
- 若提交名已创建、但系统不能证明临时链接已安全清理或最终状态可回滚，CLI 返回
  `outcome=attention_required`、exit 3 和
  `publication_state=committed_needs_attention`。调用方必须保留现场并人工检查，不能把它当作
  普通失败后自动重试。
- 若系统能证明最终名已删除，并已将本次创建的临时 inode 清空，则返回
  `outcome=rejected`、exit 2 和 `publication_state=rolled_back`。权限持续阻止删除时，同目录可能
  留下零字节随机临时项，但不会把其他替换文件当成本次临时文件清空。

提交前若无法安全清理本次创建的临时 inode，会用 `PRECOMMIT_CLEANUP_FAILED` 失败关闭且不创建
最终名。目录 `fsync` 只做 best effort，因此突然断电后的目录项持久性仍取决于本地文件系统；
上述保证属于可信本地目录中的竞态缓解，不是对抗性并发进程隔离。

## 6. 样本、验证与价值门槛

`examples/v3-phase-1/manifest.json` 定义四组纯合成样本：中文研究计划、多来源周报、Python
README 建议和中文全角冲突。这些样本验证工程合同，不代表用户已经采纳结果。

Phase 1 的工程完成与产品价值验收必须分开报告。2026-08-24 的原始广泛门槛及其数字仍由
[`v3-phase-2.md`](v3-phase-2.md) 中的旧 canonical Summary 合同完整保留。用户于 2026-08-30
另行批准的两轮收官只形成跨 profile、plan/PDF 范围受限接受。自动化测试、Mock、fixture、零调用
声明或 CLI 可运行都不能替代任何人工判断，也不能把 report、README 或直接 Apply 纳入已接受范围。

## 7. 工程验收证据（2026-08-24）

代码冻结后在两个全新的测试目录中分别完成默认测试和覆盖率测试：

| 检查 | 结果 |
|---|---|
| Sol 独立默认 Pytest | exit 0；`804 passed, 13 skipped, 1 warning`；142.78 秒 |
| Terra 覆盖率 Pytest | exit 0；`804 passed, 13 skipped, 1 warning`；182.96 秒；总覆盖率 `87.54%`，通过 fail-under 80 |
| 全仓 Ruff lint | exit 0 |
| Phase 1 六个 Python 变更文件 Ruff format | exit 0；六个文件均已格式化 |
| `compileall` / `pip check` | 均 exit 0 |
| migration | 运行时 migration 1–7 checksum 与冻结值逐项一致；没有 migration 8 |
| 高风险复核 | Sol High 复核发布、hard-link、恢复与 provenance 边界后给出 `ACCEPT_ENGINEERING_PHASE1`；无 P0/P1 阻断项 |

默认测试第一次使用了父目录尚不存在的嵌套 `--basetemp`，因此产生 434 个
`FileNotFoundError` setup error；该轮是测试环境准备错误，明确作废。创建全新的父目录后才得到
上表中的有效通过结果。全仓 `ruff format --check backend benchmark scripts tests` 仍为 exit 1：
76 个历史文件会被重新格式化、43 个已合规；Phase 1 的六个 Python 变更文件单独检查全部通过，
因此本阶段没有借机批量改写历史文件，格式债务留给独立迭代。

上述验收没有调用 provider、embedding、MCP 或网络，实际调用数分别为 0；没有启动 live API、
Docker、Godot 或 live DB。它证明离线工程合同与既有 Python 回归未被破坏，不替代第 6 节的人
工价值判断，也不把 deterministic 结果混写为真实模型评测。

## 8. 回滚

本纵切不修改数据库和来源文件。代码级回滚是移除 Phase 1 CLI、workflow 模块及对应测试/样本/
文档，并恢复 `safe_files.py` 的可选单硬链接读取扩展；数据级回滚只需保留或删除用户自行创建的
外部 Draft、Result 和导出文件。不得删除资料根、迁移或 v1/v2 数据。

## 9. T002 delivery-verification runbook candidate

`deterministic-grounded-plan-v3` 是显式 opt-in 的离线 runbook generator；默认 Draft 仍为 v2。
它仅对“本地交付复验”plan 任务输出三类证据盘点、Verification Matrix、PASS/FAIL 和独占 User
gate。所有新增检查组合标为 Reviewer-defined verification policy；缺失证据为 UNKNOWN/BLOCK，
历史证据不得重新生成或替换。

## 10. T002 runbook v5 presentation lineage

`deterministic-grounded-plan-v4` is an explicit additive opt-in for the fixed T002
delivery-verification task. Its only PDF presentation is
`v3-material-pdf-export-v4` / `projecttown-reportlab-pdf-v4`, bound to
`projecttown-human-pdf-v5` and `projecttown-trial-manifest-v5.json`. v4 and all
earlier profiles remain immutable and separately verifiable. The v5 candidate is
engineering-only until a newly authorized human Study records User evidence.

## 11. T002 runbook v6 minor-refinement lineage

`deterministic-grounded-plan-v5` is an additive, explicit opt-in for the fixed T002
runbook only. It binds `v3-material-pdf-export-v5` / `projecttown-reportlab-pdf-v5`,
`projecttown-human-pdf-v6`, and `projecttown-trial-manifest-v6.json`. It preserves v5
candidate bytes and semantics, while refining Final Authority versus Matrix Owner,
Basis precision, role labels, initial BLOCK wording, and compact table wrapping.

## 12. T002 runbook v7 execution-binding lineage

The retained v6 human candidate remains immutable. `projecttown-human-pdf-v7` is a
new engineering candidate, not a new human-acceptance claim: it binds
`projecttown-trial-manifest-v7.json`, `deterministic-grounded-plan-v6`, and
`v3-material-pdf-export-v6` / `projecttown-reportlab-pdf-v6`. It adds explicit
Run Binding and execution-state semantics while preserving offline deterministic
generation and fail-closed presentation pairing.

## 13. T002 runbook v8 execution contract

`projecttown-human-pdf-v8` is additive and create-only.  It binds manifest v8,
`deterministic-grounded-plan-v7`, and exporter/renderer v7.  It adds M00
preflight binding, separates preview text/JSON from PDF visual checks, and keeps
the frozen v7 candidate and all earlier bytes immutable. Its PDF is an intentional
four-page layout: summary/binding/inventory; flow/boundaries/Study contract; Matrix;
then states, gates, citations, and offline boundary. Four pages are expected, not a
pagination fallback.
Run Binding renders scan-safe `PATH_REF[...]` / `COMMAND_REF[...]` display values;
the create-only Result JSON retains canonical binding values and the exact command in
`draft.constraints` for M00/M01 verification. A displayed planned Study output is
only a reference and never authorizes a Study write.
The Matrix retains its ten columns but uses scan-safe row-type labels: `Mandatory`
means Mandatory Verification, `Conditional` means Conditional Release Action, and
`RP` means Reviewer-defined verification policy. These abbreviations preserve
readable word wrapping without changing the state contract on page four.

## 14. T002 runbook v9 engineering lineage

`projecttown-human-pdf-v9` is an additive, create-only engineering candidate.
It binds `projecttown-trial-manifest-v9.json`,
`deterministic-grounded-plan-v8`, and `v3-material-pdf-export-v8` /
`projecttown-reportlab-pdf-v8`. The v8 profile, manifest, Result, Trial, PDF,
and Summary identities remain independently verifiable and are never
reinterpreted as v9 bytes.

The v9 generator is narrow to the fixed T002 delivery-verification task. Other
v9 plan fixtures reuse the established generic visual plan renderer. For T002,
v9 adds the M00-01 through M00-07 preflight evidence contract, separates
runbook, target, and run identity, distinguishes historical input from fresh
output and User output, and splits the ten-field Verification Matrix into two
linked readable tables without changing its logical fields.

`PATH_REF[...]` and `COMMAND_REF[...]` are display-only identifiers. The
repository does not currently provide a canonical resolver, so supplying
strings cannot yield `PREFLIGHT PASS`: M00 remains `BINDING BLOCKED` until a
separately reviewed resolver contract exists. The v9 renderer uses measured
content flow rather than a forced page count; the exact page count for a human
handoff must be measured from the final freshly rendered candidate.

## 15. T002 runbook v10 engineering lineage

`projecttown-human-pdf-v10` is an additive, create-only presentation and
procedure lineage. It binds
`projecttown-trial-manifest-v10.json`,
`deterministic-grounded-plan-v9`, and
`v3-material-pdf-export-v9` / `projecttown-reportlab-pdf-v9`. The v10
manifest preserves the fixed T001–T010 tasks, sources, constraints, and order
from v9; only its schema/profile identity changes. The v8/v9 candidate,
manifest, Result, Trial, PDF, Summary, canonical bytes, and hash domains stay
independently verifiable and are never reinterpreted as v10 output.

The v10 T002 renderer is limited to a local delivery-verification procedure.
It must distinguish preflight configuration from runtime evidence, keep any
unresolved binding `BLOCK`, and never treat a displayed reference or a planned
Study output as authorization to write a Study. Fresh offline engineering QA
measured three A4 pages. The deterministic A4/B4 engineering PDFs have
SHA-256
`dc72b42fdeee8102a04de2fa9f0b0c8c6a4f24a264a26748e8a96fb7aeb61e12`;
the v10 manifest SHA-256 is
`6b95a1731fc88824e140c746966c8df5a33dd93ad2a05ded60423277d01390bf`.
These measurements are engineering references only and are not participant
evidence or a created Study.
