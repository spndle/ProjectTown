# Benchmark

Run `scripts/run_benchmark.ps1 -Profile formal -Output benchmark/results/formal-v1.0 -Seed 1729`, or the equivalent Python module command. The formal profile contains 30 quests, B0-B4 baselines and seven ablations. Nine key quests have five repeats and the remaining 21 have three repeats, producing 4,320 raw rows. Every run ID, isolated workspace ID, seed and configuration fingerprint is recorded.

Metrics include success, progress, false completion, plan drift, loop rate, constraint violations, recovery, duplicate side effects, tool calls, messages, `pass^3`, and deterministic `latency_ms_simulated`. The generated report is a `runtime_simulation`: `model_calls=0` and `model_tokens=0`; it does not measure wall-clock performance, a real LLM, Godot, network, or a production tool. Do not interpret it as a deployment performance claim. See the [committed report](../benchmark/results/formal-v1.0/report.md) and [checksums](../benchmark/results/formal-v1.0/manifest.json).
