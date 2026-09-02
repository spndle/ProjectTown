# Phase 4B: Local Workspace Task authoring slice

Status: engineering implementation; not human-usability acceptance and not a
release authorization.

The authoritative 4A-4E roadmap and later gates are defined in
[`v3-phase-4.md`](v3-phase-4.md).

Phase 4B extends the default-off Phase 4A local loopback workbench with a
bounded offline candidate-authoring flow:

```text
open workspace -> select catalog entries -> draft contract -> explicit confirm
-> generate candidate -> preview -> create-only Markdown/PDF copy -> download
```

The browser receives display-safe relative source names and opaque `source_id`
values only. It does not accept local filesystem locations, command arguments,
generator choices, or authorization actions. The fixed deterministic material
generator and exporter are selected by the server-side authoring contract.

## Operator setup

An operator initializes an already-existing, canonical external work root and
disjoint material root once. The command creates only the fixed record
directories (`catalogs`, `requests`, `intents`, `receipts`, `drafts`,
`results`, `exports`, the Phase 4A-only `bindings`, and the Phase 4B-only
`authoring-bindings`) and does not echo root values. Keeping the two binding
schemas in separate directories preserves the frozen Phase 4A v1 parser:

```powershell
python scripts/run_v3_local_workspace_task.py authoring-init `
  --ui-work-root <absolute-existing-work-root> `
  --material-root <absolute-existing-material-root>
python scripts/run_v3_local_workspace_task.py authoring-check `
  --ui-work-root <absolute-existing-work-root> `
  --material-root <absolute-existing-material-root>
```

Both roots must be safe, canonical, existing directories and must be disjoint.
`authoring-init` is idempotent for already-safe fixed directories. `authoring-check`
only verifies the roots, directories and readable material catalog; it never
deletes, overwrites or repairs records.

## Browser contract

With `enable_local_workspace_task=true` the Phase 4A verified-task view remains
available. Authoring requires the additional default-off
`enable_local_workspace_task_create=true` setting and pre-initialized roots.
When it is disabled, `/workspace` remains a read-only view and communicates that
candidate creation is unavailable.

The authoring page requires at least one catalog entry, an explicit task and one
of `plan`, `report`, or `readme`. A README suggestion must name a selected
Markdown catalog entry. Optional constraints are restricted key/value values.
Each mutation has an independent browser-generated Web Crypto idempotency key;
a failed request retries with the same key, while draft, generation and each
export use different keys. Generation additionally requires the exact server
provided `CONFIRM <contract-hash>` phrase and an explicit user confirmation.

The page displays `waiting_confirmation`, `generated`, `attention`, and related
safe states. Preview is a candidate only. Markdown and PDF creation uses the
external work root's create-only records; downloads read a verified completed
export. There is no browser control for source replacement, restoration,
retention, discard, distribution, provider use, networking, or paid calls.

## Security and recovery

Existing loopback session, exact Origin, CSRF, JSON content type, request-size,
and idempotency protections apply to every authoring mutation. Source drift,
receipt tampering, invalid catalog entries, unresolved state, duplicate output,
or interrupted publication fails closed into a safe error/state. The core owns
only its external work root; it never overwrites material sources.

Rollback is to disable `enable_local_workspace_task_create`, leaving the Phase
4A read-only view intact. Remove this additive static/API slice only through a
separate reviewed code change; do not delete external user records or exports.

## Engineering validation (2026-08-31)

The deterministic core completed in two independent external roots:

- `D:\ProjectTown-usability\projecttown-v3-phase4b-engineering-20260831-a-work`
- `D:\ProjectTown-usability\projecttown-v3-phase4b-engineering-20260831-b-work`

Their canonical Draft, Result, Markdown and PDF bytes match across roots. The
Markdown SHA-256 is `327752a90380d758b38c173cccc4ee3c11216dfe847af66ced8258c9a7d12e96`;
the one-page PDF SHA-256 is
`ad833fb021cd93be1a31f3d0c8871656f7079e195de403e9515ca8fdb1f554e5`.
Pypdf extraction and Poppler rendering passed; browser create/confirm/generate,
preview, export and authenticated Blob download were also exercised on loopback.
The final repository suite completed with 1217 passed and 17 explicitly skipped,
and coverage completed at 84.86% with the 80% gate satisfied. Provider,
embedding, external MCP, network/egress and paid-call counts were all zero.

These are synthetic engineering fixtures. They do not constitute human
usability acceptance or authorization for a real target write.

The 2026-09-01 fresh acceptance added plan/report/README generation and both
Markdown/PDF exports in two new sibling root pairs. The focused Phase 4 suite
completed with 151 passed; deterministic PDF hashes and negative/recovery
results are recorded in
[`v3-phase-0-4-acceptance-2026-09-01.md`](v3-phase-0-4-acceptance-2026-09-01.md).
