# ProjectTown Godot 客户端（v3.0）

这是 ProjectTown 的可视化小镇客户端。仓库已自带像素背景、云层、建筑、环境、角色、肖像与开源中文像素字体，运行时不需要额外下载美术资源。

## 运行

1. 启动 ProjectTown FastAPI 后端（默认 `http://127.0.0.1:8000`）。
2. 使用 Godot 4.3 或更高版本导入本目录中的 `project.godot`。
3. 按 F6/F5 运行，或在命令行执行 `godot --path godot`。

如果后端使用其他地址，在 `main.tscn` 根节点的 `server_base_url` 属性中修改。

## v1.0 交互

- 选择 Quest 模板，或填写自定义目标；
- 客户端重启后，可在“历史 / 继续任务”中重新打开已有 Quest；恢复操作只读取状态、轨迹、证据和成果，不会自动运行、保留或丢弃；
- 客户端自动为每个 Quest 创建独立工作区，避免覆盖或误删其他任务文件；
- 点击“创建 Quest”，在 Goal Contract 面板复核目标、约束、非目标、验收标准与预算摘要；
- 再点击“确认并运行”，客户端会带状态版本确认合约后启动；
- 查看 DAG 里程碑、预算状态、Event 与 Evidence；
- 最终验收通过后，直接逐个预览真实成果内容；确认无误可保留，或在二次确认后安全丢弃；
- 对运行中任务使用暂停/恢复；`waiting_user` 必须先通过 API 提交明确决策；
- NPC 会映射 `draft/planned/running/verifying/replanning/waiting_user/paused/recovering/completed/budget_exhausted/failed` 状态。

客户端优先使用 WebSocket 的 ordered-at-least-once 事件流，按 `sequence` 去重；断线时继续通过 REST 拉取 Quest、Event 和 Evidence。Godot 4.7.1 已完成项目加载、主场景、真实 Uvicorn REST + WebSocket 联调，并从后端只读恢复现有 `waiting_user` Quest、事件、证据、成果和预览。代表性分辨率、教程、成果审核和详情窗口也完成了画面检查；这不等同于全设备像素差异回归。
