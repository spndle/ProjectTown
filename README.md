# ProjectTown 3.0.0

ProjectTown turns a goal into an observable, replayable Quest runtime. Version 3.0 exposes the Goal Contract and runtime pipeline on `/api/v2`; `/api/v1` remains a compatibility API.

The v2.0 development stream is now closed as a local, single-user,
single-process development and portfolio baseline. It adds the isolated model
adapter/evaluation foundations, deterministic RAG, fixture-only local MCP,
history/visual work and migration 7 compatibility-shadow artifact provenance,
without enabling paid model calls or claiming a public/production release. See
the [v2 closeout](docs/v2-closeout.md) and the reusable
[v3 handoff prompt](docs/v3-handoff-prompt.md).

The user-approved [v3 product-direction charter](docs/v3-product-direction.md)
defines a local personal workspace for source-grounded task suggestions. Phase 0
provides the offline metadata-only material-set inspector. Phase 1 now adds an
isolated offline CLI vertical slice for explicit UTF-8 `.md`, `.txt`, `.json`,
and `.py` selections: draft confirmation, deterministic source-grounded plan,
report, or README suggestions, frozen preview, create-only export, and external
session recovery. See the [Phase 0](docs/v3-phase-0.md) and
[Phase 1](docs/v3-phase-1.md) contracts. The separate [Phase 2](docs/v3-phase-2.md)
external Study/Trial/Summary record tooling has passed offline engineering validation; it
strictly binds the fixed T001–T010 candidate manifest but does not turn its candidates
or synthetic fixtures into human evidence.

The versioned PDF material path can produce a readable frozen preview and a
create-only offline PDF with `pdf-export`, while the Result JSON remains an
engineering record and the existing Markdown export keeps its original byte
semantics. Automated exporter and Trial-binding checks remain engineering
evidence; the retained human evidence and its scope limits are recorded
separately. See [the T001 product-fix report](docs/v3-t001-pdf-product-fix.md)
and [the Phase 2 closeout](docs/v3-phase-2-closeout.md).

This Phase 1 engineering slice has passed its frozen offline Python validation;
that is not proof of user value or a public v3 release. A default-off native-loopback
Web UI now covers verified-task browsing and bounded offline authoring/export; real-target
Apply without an exact per-operation authorization, Quest/database integration, and
authenticated external-user provenance remain unavailable. On 2026-08-30 the user explicitly replaced the
full ten-task gate with a two-round, scope-limited longitudinal acceptance based on the
retained T001 v3 and T002 v9 plan trials. The old ten-task Summary contract remains
unchanged. Phase 3A provides a verified read-only, external ApplyPlan preflight for a
selected README target. Phase 3B now adds a separately versioned, external create-only
`ExecutableProposal` containing canonical Base64 for the complete deterministic
post-image; both stages report `write_performed=false` and neither writes the target.
Phase 3C now adds an additive local controlled-write core and CLI. It has been
engineering-verified only on disposable external fixtures: an exact per-operation
authorization binds one target and proposal; backup and evidence remain external and
create-only; replacement is staged in the target directory; unknown outcomes fail closed
into reconciliation; and restore requires a separate authorization. The metadata contract
is limited to Python-visible permission bits and does not prove Windows ACLs. This does
not authorize writing any real user target. Every real target still needs a new exact
authorization. Phase 3D now adds a default-off, native-loopback-only,
pre-authorized operation vertical slice with an additive UI/API and versioned
external binding/idempotency evidence; it has been engineering-verified only on
disposable fixtures. Phase 3E v4 has a create-only, record-only protocol and CLI for
exactly two rounds: the same Participant completed R1/R2, each round has an
independent `EngineeringAcceptanceV4` `PASS`, and the separately recorded User RC is
`ACCEPT`. Its canonical status is `hold_for_version_gate`; it does not authorize a
VERSION change, tag, Distribution, real-target Apply, or Restore. Phase 4B adds a
bounded, default-off task/material authoring UI, and Phase 4D adds only a verified
bind-only handoff; neither authorizes target writes. Broader workspace automation,
every real-target write authorization, and Docker 3D remain blocked. The
[Phase 3A–3E development plan](docs/v3-phase-3.md),
[Phase 3E engineering contract](docs/v3-phase-3e.md),
[Phase 4 roadmap](docs/v3-phase-4.md),
[Phase 4A read-only Local Workspace Task Workbench](docs/v3-phase-4a-local-workspace-task.md), and
[Phase 4B local candidate authoring slice](docs/v3-phase-4b-local-workspace-task.md), and
[start-prompt-only handoff](docs/v3-phase-3e-study-handoff.md) record those boundaries. See
[the Phase 2 closeout](docs/v3-phase-2-closeout.md).

## Quick start

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\scripts\run_v1.ps1
```

If local PowerShell policy blocks repository scripts, start the same app directly with `.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --env-file .env`, or invoke the trusted script in a process-scoped `-ExecutionPolicy Bypass` shell.

The supported v1/v2 launcher may load `.env` as application configuration. Phase 3 tools, agents, and workflows must not open, scan, or echo `.env*` files themselves; inherited configuration does not authorize provider, external MCP, network, Apply, or Publish activity. The precise boundary is recorded in [`docs/v3-phase-3.md`](docs/v3-phase-3.md).

The local service listens on `http://127.0.0.1:8000`; check `/health` and `/docs`. Godot 4.7.1 has loaded the project and completed live REST + WebSocket and read-only Quest-restore smoke runs. Docker Compose has also been built, started and health-checked on loopback; the Docker-only path is in [`docs/quickstart.md`](docs/quickstart.md).

