# ProjectTown v9 T002 真人 Study 启动 Prompt（start-prompt-only）

$sol-terra

项目根：

`D:\pycharmproject\ProjectTown`

本文件仅用于启动一个后续会话。它不是 Study 创建授权，不得自行创建
Study、Trial、Result、PDF 或 Summary。首先要求用户提供并明确授权一个此前
不存在的 Study ID、Study root 和 sibling work root；路径不得与任何 v1-v8
真人现场或 v9 工程 evidence root 重合。

## 固定候选合同

- candidate profile：`projecttown-human-pdf-v9`
- manifest：`examples/v3-phase-2/projecttown-trial-manifest-v9.json`
- manifest schema：`v3-phase-2-projecttown-trial-candidates-v9`
- manifest SHA-256：`8c68fc680c7ef996bb85460475702621be6e89fe81d06d780f0e197fc0c78919`
- generator：`deterministic-grounded-plan-v8`
- PDF exporter：`v3-material-pdf-export-v8`
- PDF renderer：`projecttown-reportlab-pdf-v8`
- completed Trial schema：`v3-usability-trial-v3`
- expected v9 PDF page count：`3`

固定 T002 任务、来源和 constraints 必须直接取自 manifest v9，不得为了评分
改变。固定任务为：

“制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项”

## 冻结 target input

v9 runbook 的验证对象仍是冻结 v8 candidate；下列文件只能读取：

- Trial：`D:\ProjectTown-usability\projecttown-v3-phase2-human-pdf-v8-20260829-001\T002.json`
- Trial record hash：`941fe1781845991ad7ed2a09911bd2f38a674d2b719346c301b8e5a563320eb1`
- Result：`D:\ProjectTown-usability\projecttown-v3-phase2-human-pdf-v8-20260829-001-work\T002-result-generator-v7-recovery-01.json`
- Result SHA-256：`c73a2767086d44e25f20615f3fcbffe857fd46fd8e126b424936e8f3ec670735`
- candidate PDF：`D:\ProjectTown-usability\projecttown-v3-phase2-human-pdf-v8-20260829-001-work\T002-runbook-v8-recovery-01.pdf`
- candidate PDF SHA-256：`1686e8e33ba39e0d25a554c8750e03781a68cea8a2205f911777b53eb3ecca68`

`PATH_REF[...]` 和 `COMMAND_REF[...]` 目前没有仓库内 canonical resolver。
因此 M00 必须显示 `BINDING BLOCKED`，不得把已填写字符串解释成
`PREFLIGHT PASS`。本轮真人 Study 只能评价这一 fail-closed runbook 是否清晰、
可执行和可判定；不得声称它已经完成一次真实 release verification。

## 工程参考（不得冒充参与者证据）

- engineering candidate e：
  `D:\ProjectTown-usability\projecttown-v3-runbook-v9-engineering-20260829-e\T002-runbook-v9-engineering.pdf`
  SHA-256 `db43a8f31943f0f93b40f6f4804f2655ffe23fca9caa2a041a80d7c453523ea8`
- deterministic fresh-root peer f：
  `D:\ProjectTown-usability\projecttown-v3-runbook-v9-engineering-20260829-f\T002-runbook-v9-engineering.pdf`
  SHA-256 `a06d38bd40b490ca593509689920988dd1bf349c344495a4af163f30e4ab80c4`

两份均为 3 页工程证据。正式真人候选必须在新的 work root 中按相同版本合同
重新生成；不得复制 e/f PDF 作为参与者实际阅读的冻结呈现。

## 获得新授权后的执行边界

1. 先只读复核 v8 target hashes、manifest v9 hash 和新 Study/work paths 的
   create-only 条件。
2. 在新 work root 运行现有 `draft -> generate -> pdf-export` CLI 链，生成新的
   v9 Result 和 3 页 PDF；不得虚构 resolver 或新生产接口。
3. 重新执行 Result/PDF check、SHA-256、pypdf 文本提取和全部页面彩色/灰度
   Poppler 渲染。若页数不是 3、source stale、binding 混配、存在裁切/重叠/
   missing glyph/孤立页，停止并保留现场。
4. 参与者本人必须实际打开该新 PDF，并自行提供 disposition、
   structural rewrite、citation usable、control rating、improvement reason、
   actions、active elapsed、manual baseline、participant notes、带时区的
   participant timestamp，以及该 PDF 的 exact participant evidence path。
5. 代理不得生成、推断、改写或补全参与者字段。只有字段由用户明确确认后，
   原会话才可决定是否执行 create-only `trial-create`。
6. `check --record trial` 必须通过 PDF bytes/profile/version/path binding；不得把
   新 v9 Trial 与 v8 或其他 profile 混入同一 Summary。

provider、embedding、外部 MCP、network/egress 和付费 API 必须保持 0；不得
读取 secrets、`.env*`、Authorization header 或私有 endpoint。Study PASS、
User Retain/Discard、Accept、Apply 和 Publish 是不同状态；没有用户额外明确
授权时不得 Apply/Publish，也不得进入 Phase 3。

返回时只报告工程与真人记录核验事实。测试通过、PDF 可打开或代理视觉检查
均不等于真人接受；在新 Trial 完成前结论必须保持 awaiting participant input。
