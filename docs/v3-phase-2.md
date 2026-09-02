# v3 Phase 2：离线真人试验记录工具

> 状态：工程记录工具已于 2026-08-24 通过工程验收。用户于 2026-08-30 以完整十任务 Study
> 过于冗长为理由，批准以 T001/T002 两轮冻结真人 Trial 作范围受限收官。旧十任务 Summary 仍没有
> 完整可汇总 Study，且其 schema/hash/门槛不变；两轮收官不代表单一 profile、v10、report/README
> 或直接 Apply 已获接受。

## 1. 目标与边界

Phase 2 的最小目标是让用户能够以可恢复、创建式、离线的方式记录 Phase 1 的 10 个固定候选任务，
并让工具只计算门槛、而不替用户作产品价值决定。流程是外部 canonical
**Study → Trial → Summary**；所有记录都在用户选择的外部新文件中，不进入 Quest、API 或数据库。

本阶段不新增 Web UI、`/api/v3`、migration 8、数据库、直接 Apply、provider、embedding、RAG、
MCP 或网络调用；不读取 secret，也不改变 v1/v2 或 Phase 0/1 合同。仓库版本仍为 `1.0.0`。

## 2. 固定候选与记录类别

v1 的 [`projecttown-trial-manifest.json`](../examples/v3-phase-2/projecttown-trial-manifest.json)
保持不可变，适用于历史 v1 record。文字优先 PDF 候选固定使用
[`projecttown-trial-manifest-v2.json`](../examples/v3-phase-2/projecttown-trial-manifest-v2.json) 与
`candidate_profile=projecttown-human-pdf-v2`；视觉 PDF 候选固定使用新增、独立的
[`projecttown-trial-manifest-v3.json`](../examples/v3-phase-2/projecttown-trial-manifest-v3.json) 与
`candidate_profile=projecttown-human-pdf-v3`。三个 manifest 都固定预注册相同 T001–T010 及
artifact-kind 顺序，并绑定精确 `candidate_manifest_hash`；后续操作都会拒绝 manifest drift。不得增加、
删除或替换任务。v1/v2 canonical record 不得写入候选文件路径、任务全文、来源内容或自由文本；v3 的
唯一受限例外见第 11 节。

候选呈现是 fail-closed 的一对一绑定：`projecttown-human-pdf-v2` 只接受
`v3-material-pdf-export-v1` / `projecttown-reportlab-pdf-v1`；
`projecttown-human-pdf-v3` 只接受
`v3-material-pdf-export-v2` / `projecttown-reportlab-pdf-v2`。后者是离线确定性向量视觉呈现，
并非文生图或在线服务。不得将 v2 Trial、PDF 或 Summary 改标为 v3，也不得在同一正式 Summary 中混用。

| 记录 | 用途 | 不可声称的内容 |
|---|---|---|
| Study | 固定 T001–T010、artifact-kind 预注册、candidate manifest hash 和外部 record lineage；PDF profile 使用 Study v2 | 用户已执行或同意任务。 |
| Trial | 用户实际执行后的结构化 outcome、时间、保留/导出与评分记录；PDF profile 的 completed Trial v2 另绑定实际呈现 PDF | 自动生成或 synthetic 测试就是用户评分。 |
| Summary | 对完整 Trial 集计算门槛和缺项 | 自动接受、发布或进入 Apply。 |

[`engineering-manifest.json`](../examples/v3-phase-2/engineering-manifest.json) 的
`synthetic_engineering_fixture` 与 `synthetic_rating` 仅为工程样例；即使字段名称近似采纳指标，
也绝不计入真人 7/10。

## 3. 隐私、完整性与更正

canonical record 只保存固定 task ID、artifact kind、受限枚举、计数、时间/评分等结构化值及
domain-separated hash/lineage；候选 manifest 本身可包含任务/路径元数据，但 canonical records 只绑定
其 hash，不复制这些内容。PDF profile 的 Trial v2 保存 PDF bytes hash、导出/渲染器版本与 frozen artifact
hash，不保存 PDF 路径或来源根；它要求 completed Trial 绑定实际由 `pdf-export` 生成并复核的 PDF。v1/v2
records 不保存任务、路径、资料内容、完整 prompt/response、密钥、Authorization header 或自由文本。v3
仅允许参与者显式输入的 `participant_notes`、`participant_timestamp` 和受限 evidence path：三者均进入
Trial hash，Summary 仅投影其存在性，CLI 不回显 notes 或完整路径。Study、Trial、
Summary 都是 create-only 的 reserved direct children：
`study.json`、`Txxx.json` 与 `summary.json`，不能覆盖或修改旧记录。

