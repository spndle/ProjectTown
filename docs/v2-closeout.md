# ProjectTown v2.0 本地开发收官与验收报告

- 收官日期：2026-08-22
- 验收角色：Sol（需求、架构、代码复核与最终验收）+ Terra（边界明确的实现与实机验证）
- 收官口径：本地、loopback、单用户、单进程、单节点、SQLite 的开发与求职演示基线
- 最终结论：**在上述边界内验收通过；不等同于公开发布、生产上线或真实模型/真实 MCP 验收**

## 1. 版本口径

本报告中的“v2.0”是当前开发阶段的收官名称。仓库尚无 Git 元数据、许可证、release tag 和公开发布授权，因此 `VERSION`、`/health` 与 Godot 标题仍保持既有 `1.0.0`/`v1.0` 发布标识；本轮没有用一次未授权的版本号替换来冒充正式发布。若用户决定发布 v2，必须先选择许可证、初始化或关联 Git、审查完整首个提交，再统一更新版本、tag、发布说明与演示媒体。

## 2. 已交付范围

v2 在不破坏 v1 核心不变量的前提下完成了以下本地能力：

1. Phase 1A–1C：provider-neutral model contract、attempt/cost ledger、隔离的 OpenAI/Qwen adapter、默认关闭的本地 Settings 控制面，以及 `base_url`、`api_key`、`model` 同源三元组；真实调用仍需重新授权。
2. Phase 1D：Quest 历史搜索、状态筛选、分页和基础失败/恢复信息；archive/unarchive 与完整失败导航未虚报完成。
3. Phase 1E：固定 Godot 4.7.1、renderer、字体、7 个 fixture、3 个 viewport 和 fail-closed golden runner 的确定性截图回归。
4. Phase 2A：provider-free、进程内、确定性的 lexical RAG、citation/bundle hash 与合成离线评测；它未接入 Quest、Event、Evidence 或 replay。
5. Phase 3A：默认关闭、fixture-only 的本地 stdio MCP 2025-06-18 Gateway Adapter；它不是任意真实 server 或远程 MCP 的安全验收。
6. 全面审查后的可信执行修复：只读工具不再创建 workspace；worker entry 才执行 admission；lease heartbeat/owner-safe release；公共 model-call JSON 的敏感 token 字段防御；真实评测区分 fresh dispatch 与 idempotent replay；telemetry close 生命周期竞态修复。
7. migration 7 compatibility-shadow artifact provenance：受限 baseline/final workspace snapshot、实际 `write_file` before/after observation、action/event/Evidence/artifact 交叉绑定、旧 Quest `legacy_unobserved` 恢复，以及 Godot 只读审计提示。
8. secret hygiene：Git 与 Docker 构建上下文均排除 `.env.*` 和整个 `.secrets/`，同时允许公开的 `.env.example`；没有把会话中出现过的密钥或私有 endpoint 写入仓库文档。

仍保持成立的核心包括 `/api/v1` 兼容面、现有 `/api/v2` 语义、Goal Contract 两阶段确认、Event ledger/CAS/checkpoint/replay、Gateway allowlist/Sandbox/幂等与 unknown-effect 协调、独立 Verifier/Evidence、Watchdog/预算/受限 replan、成果 preview/retain/discard 以及 WebSocket `(quest_id, sequence)` 去重。

## 3. 本轮主要变更范围

