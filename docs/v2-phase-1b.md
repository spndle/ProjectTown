# ProjectTown v2 Phase 1B 验收记录

日期：2026-08-20

状态：**通过（B-min）**

## 已交付范围

- 新增纯标准库、OTel-compatible 的内部 span/metric 记录边界；未接入 OpenTelemetry SDK、OTLP 或外部 collector。
- 默认应用使用真正的 `NoOpTelemetry`：不启动线程、不构造记录，也不读取模型派生摘要。
- HTTP 请求由服务端生成 32 位小写十六进制 `X-Correlation-ID`；保留既有 `X-Request-ID` 行为，但不将其原值写入遥测。
- `ModelCallCoordinator` 可选注入遥测，记录 call/attempt、结果、token、cost 与 wall latency 摘要；仍未接入默认 Quest Planner，也不会采用或执行模型候选。
- exporter 使用有界非阻塞队列、确定性 trace 采样、故障计数、超时熔断和有界关闭；遥测失败不能改变模型结果或 Quest 状态真相。
- 日志与 debug 错误响应不再回显未处理异常的原始文本。
- 未新增 migration、公开 API、生产依赖或外部网络调用。

## Sol 验收证据

- 高风险架构复审：最终结论 `ACCEPT`；关闭了 Record 构造/派生读取反向影响业务、异常文本泄漏、emit/close 竞态、关闭时限与 in-flight 终态等 P0/P1。
- 聚焦测试连续三轮：每轮 `22 passed`，exit 0。
- 默认 Pytest 连续两轮：每轮 `178 passed`，exit 0。
- 覆盖率连续两轮：每轮 `88.67%`，门禁 `>= 85%`，exit 0。
- Ruff、compileall、pip check：均 exit 0。
- 10,000 条遥测压力探针：`submitted=10000 = dropped 9999 + timeout 1`，关闭后 `queued=0`、`in_flight=0`，计数冻结。
- exporter raise、永久阻塞、early-close 与关闭异常均完成故障注入；业务结果不变。
- 真实 Uvicorn 启动三次：每次四个请求，Correlation ID 格式正确且逐请求唯一，`X-Request-ID` 兼容。
- 动态 secret canary：隔离 SQLite、遥测 sink、日志与临时 Benchmark 输出命中 0；阳性样本可以被扫描器检出。
- 旧 migration 4 数据库升级到 migration 5、重开、`integrity_check` 与 FK 检查通过；migration 1–4 未改写。
- 最终源码重新构建 Docker 镜像，两轮全新隔离容器的 `/health`、`/api/v2/health` 均为 200；migration 1–5、SQLite integrity/FK 与默认 `v1_model_calls=0` 均通过。
- Godot 4.7.1 editor、API、昼夜周期和右侧面板实机 smoke 通过；最后窄修复未触碰 Godot，前后 Godot 源哈希一致。
- formal-v1.0 四个 manifest artifact 哈希全部匹配，正式 deterministic Benchmark 未被遥测污染。

证据根目录：

- `sandbox/tmp/p1b-baseline-20260820/`
- `sandbox/tmp/p1b-impl-20260820/`
- `sandbox/tmp/p1b-sol-review-20260820/`
- `sandbox/tmp/p1b-acceptance-20260820/`

## 明确边界与剩余风险

- 本阶段验证的是内部 OTel-compatible contract，不代表已经接入 OTel SDK、collector、告警平台或生产保留策略。
- Correlation 当前覆盖 HTTP 请求内上下文与显式注入的模型协调器；不宣称跨现有 Quest `ThreadPoolExecutor` 的端到端传播。
- 模型 wall latency 是派生遥测，不是新的持久化审计字段。
- 阻塞 exporter 最多可能留下一个 daemon helper 短暂存活；有界关闭已返回，pump 会停止新增 export，Quest 与冻结后的计数不受影响。
- `telemetry_enabled`、队列、timeout 与采样配置为后续显式 exporter 接线预留；没有显式注入时应用保持 NoOp。

## Phase 1C 进入门

Phase 1C 是单一真实 Provider 的显式 opt-in，开始前必须由用户确认：

1. 供应商与具体模型；
2. 单次和总评测硬预算；
3. 密钥只从何种外部秘密源注入；
4. 允许出境的最小数据范围；
5. Prompt/Response 的保存或禁止保存策略。

在这些决定明确前，不调用付费 API，不上传 Quest 内容，也不把离线 deterministic simulation 当作真实模型评测。