更正采用新的 study root；不原地编辑、删除或重写旧 record，也不使用前序关系字段。损坏、缺项、
错误 lineage 或无法验证的外部记录必须 fail closed：`summary-create` 拒绝创建 Summary，而不是以默认值
补齐或生成占位记录。

## 4. 命令流

已冻结的离线 CLI 命令面为：

```text
study-create   --study-root <ABS> --study-id <ID> --evaluation-kind human_usability|synthetic_engineering_fixture [--candidate-profile projecttown-human-pdf-v2|...|projecttown-human-pdf-v10]
trial-create   --study-root <ABS> --task-id T001 <restricted action/time/rating/outcome/result/export flags> [--pdf-export <ABS>] [--participant-notes <TEXT> --participant-timestamp <RFC3339> --participant-evidence-path <ABS>]
summary-create --study-root <ABS>
check          --study-root <ABS> --record study|trial|summary [--task-id T001]
preview        --study-root <ABS>
```

这些是离线 CLI 合同，不是 live service、网络或写入来源文件的接口。`check` 与 `preview`
只验证或显示冻结 record；创建命令从不覆盖 reserved target。三个 participant 参数适用于 v5 及后续明确的 participant-evidence PDF profiles（当前为 v5–v10，均使用 TrialV3）；旧 record schema 与语义不变。任何 `attention_required` 或完整性失败都
不能自动重试为成功，必须保留现场并由用户决定。

PDF profile 下，completed Trial v2 缺少 `--pdf-export` 会被拒绝；失败 Trial 不得伪称呈现了 PDF。绑定
复核 PDF 的 bytes、导出版本、渲染器版本和 frozen artifact hash，防止用其他 Result、非规范 PDF 或在
资料根/仓库内的路径替代实际候选。历史 Study/Trial/Summary v1 继续按 v1 schema、hash domain 和
语义解析；新的 v2 record 不能回写、重算或改变旧记录。

## 5. 门槛计算与接受边界

Summary 只能在所有 T001–T010 都有可验证的用户 Trial 时创建：三类 artifact 每类至少 2，至少 7/10
由用户标记为“不需要结构性重写即可采用”且实际保留或导出，主流程不超过五个主要动作，且离线
provider/embedding/MCP/egress 调用均为 0。任何缺项、非用户确认、非固定 task ID、manifest drift 或
不可验证 lineage 都会拒绝 Summary 创建。

即使上述数字均满足，自动状态最多是
`criteria_met_unanchored_awaiting_user_acceptance`。其他 gate state 只有 `engineering_only` 与
`criteria_not_met`；record status 是 `external_self_consistent_unanchored`，永远不是 human accepted。
只有用户明确确认后才可形成产品价值验收；工具绝不自动写出 `accepted`、不授权 Phase 3 中的
目标写入 Apply，也不宣布发布。

## 6. 工程样例与真人任务

永久代表性样例门槛适用于本阶段：正向 create/check/preview、无效 enum/lineage/重复 task 的负向路径，
以及中断或 publication attention/recovery 路径，都必须在新的证据目录中运行并记录 exit code/counts。
这些合成样例只能证明 record tooling 合同。

旧 canonical Summary 路径中的真人价值汇总由用户自行执行 T001–T010、评分并确认。交付报告必须把
synthetic engineering results、human Trial records、Summary calculation 与 user acceptance 分开列出；
没有用户记录时报告“未接受”。2026-08-30 的两轮收官走独立 receipt，不改变这里的 Summary 合同。

## 7. 工程验收证据（2026-08-24）

- Phase 2 收集到 45 个 core 单元节点与 35 个 CLI 集成节点；覆盖正常、拒绝、冲突、放弃、篡改、
  manifest drift、路径边界、发布回滚/需关注、恢复和确定性样例。