| 区域 | 主要路径 | 作用 |
|---|---|---|
| Secret/build 边界 | `.gitignore`、`.dockerignore` | 排除 `.env.*`、`.secrets/`，保留 `.env.example` |
| Provenance 核心 | `backend/app/v1/provenance.py` | 有界、无链接跟随、句柄绑定且检测竞态的 workspace scanner；artifact 分类器 |
| Gateway | `backend/app/v1/gateway.py` | 对实际写后文件做有界 descriptor 读取，并与原子 action receipt/Event 一起记录 observation |
| SQLite | `backend/app/v1/storage.py` | additive migration 7、不可变 side-ledger、baseline/final/provenance 原子写与绑定校验 |
| Runtime | `backend/app/v1/service.py` | admission 后工具前 baseline、旧 Quest 恢复、final snapshot/classification/review 原子入口 |
| 既有审查修复 | `backend/app/tools.py`、`backend/app/telemetry.py`、`backend/app/v1/model_runtime.py`、`backend/app/v1/mcp_adapter.py`、真实评测 runners | 只读副作用、租约、敏感字段、生命周期与报告准确性修复 |
| Godot | `godot/scripts/main.gd`、`godot/tests/artifact_provenance_ui_smoke.gd`、`right_panel_layout_smoke.gd`、golden manifest | 成果 provenance 只读提示、窄/中/宽布局回归；PNG golden 未改 |
| 回归测试 | `tests/unit`、`tests/integration`、`tests/recovery`、`tests/contract` | migration、竞态、恢复、原子性、旧 manifest、retain/discard 与安全边界 |
| 文档 | ADR、architecture、limitations、Phase 2/3、审查报告、本报告、v3 Prompt | 固定范围、证据、回滚与下一阶段决策 |

当前关键候选文件 SHA-256：

```text
backend/app/v1/provenance.py  5d78b551397456c2b63127e685c4c0ca0433738ab9964cf6f1b8e6db66811179
backend/app/v1/gateway.py     68d5140a293d1152f6f38c0304db4a2a5518f310983a1c46856849114418071d
backend/app/v1/storage.py     95c7726755390cde70effbf9d423436bd42b8c02d0a905cb088ba4ce0cdcae81
backend/app/v1/service.py     5c47e7c690d458e769d9529e59dd80c5fb6cbd544bd7920fdd12855eb49dc52a
godot/scripts/main.gd         af5fcd70f0ddd0b168ee82917a1d93490641b02fbdf8f4100f696a90d383cae1
```

Sol 在容器内重新计算前四项并与宿主候选逐项比较，全部相同。

## 4. 数据库、迁移与回滚点

### 4.1 migration checksum

```text
1  a5cf0cc34069eb682302ddfb7fc73dc512813b58d1ce1a805d5dcc66a98e0404
2  64ba14a8f3d5b7d33083d5567b43f8096b80b7d15d72bfdc12a12d92d06e0d44
3  5790cbf8ea42f8eca1d7b56e2ee5ea073c3a423ac1daf086d655baf54e4fbdf3
4  a13b4bdb679de98545c0427aa66e4de7f9fb1e808eb2af11deb1b35ae3bb888c
5  35e9cdedf7b779368ccfe2a7dca520adf27c07e689dfbb40860a7338e90c1e36
6  a00417e01f234239c4f7715750c4b9f34fc1ea5385828b1fc895d49b4ebd936c
7  53eac8c6c4241107c5c1cd3e85e2ef162f9a6322e5d288f706924a785589a7ce
```

migration 1–6 未重写。migration 7 只新增：

- `v1_workspace_snapshots`；
- `v1_workspace_snapshot_entries`；
- `v1_tool_file_observations`；
- `v1_artifact_provenance`。

表级约束和测试拒绝 UPDATE/DELETE，并明确拒绝 provenance coarse status `verified`。v2 只能记录 `shadow`、`legacy_unobserved` 或 `unrecoverable`；细分 `shadow_observed_*` 仍不是独立完成证明。

### 4.2 数据证据与备份

迁移 7 之前的完整 SQLite online backup：

`sandbox/tmp/v2-closeout-pre-m7-1cb937db062448239b0066ecb995ff6a/projecttown.db`

- size：2,191,360 bytes；
- SHA-256：`93314e1098d70a7763f42d6c04cdb45a44892aa6f0327fa38969094bb22b2e93`；
- `integrity_check=ok`；
- `foreign_key_check=0`；
- migration 1–6。

