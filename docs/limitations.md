# Limitations

- SQLite is single-node and single-process; there is no HA or horizontal coordination.
- The safe default is loopback. Non-loopback deployments have no built-in authentication and need an authenticated proxy.
- WebSocket delivery is at-least-once, not exactly-once; clients must deduplicate and reconcile.
- No real LLM calls or unrestricted production tool execution are included. Phase
  1A includes only an unwired provider-neutral contract, deterministic fake and
  auditable candidate coordinator; it neither selects a provider nor adopts a
  candidate into the Planner. See [`v2-phase-1a.md`](v2-phase-1a.md).
- Phase 2A provides only explicit in-memory lexical retrieval and a synthetic,
  sandbox-only deterministic evaluation. It is not a Quest knowledge base,
  answer service, provider integration, embedding/vector-store implementation or
  real-model evaluation. RAG text remains untrusted and cannot become Evidence
  or grant tool permissions. Quest-bound provenance/replay would require a new
  additive migration 8 or later and separately accepted recovery design; see
  [`v2-phase-2.md`](v2-phase-2.md).
- P3A is only a default-off, fixture-only local stdio MCP Gateway Adapter. It
  requires application-side injection of fixed server descriptors; there is no
  settings panel, config-file loader or HTTP API for arbitrary command
  execution. It has no real third-party-server acceptance, remote MCP, OAuth,
  production subprocess isolation or OS-level network sandbox guarantee.
  Descriptor/schema drift is rejected and MCP results remain untrusted tool data,
  not Evidence; replay performs zero MCP calls. The existing Quest path Sandbox
  does not contain arbitrary subprocess filesystem or network access. Windows `taskkill` and POSIX
  `killpg` cleanup have fixture evidence only. See [`v2-phase-3.md`](v2-phase-3.md).
- The fixed local development/test file `.secrets/model-providers.local.toml` is
  ignored and excluded from Docker, but it is intentionally fail-closed and is
  not a production secret manager. Schema v3 stores one same-source provider
  triple: `base_url`, `api_key`, and `model`. OpenAI is restricted to its
  approved configuration; Qwen is restricted to the native DashScope Beijing
  workspace `/api/v1` base URL and `qwen-plus`. The Godot Settings panel is a
  default-off loopback-only development/test editor for that local file, not a
  provider client. Qwen is configurable but its real network path has not run
  or been accepted; DeepSeek remains unavailable. The independent Qwen cost
  profile has 0.5/20 CNY safety caps, which are not a paid-call authorization.
  No OpenAI/Qwen/DeepSeek network test is claimed by P2A or this later UI
  increment. Any replacement key must be rotated and entered directly by the
  user in the local panel, never recorded in docs, source, tests, logs or
  benchmark output.
- The opt-in `docker-compose.local-settings.yml` is only for one local
development/test Docker instance. It stores the provider triple in a Docker
named volume and mirrors only the session token to ignored host `.secrets`, but Docker
administrators and the same Windows user can still access their respective local data. It
  is not a production secret manager, cannot be made multi-node by this lock,
  and must not be used for a shared/public deployment. The base Compose remains
  route-free and secret-mount-free; the override uses a fixed local bridge and
  fails if its subnet conflicts.
- Artifact preview supports verified UTF-8 text files up to 256 KB. "Retain" keeps
  them in the Quest-owned sandbox; it is not an export into another project.
- Migration 7 records bounded workspace baselines, atomic `write_file`
  observations and artifact provenance as a compatibility shadow. It improves
  auditability but is not an ownership proof, malware verdict or replacement for
  Verifier/Evidence. Incomplete scans and historical Quests are explicitly
  labelled `unrecoverable_*` or `legacy_unobserved`; these labels do not change
  the existing opt-in artifact-review default or the user's retain/discard
  authority. See
  [`adr-0001-artifact-shadow-provenance.md`](adr-0001-artifact-shadow-provenance.md).
- Godot 4.7.1 project loading, the main scene, time-of-day rendering, live REST + WebSocket transport, read-only restoration, and a deterministic fixed-fixture visual-regression baseline were validated. The baseline covers controlled viewport/layout states, not exhaustive devices, platforms, accessibility settings or a substitute for manual product review.
- Docker Compose has been exercised locally on loopback. The release remains a
  single-node development deployment, not an authenticated public service.
- The project root has no source-code license yet. Third-party font licenses are included, but the author must choose the project license before presenting the repository as open source.
- A release video and Git tag are not included. This workspace has no Git metadata, so a release tag cannot be created or verified here.
- Godot Quest history has search, filters and pagination, but archive semantics and failure/recovery navigation remain incomplete.
