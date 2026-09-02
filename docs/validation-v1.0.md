# ProjectTown v1.0 最终验收报告

最终复验日期：2026-08-13  
环境：Windows、Python 3.12.13、Godot 4.7.1 stable、Docker Desktop / Compose 5.3.1  
版本：`1.0.0`

## 结论

ProjectTown 的 **v1.0 功能代码与本地 Docker 交付候选版已验证通过**。发布边界是
loopback、单节点、单进程 SQLite；系统不包含真实 LLM、内置公网认证或生产级多租户。

本次收官在旧验收基础上复查开发文档、后端、数据库、Godot、评测、Docker、CI、
文档和仓库卫生，并补上两个实际问题：

1. Evidence ID 过去未包含 Quest ID，相同 Quest 的最终验证可能发生跨 Quest 主键碰撞；
2. Godot 过去无法在重启后重新打开既有 Quest，`waiting_user` 成果审核旅程会中断。

两项均已修复并加入回归覆盖。公开 GitHub Release 仍需要作者完成许可证选择、Git
初始化/远端关联、`v1.0.0` tag 和演示视频；这些外部发布动作不在本报告的“已完成”内。

## 自动化质量门禁

| 检查 | 结果 |
|---|---|
| Ruff：`backend tests scripts` | 退出码 0，`All checks passed!` |
| Python 编译：`backend scripts` | 退出码 0 |
| Python 依赖一致性 | 退出码 0，`No broken requirements found.` |
| 全量 Pytest | 退出码 0，`97 passed, 1 warning` |
| 后端语句覆盖率 | `87.75%`，通过 `80%` 门槛 |
| 唯一警告 | 第三方 FastAPI/Starlette TestClient 的 httpx 适配弃用提示 |

执行命令：

```powershell
.\.venv\Scripts\python.exe -m ruff check backend tests scripts
.\.venv\Scripts\python.exe -m compileall -q backend scripts
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp sandbox/tmp/v1-final-sol-pytest `
  --cov=backend --cov=benchmark --cov-report=term-missing --cov-fail-under=80