最终 live Docker SQLite 连续两轮均为 `integrity_check=ok`、外键违规 0、migration 1–7 checksum 精确匹配。

### 4.3 回滚

- migration 7 是 additive migration，不提供破坏性 down migration。
- 代码/UI 回滚时可停止新 side-ledger 写入和提示显示，但必须保留新表。
- 需要数据级回退时，停服并恢复上述完整、已验证的 pre-migration 备份；不得手工删表、删 migration ledger 或修改 checksum。
- 由于当前无 Git，源码回滚不能依赖 `git revert`；初始化 Git 前必须保留当前完整工作区快照并审查首次提交。

## 5. 最终实际验证证据

### 5.1 Python 与覆盖率

| 门禁 | 最终结果 |
|---|---|
| Ruff：`backend benchmark tests scripts` | exit 0 |
| compileall：`backend benchmark scripts tests` | exit 0 |
| `pip check` | exit 0，`No broken requirements found` |
| 默认 Pytest Terra A/B | 每轮 `605 passed, 12 skipped, 1 warning`，exit 0 |
| 默认 Pytest Sol 独立轮 | `605 passed, 12 skipped, 1 warning`，exit 0，138.75s |
| Coverage Terra A/B | 每轮 `14185` statements、`1194` missed、总计 `92%`，fail-under 80 通过 |
| provenance/Gateway/migration/review/admission/service A/B | 每轮 `133 passed, 2 skipped`，exit 0 |
| Sol provenance/Gateway/migration/review 聚焦 | `115 passed, 2 skipped, 1 warning`，exit 0 |
| Secret/release/settings 补充 | `91 passed, 2 skipped, 1 warning`，exit 0 |

唯一 warning 是既有 Starlette `TestClient`/httpx 弃用提示。

测试过程中有一次复用了已存在 basetemp 的无效首尝试，在 Windows SQLite 文件锁清理阶段出现 `290 passed, 1 skipped, 326 setup errors`。该轮没有被计为通过，也不是断言回归；保留原目录后改用两个全新隔离目录，随后 Terra 两轮和 Sol 独立轮全部通过。这仍提示测试临时目录需要唯一命名，不能把失败目录复用为验收证据。

### 5.2 RAG、MCP 与 Benchmark

Phase 2A RAG 测试两轮各 `20 passed`。runner 两轮逐字节一致：

```text
results.json  4deb8715c45683fcfbe5608b64267a677476e3a1fb5892ecfdcbb6e4f2759bb1
results.csv   b34f02a8e6b398e65b317ff35e11a47f43687ecf2d02a7d2a201c7bc7ab92bf4
report.md     5023bef80419c381d9e4cab43192225fb23d49f89da3bef8edb6d7a3a24a76c4
manifest.json 932d5dbfd6ceea5315b072b543c78729e46ade5725d278fa6baf2eb4f9d15477
```

两轮均为 `deterministic_rag_evaluation=true`、`provider_calls=0`、`embedding_calls=0`。首次按不允许的输出根调用被 runner 以 `OUTPUT_PATH_OUTSIDE_SANDBOX` 拒绝，随后改用其合同规定的 `sandbox/tmp/rag-evaluation/**`；拒绝行为是安全边界生效，不计为评测失败。

Phase 3A MCP focused suite 两轮各 `12 passed`；没有启动真实第三方 server。

formal-v1.0 committed manifest 自校验通过，两轮临时重生成均为 seed 1729、4320 raw rows，并与 committed 五个文件逐项一致：

```text
results.json  f8b5d380fc87a37f071b28f38d69988ad8938470da4dbbc3663583111a193d06
results.csv   4f24fac7930945aaa7986e905cf5ac7f6393499fa8bd4caaa61162be6712d1c6
report.md     21ba42602336fd34015df50ef2f2bba24f90d0bf8645172c870c6d4418128f0b
success.svg   5181d5fec3434925aa9ea9c85a688c5e169ce82065cb54a16c8509c9910af3d5
manifest.json 666aeaad38713679937e8a49ebed37ea45c08aa1b8217b11439134be2cc5e30e
```

