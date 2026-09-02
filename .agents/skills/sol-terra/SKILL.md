---
name: sol-terra
description: "Run the ProjectTown Sol-Terra development workflow when the user explicitly invokes $sol-terra or asks Sol to plan, delegate, implement, test, review, or accept a bounded repository change through specialized Terra subagents. Use for project work that benefits from controlled exploration, implementation, verification, or high-risk decision routing; keep simple answers and one-step read-only lookups in the primary thread when delegation adds no value."
---

# Sol Terra

Keep Sol as the control plane and final acceptor. Delegate bounded execution to
the configured Terra roles. Follow the nearest AGENTS.md in addition to this
workflow; do not restate unrelated project conventions here.

## Establish the boundary

1. Restate the outcome, non-goals, constraints, and required evidence.
2. Inspect relevant current state before proposing changes.
3. Ask the user only when a missing choice materially changes the result or
   requires new authority; otherwise make a conservative explicit assumption.
4. Keep requirements, decisions, risks, and final acceptance in the Sol thread.

## Choose the route

Use the narrowest configured role that fits the next bounded unit:

| Need | Route | Write authority |
| --- | --- | --- |
| Locate entries, trace symbols, map a small call chain, collect evidence | `terra_explorer` | None |
| Apply a small, explicit, reversible patch | `terra_implementer` | Contracted workspace paths only |
| Run tests/builds, reproduce a failure, inspect logs | `terra_tester` | Necessary command outputs or an explicit temp path only |
| Decide a qualifying high-impact issue | `sol_escalation` | None |

Do not delegate a trivial one-step read, a decision Sol must own directly, or a
unit without a safe boundary. Terra is the configured execution family; do not
route ProjectTown work to Luna unless the user explicitly changes this policy.

Delegate to `terra_explorer` before implementation when the owning file,
execution path, callers, or test surface is uncertain. Skip exploration when
the evidence is already concrete and current. Treat a filename match as a lead,
not a verified call chain.

## Split and contract work

Create the smallest units with one objective, disjoint or serialized write
boundaries, deterministic completion criteria, a validation method, and a
rollback path. Parallelize independent read-heavy work; serialize overlapping
writes. Without independent worktrees, never allow concurrent edits to the same
area.

Every Terra delegation must include:

Task goal:
  One bounded outcome.

Allowed scope:
  Exact read paths and exact write paths, or none.

Forbidden scope:
  Excluded paths, behavior, dependencies, external systems, and decisions.

Known context:
  Verified call chain, constraints, prior decisions, and assumptions.

Completion criteria:
  Observable conditions that must all hold.

Validation method:
  Exact commands or checks and expected results.

Rollback or cleanup:
  Exact restoration steps; use N/A only for genuinely read-only work.

Return format:
  Investigation conclusion
  Files modified
  Key code changes
  Commands executed
  Test results
  Unresolved questions
  Risks and recommendations

Require the role to stop with `CONTRACT_INCOMPLETE`, `OUT_OF_SCOPE`, or
`BLOCKED` when the contract is missing, exceeded, or unsafe. Terra must not make
a major architecture decision, expand requirements, edit unrelated files, add a
production dependency, or weaken sandbox, approval, or network controls.

## Review and accept as Sol

For every returned unit:

1. Compare actual changed paths with allowed scope.
2. Inspect the diff or equivalent before/after evidence.
3. Check implementation against acceptance criteria and existing behavior.
4. Confirm commands finished with exit status and meaningful pass/fail counts.
5. Re-run a focused check when evidence is incomplete or risk warrants it.
6. Reject self-reported completion without file, symbol, command, test, or
   artifact evidence.

Accept only when both scope and validation evidence pass. Otherwise issue one
precise repair contract, request additional verification, or use the recorded
rollback.

## Gate Sol High strictly

Invoke `sol_escalation` only when at least one condition is explicit:

- the decision crosses multiple subsystems;
- it changes a protocol, migrates data, or performs an irreversible operation;
- material security, permission, concurrency, race, or consistency risk exists;
- alternatives have substantial long-term cost or architectural tradeoffs;
- Sol Medium has made two documented attempts without a credible conclusion; or
- a wrong decision could cause production failure, data loss, or broad rework.

Do not invoke it for routine features, formatting, normal tests, simple bugs,
code search, or documentation cleanup. Keep it read-only. If the user disables
High, expose any unresolved risk and request direction only when necessary.

## Finish with Sol acceptance

Report the final outcome, accepted changes, validation evidence, remaining gaps,
observability limits, risks, and rollback. Distinguish:

- verified effective: required behavior ran and passed;
- partially effective: configuration or some routes passed but a material runtime
  property remains unobservable or failed; and
- configured but unverified: files validate statically but no runtime route ran.

Never call the workflow complete merely because configuration files exist.
