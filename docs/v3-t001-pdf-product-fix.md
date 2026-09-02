# T001 PDF 产品问题修复：工程候选说明

> 状态：PDF 候选已完成工程级检查；**真人价值尚未接受**。本文不创建 Study，不记录 Trial，也不授权
> Phase 3 或直接 Apply。

## 根因与修复目标

旧 T001 的 Result JSON 同时充当工程冻结记录与事实上的阅读面；其中旧 plan 模板把来源命中展开成
`Examine <file>` 式清单。它能维持 hash 自洽，却不能给普通用户一个可用的迭代计划。真人反馈为
`not_kept`、需要结构性重写、引用不可用、控制感 1/5；该反馈不能被旧 Result 的 self-consistent、
fresh 或 synthetic 测试推翻。

新候选将职责分开：JSON 仍是冻结会话、lineage 与 hash 的工程记录；preview 是直接阅读的文本面；
PDF 是面向用户的 create-only 派生成果。新版 plan 按任务目标生成执行摘要、主流程、阶段/优先级、
目的和行动、依据、交付物、验收标准、风险/未知/决策点、约束核对和短引用。它必须保持 source-grounded，
不能把命中摘录或空泛模板冒充计划。

## 版本与兼容策略

新生成 tuple 是：

```text
deterministic-grounded-plan-v2
segmented-deterministic-rag-v2
markdown-block-v2
```

旧 `deterministic-grounded-template-v1`、`segmented-deterministic-rag-v1` 和
`utf8-raw-240k-v1` 继续冻结。完整性验证按 Result 内冻结的 tuple 严格分派；未知、混合或不支持的
版本失败关闭。Result schema/hashes 继续保护原会话，PDF 不被写入 Result，因此 v1 bytes、hash、rootless
integrity 和 frozen preview 不会被新模板重算后误判损坏。原 Markdown `export` 的字节语义也不变。

## PDF 边界

`scripts/run_v3_material_workflow.py pdf-export` 从已验证且 fresh 的 Result 生成 PDF。它只向资料根之外、
尚不存在的绝对路径发布；目标存在、资料 stale、未解决 conflict、字体/backend 不可用或会话无效均失败关闭。
它不会覆盖来源、旧 export、旧 Result 或旧 Study。PDF 本身包含离线/确定性边界，且不含资料根绝对路径、
secret、内部异常或真实模型主张。

实现使用 ReportLab 4.x（生产依赖）和仓库内 Fusion 中文字体（OFL-1.1）；pypdf 6.x 仅用于开发/测试
的 reopen 与文本提取，页面渲染和视觉检查使用本地 Poppler。所有候选检查均为离线；provider、embedding、MCP、网络及付费调用
必须分别记录为 0。

## 工程候选与不成立的样例

样例 A–G 是迭代中发现的工程失败候选：存在泛化阶段、低相关/过长引用、Markdown 残留、不可读符号或
布局问题。它们**不**构成通过证据，也不能用于真人 Study。

Sol 最终视觉复核的工程候选为 H：

```text
D:\ProjectTown-usability\projecttown-v3-fix-engineering-20260825-sol-h\T001-plan.pdf
SHA-256 4240a8cff2705cbd5615fce58bbb15083e1baa58238b17fd2229e96037b8de3d
```

独立重复候选 I 得到相同 bytes hash。H/I 为两页、中文可读、可重新打开/提取文本/逐页渲染的工程候选；
它们只说明此候选通过了工程视觉复核，**不是**真人采纳、价值通过或 Phase 3 授权。核心 Sol focused
测试记录为 `65 passed`；该数字不替代新 Study 的用户评分。

## PDF Trial v2 与真人门槛

PDF 候选使用 `projecttown-human-pdf-v2` profile 和
[`projecttown-trial-manifest-v2.json`](../examples/v3-phase-2/projecttown-trial-manifest-v2.json)。Study/Trial/
Summary v2 与 v1 并存；已完成 Trial 绑定实际 `pdf-export` 文件的 bytes hash、PDF export/renderer
version 与 source artifact hash，避免将非实际呈现的文件计入真人评价。

必须重新建立新的外部 Study root 和 sibling work root，固定重做 T001–T010；旧 Study
`projecttown-v3-phase2-human-20260824-001` 与其 work root 仅供只读审计，T002 的未记录现场不得补记、
修改或删除。旧评分不能迁移到新候选。

真实用户需亲自记录 disposition、是否需要结构性重写、引用是否可用、控制感 1–5、actions、active elapsed
与 manual baseline。只有完整 10 项、三类产物各至少 2、至少 7/10 无需结构性重写且被实际保留/导出、
主流程不超过 5 个动作、且离线调用均为 0 时，Summary 才可能到达
`criteria_met_unanchored_awaiting_user_acceptance`。这仍需要用户明确接受；此前不进入 Phase 3。

具体运行交接见 [`v3-phase-2-pdf-study-handoff.md`](v3-phase-2-pdf-study-handoff.md)。

## 回滚与剩余风险

回滚只移除此版本化 PDF 入口、v2 generator/record 分派和相关测试/文档；不得改写旧 generator、旧
schema、旧 Result、manifest v1 或外部 Study。外部 create-only PDF 可由其所有者自行保留或处理，不应
由代码回滚删除。

剩余风险是产品价值而非工程通过：H/I 尚未经过真实用户任务、不同资料集、不同计划/报告/README 产物
和 10-task 门槛的检验。PDF 的可读性也需在每一个实际 Study 任务中先由参与者确认；若用户认为成果
仍需结构性重写，应记录结果、保留证据并重新设计，而不是重写旧 Trial。