formal 仍明确是 `runtime_simulation=true`、零真实模型调用，不代表模型质量或生产延迟。

### 5.3 Docker 与 SQLite 实机

- `docker compose ... build --pull` 因 Docker Hub 连接不可达而 exit 1；该失败已披露，不计成功。
- 使用本机已有的同一基础镜像缓存重新 COPY 最新源码构建：exit 0；`up -d --force-recreate` 与 restart 均 exit 0。
- 重建后 `/health` 连续 2 次、重启后连续 2 次均 HTTP 200、`database=ok`。
- 无 session token 的 Settings 请求为 403。
- 容器 `healthy`、用户 `10001:10001`、read-only rootfs、`cap_drop=ALL`、`no-new-privileges=true`、仅 `127.0.0.1:8000`。
- Docker 日志 73 行，凭据候选 0；没有读取 named volume 中的 provider 值。
- 两个新 Quest：`qv1_774555ffac27` 完成 retain，`qv1_745d0f99cbb9` 完成 discard（投影按既有语义为 failed/discarded）。每个 Quest 都有 2 个 snapshot、1 个 write observation、1 个 provenance、1 个 `ArtifactReviewRequested`，manifest 含六个 provenance 字段。
- 两项 review 均验证同 idempotency key 重放；重启后 full replay、checkpoint replay 与 projection 一致，checkpoint 有效，state version 均为 21。
- 只读恢复 fixture `qv1_f3be8065a1d8` 保持 `waiting_user/pending`，没有替用户 retain/discard。

证据目录：`sandbox/tmp/v2-closeout-final-docker-20260822-01`。

### 5.4 Godot 4.7.1 实机

引擎：`4.7.1.stable.official.a13da4feb`。

- `validate_godot_v1.ps1` 两轮 exit 0；每轮完成 editor parse、主场景、本地 Uvicorn/API smoke，均为 21 events、4 evidence。
- 昼夜两轮：`TIME_CYCLE_SMOKE_OK anchors=6 midpoints=3`。
- provenance UI 两轮：`ARTIFACT_PROVENANCE_UI_SMOKE_OK cases=8`。
- 右侧布局两轮：`RIGHT_PANEL_LAYOUT_SMOKE_OK widths=[900.0, 1280.0, 1920.0]`。
- Docker Quest 只读恢复两轮：19 events、4 evidence、1 artifact；前后 status/state-version/disposition/provenance 数量完全相同。
- 正式视觉 B/C 两轮各 7 fixtures × 3 viewports = 21/21；`changed_pixel_ratio=0`、`max_channel_error=0`、`mean_abs_channel_error=0`。两份 report SHA-256 均为 `eb4da8278de22e74bbd0c81a7550ffa56fd784b1efe04b18c0c42e30f943265b`。

正式视觉报告：

- `sandbox/tmp/v2-closeout-final-godot-20260822-visual-b/report.json`；
- `sandbox/tmp/v2-closeout-final-godot-20260822-visual-c/report.json`。

### 5.5 Secret hygiene

收官扫描没有读取 `.secrets/**` 的实际内容，也没有打印环境密钥、session token、Authorization header 或私有 endpoint。最终候选源码/文档扫描覆盖 196 个文本文件；命中只来自动态变量、检测规则或显式测试 placeholder，确认存储的真实凭据为 0。此前同轮审查还覆盖 389 个二进制、62 个可访问输出和 3 个 SQLite；Docker 最终日志候选为 0。一个历史 sandbox coverage 目录因 Windows ACL/锁不可访问，未提升权限强行扫描，因此不把该目录宣称为已覆盖。

会话中曾明文出现过的 provider key 必须视为已泄露。任何未来真实调用前，用户应先在供应商侧撤销并轮换，再由 Quest Settings 面板写入替代值；不能继续使用旧值，也不能把它复制到文档、测试或新会话 Prompt。

