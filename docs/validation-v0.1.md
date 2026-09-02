# ProjectTown v0.1 验证报告

> 历史记录：本页只描述 2026-08-05 的 v0.1 基线，不代表当前 v1.0 验收状态。当前结果见 [`validation-v1.0.md`](validation-v1.0.md)。

验证日期：2026-08-05  
验证环境：Windows、Python 3.12.13

## 自动化结果

| 检查 | 结果 |
|---|---|
| Ruff 静态检查 | `All checks passed` |
| Pytest | `18 passed` |
| 后端语句覆盖率 | `88%` |
| 示例 JSON 解析 | 通过 |
| PowerShell 启动脚本语法 | 通过 |
| Godot 文件/API 契约静态测试 | 4 项通过 |

测试覆盖：

- 健康检查和模板列表；
- 三个固定 Quest 的创建、异步执行、产物、里程碑和 Trace；
- 工具失败后的结构化错误链；
- Sandbox 工作区及工具路径逃逸拦截；
- 重复启动冲突；
- SQLite 重启后的 Quest 与 Trace 持久化；
- Godot 主场景、脚本引用、REST 路由和状态映射。

## 真实 HTTP 联调

使用独立 Uvicorn 进程完成了一次 `project_brief` Quest：

```json
{
  "health": "ok",
  "status": "completed",
  "progress": 100,
  "milestones": 3,
  "traces": 15,
  "artifact": true
}
```

联调链路：

```text
GET /health
→ POST /api/v1/quests
→ POST /api/v1/quests/{id}/run
→ GET /api/v1/quests/{id}
→ GET /api/v1/quests/{id}/traces
→ 检查 Sandbox 产物
```

## 已知验证边界

当前执行环境没有 Godot 可执行文件，因此未进行引擎级场景启动和截图验证。Godot 交付已完成静态场景、脚本引用及后端 API 契约检查；需要在安装 Godot 4.3+ 的机器上完成首次运行验收。

测试过程中生成的临时数据库、Quest 工作区、覆盖率文件和缓存均已从交付目录清理；它们可通过测试或运行项目重新生成。
