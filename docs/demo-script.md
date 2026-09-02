# Demo script

1. Start the local API with `scripts/run_v1.ps1`.
2. Create a draft on `/api/v2/quests`, review and confirm its Goal Contract, then run it and inspect status, evidence and events.
3. Replay `examples/replays/normal.json`, `recovery.json`, and `loop-blocked.json` to explain completion, checkpoint recovery and watchdog blocking.

The JSON traces are exported from the real v1 service/storage/gateway/verifier path and retain ordered event and evidence summaries. They are reproducible test artifacts, not video, screenshots, real-LLM traces, or production performance measurements.
