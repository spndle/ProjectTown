# ProjectTown v2 Phase 1D：Quest 历史检索与失败导航

日期：2026-08-20

状态：**已验收（不含归档）**。

## 目标与范围

本阶段为既有 Quest 控制台增加历史搜索、多状态筛选、稳定分页、失败原因导航和对应 Godot 交互状态。实现范围刻意限制为只读检索与展示：不改变创建、两阶段 Goal Contract 确认、执行、恢复、成果审核或 WebSocket 协议。

归档不在本次交付中，不能据此推断已有 archive/unarchive API 或数据库状态。

## 设计与实现

- `GET /api/v2/quests` 支持可选 `q`、重复 `status`、`offset`、`limit`。无查询参数时保留旧的返回路径与语义；有检索参数时返回页面 `items` 和完整 `total`。
- 服务与 `storage.py` 使用 SQLite 查询完成 Unicode casefold 搜索、状态筛选和确定性排序/分页，不把全量历史搬到 Godot 再过滤。
- `GET /api/v2/quests/{quest_id}/failure` 返回受限的失败摘要、可恢复性和安全导航信息，而非未处理异常、敏感工具参数或原始 provider 文本。
- `godot/scripts/api_client.gd` 使用 GET-only 历史/失败读取；`godot/scripts/main.gd` 实现搜索、12 种状态多选、上一页/下一页、总数、加载/空/错误/重试、过期请求 generation 丢弃、选中项保持和 `waiting_user` 审核提示。

## v1 不变量与接口影响

| 面向 | 影响 |
| --- | --- |
| API | `/api/v2/quests` 无参数兼容；新增参数和 failure 端点为只读。`/api/v1` 兼容面不改。 |
| 数据库 | 无 migration；不改写 migration 1–6。 |
| 恢复/事件 | 不写 Quest、event ledger、checkpoint、CAS 状态版本或回放数据。 |
| 成果审核 | 只展示 `waiting_user` 上下文；成果仍须预览后由用户显式 retain/discard。 |
| 安全 | 失败端点返回白名单化摘要；不信任 Agent 自报完成，不暴露密钥/Prompt/Response。 |
| Benchmark | formal-v1.0 deterministic runtime simulation 与真实模型评测均无语义变化。 |
| Godot | 不改变 WebSocket at-least-once 处理或 `(quest_id, sequence)` 去重；历史刷新与 WebSocket 状态独立。 |

## 验收证据

- 后端检索/失败导航聚焦验证：Python 测试连续两轮各 `17 passed`；transport 审计连续两轮各 `16 passed`。
- Godot 历史 smoke 连续三轮通过；独立 UI 验收还覆盖 2048、1920、1280、720 四个布局宽度、离线/编码探针及状态管理探针。
- 传输审计确认新增读取为 GET-only，且不影响原有 cursor/去重路径。
- 最终门禁发现并修复了同一 `created_at` 下无参列表与分页查询的并列排序不一致：两条路径现在都按 `created_at DESC, quest_id DESC`，并新增确定性时间戳并列回归；API/存储聚焦验证连续三轮各 `8 passed`。
- 修复后 Ruff、compileall、pip check 均 exit 0；排除下述当前 Godot 运行时阻塞项后，全量 Python 连续两轮各 `270 passed, 2 skipped`，覆盖率连续两轮均为 `89.07%`（门槛 85%）。

相关本机证据目录：`sandbox/tmp/p1d-*20260820/`。

## 回滚

可回滚本阶段 API 参数、failure 路由和 Godot 历史展示代码；不会留下新数据库数据或 migration。无参数的 Quest 列表、Quest 生命周期、事件/恢复和成果审核仍可按 v1 行为运行。

## 限制与未决事项

归档的业务语义需要用户决定后才可实现。建议的保守方案是：仅已终态且没有待审核成果的 Quest 可归档；默认列表隐藏归档项；禁止归档 `running`、`recovering`、`waiting_user`、`discarding`；首版不提供 unarchive。该方案尚未获得用户确认，故没有实施。

后续归档若获批准，须以新增版本化 migration、旧数据库回归、归档筛选 API、审计事件以及恢复/成果审核冲突测试实现，不能把 SQLite/进程内锁直接扩展成多节点方案。

本轮最终复验期间，一个由并行动态测试启动的 Godot GUI 进程无法在当前权限下终止，随后唯一动态 history UI Pytest 在干净项目副本中仍以 Godot signal 11 退出；该失败没有被计为通过。它不推翻此前同一 Godot 源码上的三轮 history smoke 和独立 UI 验收，但需要在关闭残留进程或重启 Godot 会话后再跑一次最终动态门禁。