## 6. 子系统影响

| 子系统 | v2 收官影响 | 保持的边界 |
|---|---|---|
| API | 未改变 `/api/v1` schema 或既有 `/api/v2` 默认行为；新 provenance 字段只出现在新冻结成果 manifest | 旧 manifest 无字段仍可 retain/discard；shadow 不授予权限 |
| 数据库 | additive migration 7 和不可变 side-ledger | migration 1–7 不得重写；后续只能 8+ |
| 恢复 | admission 后保存 baseline；旧 action/no-baseline 只记 `legacy_unobserved`；replay 消费持久数据 | replay 不扫描、不调用工具/模型/RAG/MCP |
| 成果审核 | final snapshot、manifest、provenance 和审核事件原子写；Godot 只读展示 | 仍由 Verifier/Evidence 与用户明确 retain/discard 决定 |
| 安全 | descriptor/path 双视图、mtime/ctime/ino/dev、bounded read、link/reparse 拒绝；Git/Docker secret ignore 加固 | 不声称对外部并发写入或任意子进程拥有 OS 级隔离 |
| Benchmark | formal、RAG、real-model 目录/标签继续分离 | 本轮 provider/model/embedding calls 全为 0 |
| Godot | provenance 审计提示自动换行且不越界；按钮状态不由提示控制 | golden PNG 未自动更新；固定 Windows 4.7.1 基线 |

## 7. 未完成与 v3 决策

以下事项没有被 v2 收官报告虚假标记为完成：

1. `artifact_review_required` 的既有默认仍为 false。若要所有写成果 Quest fail-closed，必须新增版本化行为模式，并决定无成果 Quest 的语义、旧 `/api/v2` 兼容和 workspace ownership。
2. migration 7 provenance 只是 compatibility shadow，不能声称 `verified`，也不能取代 `diff_scope`、Verifier/Evidence 或用户决定。
3. Quest history 尚缺 archive/unarchive 和失败原因到 event/Evidence/checkpoint/恢复动作的完整导航。
4. 没有 accepted real-provider evaluation；20 元仍不是跨 provider、跨数据库的全局财务总闸。
5. P2B Quest-bound RAG 必须使用 migration 8+；RAG 语料、许可、敏感等级、保留期与 egress 尚需用户决定。
6. P3B 真实 MCP 的 hash-to-spawn TOCTOU、OS/容器 containment、文件/网络权限和真实 server 兼容性尚未验收。
7. 当前只支持本地单节点；没有认证、多用户、PostgreSQL、durable queue、distributed fencing 或 WebSocket fan-out。
8. `storage.py`、`service.py`、Godot `main.gd` 仍过大；strict env、telemetry 可终止边界、依赖 lock、Docker base digest、SBOM 和 hosted CI 仍是工程债。
9. 根目录仍无 Git、许可证、tag 与公开演示媒体；不得擅自选择或发布。

## 8. Sol 最终验收

Terra 的实现结果没有被直接当作验收。Sol 已检查关键 changed paths、migration SQL/checksum、scanner/Gateway 句柄竞态防御、storage 原子事务、Service 错误边界、Godot UI 状态映射和实际证据；并独立完成 Ruff、115 项聚焦测试、91 项 secret/release/settings 测试、完整 605 项默认测试、coverage 数据库读取、formal/RAG/Godot report 哈希、Docker 宿主/容器源码哈希与 health 复核。

因此接受当前工作区为 **ProjectTown v2.0 本地开发收官基线**。接受范围不扩展到上述 v3 决策项、正式发布或任何付费/外部调用。

下一会话入口为 [`v3-handoff-prompt.md`](v3-handoff-prompt.md)。该 Prompt 已把本会话的需求、已交付能力、失败证据、迁移不变量、模型/密钥决策、未完成风险与推荐顺序做脱敏整合；不包含旧 key、私有 Base URL、session token 或完整 Prompt/Response。