- 最终代码的全新聚焦目录完成，exit 0、`140 passed, 1 skipped, 1 warning`；唯一 skip 是当前 Windows
  账户缺少创建 symlink 的权限（WinError 1314），对应的无特权 noncanonical-path 负例已通过。发布路径
  三个 inode-replacement 注入节点还在两个额外新目录中各通过一次，均为 `3 passed`。
- 最终代码的全量默认轮完成，exit 0、`891 passed, 14 skipped, 1 warning`。
- 最终代码的独立全量 coverage 轮完成，exit 0、`891 passed, 14 skipped`，总覆盖率 `87.62%`，
  通过 fail-under 80。
- 全仓 Ruff、四个 Phase 2 Python 文件的 format check、`compileall` 与 `pip check` 均为 exit 0。
- migration 仍只有 1–7，checksum 与冻结值逐项一致；没有 migration 8。真实 provider、embedding、
  MCP、网络或付费调用为 0。

最终证据目录为 `sandbox/tmp/phase2-sol-postrace-focused-parent/run`、
`sandbox/tmp/phase2-sol-postrace-full-parent/run`、`sandbox/tmp/phase2-sol-postrace-coverage-parent/run`、
`sandbox/tmp/phase1-publication-unlink-race-attempt-20260824` 与
`sandbox/tmp/phase1-publication-unlink-race-final-b-20260824`。

这些数字只接受 Phase 2 的工程实现。旧十任务 canonical Summary 至今仍无完整 T001–T010 Study；
历史上存在多个不可跨 profile 汇总的 T001/T002 Trial。2026-08-30 的两轮收官通过独立 additive
receipt 表达，不把这些历史 Trial 拼装成旧 Summary。

## 8. 回滚

代码回滚仅移除独立 Phase 2 CLI、record 模块、测试与本说明；不删除用户资料根、v1/v2 数据、migration
或外部 records。用户可保留外部 Study/Trial/Summary 以便审计；若不再需要，应由用户自行处理。

## 9. T002 runbook candidate lineage

`projecttown-human-pdf-v4` 只绑定 `v3-material-pdf-export-v3` /
`projecttown-reportlab-pdf-v3` 和 `projecttown-trial-manifest-v4.json`。v2/v3 profile 保持原绑定；
不同 profile 不得混入同一正式 Summary。该候选仍须在用户另行授权的新 Study 中执行，工程测试或
PDF 视觉检查不构成 User 接受。

## 10. T002 runbook v5 candidate boundary

The v5 profile is a new, create-only candidate lineage for a narrow T002 semantic
clarification. It preserves v4's three-category layout while making original-candidate
comparison, provenance classification, VERIFIED/User Gate progression, Matrix Basis,
and independent-study blocking rules explicit. It does not alter fixed tasks, sources,
old Trial records, Summary eligibility, or User-controlled Apply/Publish actions.

## 11. v5 participant evidence Trial contract

`projecttown-human-pdf-v5` uses the additive `v3-usability-trial-v3` record and
`projecttown/v3/usability-trial/v3` hash domain. A completed v5 PDF Trial requires
explicit participant notes, a timezone-bearing canonical participant timestamp, and the
canonical absolute path of the exact PDF the participant opened. Those values are
included in the Trial hash; the evidence path is re-read and hash-checked during Trial
and Summary verification. Summary v3 projects presence only, never the notes, timestamp
or path, and remains `criteria_met_unanchored_awaiting_user_acceptance` at most. Older
v1/v2 Trial and Summary bytes and hash domains remain unchanged.

## 12. T002 runbook v6 minor-refinement boundary

`projecttown-human-pdf-v6` is a separate create-only presentation lineage using
`projecttown-trial-manifest-v6.json`, `deterministic-grounded-plan-v5`, and
`v3-material-pdf-export-v5` / `projecttown-reportlab-pdf-v5`. v5 and v6 both route to
TrialV3/SummaryV3, but their presentation bindings remain fail-closed and separate.
The v6 change is a minor refinement, not a revision of the retained v5 human evidence.

## 13. T002 runbook v7 Study boundary

`projecttown-human-pdf-v7` is an additive, create-only lineage for a separately
authorized Study. It uses manifest v7, generator v6, and exporter/renderer v6;
it routes to TrialV3/SummaryV3 with the existing participant-evidence contract.
The retained v6 Study is unchanged. Engineering checks and PDF inspection do not
constitute human acceptance; any Summary remains awaiting explicit User acceptance.

