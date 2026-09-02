# v3 Phase 0：离线资料集发现基础

## 已观察到的代码审查

现有 v2 provenance 已有基于 descriptor 的稳定常规文件读取、重解析点拒绝和前后 identity 校验。Phase 0 将这些通用只读原语抽至 `backend/app/safe_files.py`，不接入 Quest、API、数据库、RAG、provider 或 MCP。它落实了 [v3 产品方向](v3-product-direction.md) 的“显式资料集、默认离线”Phase 0 边界。

## 锁定策略

策略版本为 `v3-material-set-v1`：根目录必须是一个绝对、既有、非重解析目录；显式选择唯一、NFC、POSIX 相对路径。仅支持严格 UTF-8 的 `.md`、`.txt`、`.json`、`.py`；`max_files=100`、`max_file_bytes=1,048,576`、`max_total_bytes=10,485,760`。空白文件、越界/重解析路径、格式或编码错误都 fail closed，且不返回部分 manifest。

## 样本与 CLI

提交样本包括中文研究材料、Python README/.py/.json、空白文本和不支持 CSV。运行：

```powershell
.\.venv\Scripts\python.exe scripts\inspect_v3_material_set.py --root (Resolve-Path examples\v3-phase-0\research-cn) --file notes.md --file constraints.txt --output (Join-Path $PWD 'sandbox\tmp\research-cn.json')
```

报告 schema 为 `v3-material-set-manifest-v1`，含 `schema_version`、`policy_version`、完整 `policy`（version、max_files、max_file_bytes、max_total_bytes）、状态、相对路径条目、root hash 与安全 issue；不含绝对路径或来源文本。根或输出边界无效时不写报告；根与输出边界均验证通过后，资料内容失败可在资料根外写出结构化报告。输出目录必须已存在、安全、规范化后在资料根外且目标文件不存在。

## 非可执行低保真流程

```text
[本地工作区] -> [选择任务] -> [确认任务] -> [显式选择资料集] -> [离线检查报告] -> [未来：建议/预览/导出]
```

这是文档 wireframe，不是 Web UI、API 或可执行工作流。

## 安全与 TOCTOU 边界

读取使用 `lstat`、可用时 `O_NOFOLLOW`、有界 descriptor chunk、`fstat` 和路径/descriptor 前后 identity 检查。该机制降低检查期间替换文件的风险，但不把本机文件系统描述为事务性快照；不稳定或不可读即失败关闭。硬链接、挂载边界、非事务性读取与输出竞争仍是 Phase 1 需单独制定策略的限制，不能据此宣称完整隔离。

## 当前已实现与未实现

当前只实现显式资料集的离线检查和 JSON 报告。提交的样本均为合成样本，不含私人用户资料。没有建议生成、来源预览、下载、Apply、API、持久化、实时 UI、provider/egress 或用户价值结论。

## 测试、重复与回滚

确定性核心应在两个全新临时根目录运行；失败/恢复类案例一次并在失败后聚焦复跑。Phase 0 不满足 Phase 1 的 10 个真实任务、7 个有用产物价值门槛。回滚仅删除新增 Phase 0 文件并恢复本次 provenance helper 导入，不删除用户资料或验证输出。
