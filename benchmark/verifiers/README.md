# Local benchmark verifiers

Verifier specifications are catalog metadata and are evaluated by the deterministic
local simulator. Gold constraints are supplied only to the external scorer; they
are never included in `agent_view` or strategy input. No user code, network, or
LLM is executed. Every generated row is marked `runtime_simulation=true`.