## 14. T002 runbook v8 Study boundary

v8 is a new human-review candidate only after a separately authorized, unique
Study and work root are supplied.  It uses the existing TrialV3/SummaryV3
participant-evidence protocol, but its v7 generator/exporter/renderer binding
cannot be mixed with older profiles. Its four-page PDF is deliberate: binding and
inventory; flow and boundaries; Matrix; then state/gate/citation evidence. No handoff
document creates a Trial.
For v8, the PDF/preview show scan-safe path and command references only; canonical
Run Binding values and the exact command remain in the Result JSON `draft.constraints`
for M00/M01. The planned Study output remains only a reference until a separately
authorized create-only Study run.
The v8 Matrix preserves ten columns and explains its compact labels in the PDF:
`Mandatory` = Mandatory Verification, `Conditional` = Conditional Release Action,
and `RP` = Reviewer-defined verification policy.

## 15. T002 runbook v9 Study boundary

The v9 profile is a separate presentation lineage using manifest v9,
generator v8, and exporter/renderer v8. It continues to use the additive
TrialV3/SummaryV3 participant-evidence contract, while old v1-v8 record bytes,
hash domains, manifests, and presentation bindings remain unchanged.

The v9 runbook keeps `PATH_REF[...]` and `COMMAND_REF[...]` as opaque display
identifiers. Until a real repository resolver exists, M00 is
`BINDING BLOCKED`; field presence cannot imply `PREFLIGHT PASS`, `VERIFIED`,
human Study PASS, User disposition, or release authorization. M07
`WAITING USER` is a valid waiting state. M08 without separate User authority is
`NOT AUTHORIZED / DO NOT EXECUTE`, not a verification failure.

Engineering evidence may use new external fresh roots to prove deterministic
generation, layout, extraction, rendering, create-only, stale, conflict, and
recovery behavior. It never creates a human record or substitutes for the PDF
actually opened by a participant. A later human Study requires a new unique
Study root and sibling work root, a freshly generated Result/PDF, the measured
v9 page count, and participant-confirmed notes, timestamp, actions, and exact
evidence path. No handoff document grants that authorization.

## 16. T002 runbook v10 Study boundary

`projecttown-human-pdf-v10` is a distinct candidate profile using manifest v10,
generator v9, and exporter/renderer v9. It continues to route through the
additive TrialV3/SummaryV3 participant-evidence contract. Old v1–v9 study,
trial, summary, manifest, and presentation bytes/hashes remain unchanged.

Any future v10 human Study is create-only and requires separate user
authorization, a never-before-used Study ID, a new Study root and sibling work
root, a freshly generated canonical Result/PDF, and the measured v10
`expected_page_count` from engineering QA. The participant—not the tool or
handoff—must provide notes, a timezone-bearing timestamp, actions, and the
canonical PDF evidence path. Summary status remains at most
`criteria_met_unanchored_awaiting_user_acceptance`; it never authorizes
Retain, Discard, Apply, Publish, or Phase 3.

The accepted engineering reference is three A4 pages. Its A4/B4 PDF SHA-256 is
`dc72b42fdeee8102a04de2fa9f0b0c8c6a4f24a264a26748e8a96fb7aeb61e12`, and
manifest v10 SHA-256 is
`6b95a1731fc88824e140c746966c8df5a33dd93ad2a05ded60423277d01390bf`.
These hashes bind engineering QA only; a future participant must still open a
fresh create-only PDF in a newly authorized sibling work root.

## 17. Two-round scope-limited closeout

The user explicitly reduced the product gate from the full T001–T010 Study to
two representative rounds because the full protocol was too burdensome. The
additive `v3-phase2-closeout-v1` receipt binds the retained T001 v3 and T002 v9
Study/Trial/Result/PDF evidence without changing any old Study, Trial, Summary,
manifest, or hash domain. Its conclusion is strictly
`scope_limited_longitudinal_cross_profile_acceptance`: both artifacts are plans,
v10 and report/README remain unvalidated, and no Apply/Publish is authorized.
See [`v3-phase-2-closeout.md`](v3-phase-2-closeout.md).
