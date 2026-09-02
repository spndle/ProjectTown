# ProjectTown v2 Phase 1E：确定性的 Godot 截图回归基线

日期：2026-08-20

状态：**本地 Windows golden 基线、比较 harness 与自动 runner 已验收；GitHub-hosted Windows workflow 已配置但尚未实际远端运行，不能预先宣称 CI 通过。**

## 目标与范围

本阶段建立离线、确定性的 Godot 4.7.1 截图回归，以防止控制台布局、教程、历史、失败导航、成果审核和恢复界面发生未审查的视觉漂移。它不改变 Quest 后端协议、数据库、模型调用或公开发布流程。

## 设计与实现

- `godot/tests/capture/` 提供 `main`、`tutorial`、`history`、`failure`、`artifact_review_waiting_user`、`restore_waiting_user`、`settings` 七个固定夹具；每个覆盖 1280×720、1920×1080、900×720，共 21 张截图。
- 夹具在加入场景前禁用 transport、使用固定合成数据和时间、冻结动画，并把输出限制在项目 sandbox，从而不依赖后端或网络。
- `godot/tests/goldens/windows/` 保存 21 个 canonical PNG；`manifest.json` 固定 Godot `4.7.1.stable.official.a13da4feb`、`gl_compatibility`、视口、PNG/RGBA 哈希、字体和受保护项目资源哈希，以及零容差比较阈值。
- `tests/visual/harness.py` 以标准库严格验证 PNG chunk/CRC/解压和像素；先做哈希/字节快速路径，再对变化的候选做逐像素比较与 diff。golden 只能在受控的显式接受流程中更新。
- `scripts/run_godot_visual_regression.py` 与 `.github/workflows/visual-regression.yml` 配置 Windows fail-closed 自动捕获路径，固定 Godot 二进制和哈希，并拒绝 `PROJECTTOWN_UPDATE_GOLDENS`。本地 runner 已完成实机验收；hosted workflow 尚未有远端运行证据。

## v1 不变量与影响

| 面向 | 影响 |
| --- | --- |
| API/数据库 | 无 API、迁移或持久化变化。 |
| 恢复/成果审核 | 截图覆盖 `restore_waiting_user` 与成果 retain/discard 前的预览状态，但不写入 Quest 或改变审核决策。 |
| 安全 | 夹具离线，输出路径必须被 sandbox confinement 检查；不读取密钥、不调用 provider、不保存 Prompt/Response。 |
| Benchmark | 不触碰 formal-v1.0 或真实模型评测结果；视觉基线是独立的 UI 工件。 |
| Godot/WebSocket | 检查 Godot 视觉状态，不修改 at-least-once 投递和 `(quest_id, sequence)` 去重契约。 |

## 已验收的本地证据

- 夹具扩展至当前 manifest 所列范围后，尚未在本文件记录新的完整 runner 或哈希证据；不能将下面的早期本地记录当作当前 manifest 的验收。
- 早期本地记录确认了固定夹具、尺寸、夹具标记、离线条件，以及一像素改动会按预期失败并产生可解析 diff；canonical 未被改写。
- 早期 harness 独立复核连续三轮为 `19 passed, 1 skipped`；Ruff、compileall、pip check 均 exit 0。该记录及其中的 manifest、runner、测试和 workflow 哈希均早于当前 manifest，未在此处重新声明为当前证据。
- 早期 runner 聚焦测试连续三轮各 `12 passed`，并有受控 Godot 4.7.1 Windows 二进制的完整 runner 实机记录。夹具扩展后的完整 runner、canonical 哈希及其聚合哈希须重新运行后才能作为当前证据报告。

本机证据目录包括 `sandbox/tmp/p1e-*20260820/`；这些记录支撑本地基线，不替代 hosted CI 的实际运行。

## 回滚

若需停用视觉门禁，可停止调用本地 runner/CI workflow，而保留 canonical 以便审计。不要静默重写 golden；若撤回整个能力，应一起移除夹具、manifest、harness、runner 与 workflow，并保留一次可复现的旧基线归档。任何 golden 变更都须有视觉审查、manifest 更新和明确接受理由。

## 限制与未决事项

- 目前确认的是本机 Windows 环境的 Godot 4.7.1/`gl_compatibility` 基线；其他操作系统、GPU、驱动、缩放/DPI 和渲染后端未被等同验证。
- GitHub-hosted Windows workflow 尚未实际远端运行；当前只能称它“已配置、未远端验证”。本地 runner 的通过不能替代 hosted runner 对执行环境、下载和 artifact 上传的验证。
- 已验收 runner 之后的一次并行终验造成 Godot 进程冲突和 signal 11；独立重跑在解析阶段按设计 exit 2、未改写 candidate 或 golden。当前权限无法终止该轮残留的 GUI 进程，因此这次失败不计作 runner 回归通过，须在清理进程或重启会话后串行复跑。
- zero-tolerance 像素策略会对字体、驱动和 Godot 版本漂移敏感；升级引擎、资源或渲染器时必须先做受控审查，不可直接覆盖 golden。