## Runtime loop

`POST /api/v2/quests` creates a draft Goal Contract. The caller must confirm its state and contract versions before `run`; the runtime then derives a milestone DAG, routes actions through the Gateway, verifies artifacts and criteria, and records evidence, events, checkpoints, recovery, watchdog, budget, and HITL transitions. WebSocket event delivery is at-least-once (clients deduplicate sequence numbers), not exactly-once.

```text
create draft → review/confirm Goal Contract → run DAG → verify Evidence → complete
                                           ↘ pause / decision / recover
```

The compatibility surface under `/api/v1` supports templates and Quest CRUD/run/traces for existing clients. The Godot client can list and reopen persisted Quests after restart; reopening is read-only until the user explicitly chooses a control or artifact-review action.

## Architecture

The single event-ledger write path is the source of truth. Gateway policy and sandbox path checks precede tool execution; Verifier decisions are independent of agent claims. SQLite is the default persistence layer and release scope is one node/one process. The unwired Phase 1A foundation keeps strict provider-neutral contracts and auditable, budgeted candidate records outside Planner/API behavior. Phase 1C now has isolated OpenAI and native DashScope Qwen adapters, but only offline/Mock contract evidence exists: no real provider call or paid evaluation is accepted. Phase 2A separately adds an in-memory, deterministic lexical RAG foundation and offline synthetic evaluation; it is not connected to Quest, Evidence or recovery. P3A adds a default-off, fixture-only local stdio MCP adapter that maps fixed discovered descriptors to the existing Gateway; it is not a real-server or complete Phase 3 acceptance. Migration 7 records bounded workspace snapshots, atomic file observations and artifact bindings as a compatibility shadow; it cannot claim `verified` or replace Evidence. The default-off local Quest Settings control plane can edit development/test OpenAI or Qwen triples (`base_url`, `api_key`, `model`) without invoking a provider. See [`docs/architecture.md`](docs/architecture.md), [`docs/adr-0001-artifact-shadow-provenance.md`](docs/adr-0001-artifact-shadow-provenance.md), [`docs/v2-phase-1c.md`](docs/v2-phase-1c.md), [`docs/v2-phase-2.md`](docs/v2-phase-2.md), and [`docs/v2-phase-3.md`](docs/v2-phase-3.md).

## Evaluation and tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend tests scripts
.\scripts\run_benchmark.ps1 -Profile formal -Output benchmark/results/formal-v1.0 -Seed 1729
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_v1_rag.py tests/benchmark/test_rag_evaluation.py
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_v1_provenance.py tests/recovery/test_v1_migration7.py tests/integration/test_artifact_review.py
```

The committed formal benchmark contains 4,320 raw rows over 30 quests, B0-B4 baselines, and seven ablations. Its outputs are deterministic `runtime_simulation` artifacts (`model_calls=0`, `model_tokens=0`), not real LLM or engine measurements. Phase 2A RAG evaluation is a separate provider-free synthetic suite that writes only to `sandbox/tmp`; it must not be reported as formal runtime or real-model evaluation. See the [formal report](benchmark/results/formal-v1.0/report.md), [artifact manifest](benchmark/results/formal-v1.0/manifest.json), [`docs/benchmark.md`](docs/benchmark.md), [`docs/v2-phase-2.md`](docs/v2-phase-2.md), and the [v1.0 validation report](docs/validation-v1.0.md).

The code and local deployment candidate are validated. The project source is licensed
under the [MIT License](LICENSE); third-party font notices remain separate in
[`godot/assets/fonts/`](godot/assets/fonts/). The Git repository is configured with
`origin/main`, and the current closeout includes green hosted CI plus two reviewed Windows
visual-regression runs. The user-authorized T10 scope is limited to the `v3.0.0` tag and a
public GitHub Release with no attachments. No binary, media, or package artifacts are
authorized. Git/CI status does not itself create a tag, change VERSION, or authorize a
release.

## Repository map

- `backend/`: FastAPI v1 compatibility and v2 runtime
- `godot/`: visual client with engine-level and live transport smoke coverage
- `examples/`: quest inputs and runtime-generated replay JSON
- `scripts/`: local server and benchmark launchers
- `docs/`: quick start, architecture, benchmark, limits, development and demo notes

## Compatibility and limits

The supported deployment is local, loopback-bound, single-node SQLite. Non-loopback use has no built-in authentication and requires an authenticated proxy. There are no accepted real LLM calls or unrestricted production tool execution; providers remain default-off and isolated from Quest execution. P2A RAG is an offline, unconnected retrieval/evaluation component, not an answer service or Quest knowledge base. P3A local MCP is fixture-only, default-off and requires explicit application injection of fixed server descriptors; it adds no user-facing arbitrary-command API and is not a production subprocess sandbox. The Quest-console Settings button is disabled by default and only exposes a loopback-only development/test configuration plane. An explicit Docker development override stores provider configuration only in a named volume and mirrors only a short-lived token to host `.secrets`; it remains default-off, bridge-gateway/token restricted, and is not a production secret manager. It can configure OpenAI or Qwen, but opening, saving, or selecting Qwen does not authorize or make a provider call; DeepSeek remains unavailable. A replacement API key must be entered directly by the user in that panel after rotation, never in documentation or repository files. Docker, Godot engine loading, live transport, Quest restoration, representative graphical layouts, and a deterministic Godot visual-regression baseline are covered; the project source is MIT licensed, while release video and binary, media, or package artifacts remain outside the authorized distribution scope. Full constraints are in [`docs/limitations.md`](docs/limitations.md).