```

覆盖范围包含 Goal Contract 两阶段确认、DAG/受限重规划、并发 Decision 与启动冲突、
Verifier/Evidence、原子工具回执、响应丢失、Checkpoint/重启恢复、Watchdog、预算、
高风险审批、成果冻结/预览/保留/丢弃、跨 Quest Evidence 隔离、WebSocket、Benchmark、
Godot 协议与发布配置。

## Docker 与数据完整性

- `docker compose config --quiet`：退出码 0；
- 镜像 `projecttown-projecttown`：本机实际构建，约 56.2 MB；
- 容器 `projecttown-projecttown-1`：`healthy`；
- 端口：仅 `127.0.0.1:8000`；
- `/health`：版本 `1.0.0`、数据库 `ok`；
- `/api/v2/health`：无中断 Quest、无模糊 action、无孤立 committed action；
- SQLite `integrity_check=ok`、外键检查为空、迁移 1–4 已应用；
- Compose 保持 UID 10001、只读根文件系统、`tmpfs`、移除 capabilities 和
  `no-new-privileges`，数据存于 Docker-managed volumes。

Docker 验证证明本地单节点候选版可运行，不代表已认证公网服务或高可用生产部署。

## Godot 客户端与恢复

使用 `Godot 4.7.1.stable.official.a13da4feb` 完成：

- 编辑器无界面加载和脚本解析，退出码 0；
- 主场景启动，退出码 0；
- 昼夜/云层平滑周期：`TIME_CYCLE_SMOKE_OK anchors=6 midpoints=3`；
- 隔离 Uvicorn 的 REST + WebSocket 联调：

```text
GODOT_API_SMOKE_OK quest=qv1_98fc65f65e78 events=20 evidence=4
LIVE_GODOT_BACKEND_OK quest=qv1_98fc65f65e78 status=completed events=20 evidence=4
```

- 当前 Docker 后端的只读重启恢复验证：

```text
RESTORE_SMOKE_OK quest=qv1_9df38a0e8841 events=18 evidence=4 artifacts=1
```

恢复验证会列出已有 Quest，选择 `waiting_user` 项并读取状态、Event、Evidence、成果和
真实预览；它不创建 Quest，也不执行 run、retain、discard 或 Decision。客户端对确认、
启动、暂停/恢复、Decision 和成果审阅的迟到响应均按来源 Quest ID 隔离。

成果审阅视觉夹具在场景入树前显式关闭 transport；最终 OpenGL 截图窗口内，Docker 日志
新增 `qv1_visual_review` HTTP/WebSocket 请求为 `0`，且没有 URL 解析错误或残留 Godot
进程。因此截图测试不会再用虚构 Quest 污染真实后端日志。

代表性画面已在 1280×720、1920×1080 和紧凑宽度下检查，并覆盖教程、成果审核和详情
窗口；这不是自动像素差异回归，也不代表所有 GPU、DPI 和窗口组合。

## 真实回放

`scripts/export_v1_replays.py` 使用隔离 SQLite、Sandbox、真实 `V1QuestService`、Gateway
和 Verifier 重跑：

| 场景 | 终态 | 关键证据 |
|---|---|---|
| normal | `completed` | 25 events、7 evidence、5 receipts |
| response-loss recovery | `completed` | 21 events、4 evidence、3 receipts，含模糊副作用协调 |
| watchdog loop | `waiting_user` | 16 events、3 evidence、3 receipts，含 `LoopDetected` |

回放目录为本机临时证据；仓库中的 `examples/replays/` 保留公开示例。循环场景保持真实
安全状态 `waiting_user`，没有伪造成失败终态。

## 正式 Benchmark

`benchmark/results/formal-v1.0/` 包含 30 个 Quest、B0–B4、七项消融与 4,320 条 raw
rows。使用 formal profile / seed 1729 独立再生成后，四个文件与 manifest 完全一致：

| 文件 | SHA-256 |
|---|---|
| `report.md` | `21ba42602336fd34015df50ef2f2bba24f90d0bf8645172c870c6d4418128f0b` |
| `results.csv` | `4f24fac7930945aaa7986e905cf5ac7f6393499fa8bd4caaa61162be6712d1c6` |
| `results.json` | `f8b5d380fc87a37f071b28f38d69988ad8938470da4dbbc3663583111a193d06` |
| `success.svg` | `5181d5fec3434925aa9ea9c85a688c5e169ce82065cb54a16c8509c9910af3d5` |

这些产物明确是 deterministic `runtime_simulation`，`model_calls=0`、`model_tokens=0`；
它们证明评测管线和故障矩阵可复现，不证明真实模型效果或生产性能。

## 安全与发布卫生

- 常见密钥、API key、私钥和本机绝对路径扫描：未发现命中；
- GitHub 候选文件没有超过 25 MB 的单文件；
- `.venv/`、运行数据库、Quest 工作区、临时验收产物、Godot 导入缓存、`.codex/backups/`
  和停用的 Luna 角色文件均被忽略；有效的 `.codex/` Sol–Terra 配置与项目级
  `.agents/` 工作流保留；
- Fusion Pixel Font 的 OFL-1.1 文本、上游字形许可证与第三方声明齐全；
- 项目自制代码与像素素材的根许可证尚待作者选择。

## 未完成的公开发布动作

1. 选择根项目许可证，并明确代码与项目自制美术的授权范围；
2. 初始化或关联 Git 仓库，检查首个提交，再创建 `v1.0.0` tag；
3. 发布 3–5 分钟演示视频及正常完成、恢复、循环阻断的最终媒体；
4. 在 GitHub Release 中附上本报告、Benchmark manifest 与已知限制。

因此最终状态为：**v1.0 代码与本地 Docker 交付候选版已验证通过；公开 GitHub Release
尚待作者完成许可证、Git/tag 与演示媒体。**

## 回滚

当前工作区没有 Git 元数据。本轮收官前备份位于：

- `sandbox/tmp/runtime-evidence-id-backup-20260813/`
- `sandbox/tmp/v1-quest-restore-backup-20260813-0745/`
- `sandbox/tmp/v1-quest-restore-backup-20260813-0815-followup/`
- `sandbox/tmp/v1-closeout-doc-backup-20260813/`
- `sandbox/tmp/v1-pytest-config-backup-20260813/`
- `sandbox/tmp/v1-visual-fixture-offline-backup-20260813-round2/`

回滚时停服，按文件相对路径从对应备份恢复；数据库若需回退必须整体恢复停服快照，不能
手工删除 migration。Docker 数据备份位于 `sandbox/tmp/docker-runtime-backup-20260813-0730/`。
