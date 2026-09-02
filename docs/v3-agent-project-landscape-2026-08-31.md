# ProjectTown v3 Agent 项目对标与功能裁决（2026-08-31）

> 本文只使用项目官方 GitHub 仓库的公开 README/架构说明。Stars 是截至
> 2026-08-31 的易变快照，只用于发现有广泛使用信号的样本，不等于质量、适用性或应复制的需求。
> ProjectTown 的裁决标准是：能否改善本地单用户任务的可执行性、可验证性与控制感；宁缺毋滥。

## 1. 高星样本

| 项目 | Stars 快照 | 官方仓库显示的核心能力 | 对 ProjectTown 的有效启示 |
| --- | ---: | --- | --- |
| [AutoGPT](https://github.com/Significant-Gravitas/AutoGPT) | 187.0k | 对话转 Agent、可视化 block workflow、run/cost/attention、部署与集成 | 用户首先需要“直接完成工作”的入口和运行状态，而不是内部模块列表。其 marketplace、trigger 与大量集成不适合 v3。 |
| [Dify](https://github.com/langgenius/dify) | 153.9k | visual workflow、RAG、Agent、model management、LLMOps、API | 资料→检索→产物→观测的连续体验有价值；数百 provider/tool 和协作 workspace 不应复制。 |
| [OpenHands](https://github.com/OpenHands/OpenHands) | 85.7k | 本地/远端 agent backend、统一 Canvas、automation、明确 sandbox 警告 | 单一控制面与明确运行边界有价值；任意代码代理、schedule/webhook 和多 backend 超出本地受控写入范围。 |
| [AutoGen](https://github.com/microsoft/autogen) | 60.7k | event-driven runtime、AgentChat、扩展、Studio、Bench | 分层 runtime 与 benchmark 有参考价值；官方已标记 maintenance mode，说明 stars 不能替代维护状态判断。默认多 Agent 不是 ProjectTown 缺口。 |
| [CrewAI](https://github.com/crewAIInc/crewAI) | 57.9k | Crews 与精确 event-driven Flows、HITL、结构化输出 | 精确 Flow 应先于自主 Crew。ProjectTown 已有确定性 DAG/HITL，不应再引入角色式多 Agent 框架。 |
| [LlamaIndex](https://github.com/run-llama/llama_index) | 51.9k | 文档摄取、索引、RAG、agentic workflow、模块化 integrations | 显式资料选择、引用与导出值得补齐；vector DB、OCR 平台和 300+ integrations 当前没有证据支持。 |
| [Agno](https://github.com/agno-agi/agno) | 42.0k | Agent API、storage、human approval、observability、RBAC、scheduling | approval、run history 与 audit 是核心；ProjectTown 已有更窄实现。多租户、RBAC、消息平台和调度不属于单用户本地目标。 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 40.8k | durable execution、HITL、memory、stateful workflow、debug/eval | 可恢复、确定性副作用和人工门禁是正确方向；ProjectTown 已有 ledger/checkpoint/replay，不需要换框架。 |

## 2. 当前用户真正能使用什么

| 能力 | 当前入口与状态 | 能解决的问题 | 裁决 |
| --- | --- | --- | --- |
| Quest 创建、确认、运行 | Godot Quest Console + `/api/v2`，默认可用 | 把明确目标变成可观察、可暂停恢复的本地任务 | 保留为当前核心产品能力。 |
| 状态、轨迹、事件和 Evidence | Godot + REST/WebSocket | 回答“运行到哪里、为什么失败、证据是什么” | 保留；这是 ProjectTown 相对普通 Agent demo 的主要价值。 |
| pause/resume/人工 decision | Godot + `/api/v2` | 高风险步骤由用户决定，不信任 Agent 自报 | 保留。 |
| artifact preview/retain/discard | Godot + artifact review API | 用户在保留成果前查看当前 bytes，并可安全丢弃 | 保留。 |
| 显式本地资料→plan/report/README/PDF | `run_v3_material_workflow.py`，离线 CLI | 从限定资料得到确定性、带引用的建议与导出 | 保留，但当前是工程入口，不应宣传为统一产品流程。 |
| 3A preflight→3B proposal→3C controlled write | 多个 create-only CLI | 在精确授权前冻结目标、完整 post-image、备份与 receipt | 保留为安全底座；不应压缩成绕过记录的一键写入。 |
| 3D loopback Web | `/v3`，默认关闭，只接受预授权 binding | 在浏览器检查/执行一个已发布操作 | 保留窄纵切；不能称为通用任务/资料 UI。 |
| provider settings、fixture MCP、lexical RAG、benchmark | 默认关闭、未接线或维护入口 | 当前没有完成用户问题闭环 | 冻结或移出普通用户主路径，不删除兼容代码，不宣传为可用产品功能。 |

当前强项是受控执行和证据链。当前最大产品缺口不是缺少 Agent 角色，而是 v3 能力分散：
用户无法从一个入口理解“现在有哪些证据、哪个 gate 被阻断、下一步该做什么”。

## 3. 缺失功能裁决

### 现在实现

1. **Phase 3E 两轮 release-candidate 协议**：独立、additive、create-only，绑定 Round 1
   disposable controlled apply/reconcile/restore 和 Round 2 report preview/citation/export。
2. **只读 Phase 3E workspace status projection**：从既有 records 派生当前 gate、blockers 和下一动作；
   不创建授权、不执行 Apply、不保存新的可变真相。
3. **独立 Reviewer、User RC、VERSION、Distribution gates**：任何工程或 Study PASS 都不能自动跨越下一门。

### 后置整合，不在 3E 实现

- 一个面向用户的 Local Workspace Task 入口，统一显式资料选择、grounded suggestion、preview/export
  与已有 Quest audit/recovery。应通过 projection/adapter 复用既有 records，不写 migration 8，不重写 v1/v2。
- Godot 继续作为兼容/演示视图；未来是否由轻量 Web 成为主入口，需要 3E 真人证据支持。

### 冻结或拒绝

| 功能 | 决定 | 理由 |
| --- | --- | --- |
| 默认多 Agent、动态角色群聊 | 拒绝 | 没有消融证明优于当前 deterministic baseline；增加成本、错误面和解释难度。 |
| marketplace、45+ SaaS integrations | 拒绝 | 与单用户本地资料任务无直接关系，扩大 secret、权限和网络风险。 |
| schedule/webhook/always-on automation | 后置 | 当前尚未证明一次性本地任务闭环的用户价值，不应先扩展触发面。 |
| multi-tenant/RBAC/远端部署 | 拒绝于 v3 | 产品明确是 loopback、单用户、单节点 SQLite。 |
| 真实 provider、真实 MCP、Quest-bound vector RAG | 继续冻结 | 需要独立隐私、成本、沙箱、质量和真人价值门禁；不能借 3E 解冻。 |
| 通用浏览器路径选择/授权创建 | 继续冻结 | 会扩大路径、CSRF、授权和真实目标写入表面；3D 只允许预授权 binding。 |
| 删除 v1 API、Godot、分阶段 CLI | 不做 | 它们分别承担兼容、当前用户入口和安全记录链；表面重复不等于可安全删除。 |

## 4. 价值结论

ProjectTown 已能解决“本地任务如何可审计、可恢复、由用户控制”的问题；对需要 Evidence、暂停恢复、
成果审核和受控副作用的用户具有真实工程价值。它尚不能完整解决“普通用户从本地资料直接获得并安全应用
一个可用成果”的端到端问题，因为资料、Quest、导出和 Apply 仍是分离入口。

Phase 3E 的正确目标不是再增加技术名词，而是验证现有最小闭环是否能被人直接执行、理解并接受。
若两轮真人证据仍要求 Reviewer 重新解释流程，应回到对应阶段修复；不得用更多框架、Agent 或集成掩盖。
