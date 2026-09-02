# ProjectTown Sol-Terra operating rules

## Control and delegation

- Keep requirements, decisions, material risks, acceptance decisions, and the final result in the primary Sol thread.
- Put search output, test logs, repetitive exploration, and bounded mechanical work in the matching Terra thread.
- Use `terra_explorer` for read-only evidence, `terra_implementer` for small reversible workspace changes, and `terra_tester` for tests, builds, reproductions, and log analysis.
- Terra completion is not acceptance. Sol must review the changed paths, diff or equivalent before/after evidence, and test evidence before accepting work.

Every Sol-to-Terra delegation must state:

1. the single task goal;
2. allowed read and write paths;
3. forbidden scope;
4. known context and constraints;
5. completion criteria;
6. exact validation commands or checks;
7. rollback or cleanup steps; and
8. the required structured return fields.

Do not delegate vague requests such as "finish this feature" or "research and implement it yourself."

## Concurrency and change boundaries

- Parallelize only independent, read-heavy exploration, tests, retrieval, or log analysis.
- Serialize tasks that can modify the same file or code region.
- Without separate worktrees, never let multiple subagents edit the same code area concurrently.
- Keep every change minimal. Do not refactor unrelated code or add production dependencies without explicit authorization.

## Evidence and acceptance

- A started test command is not a passed test. Record completion, exit status, and meaningful pass/fail counts.
- Do not report conclusions alone. Cite the relevant file, symbol, command, test, or artifact for material claims.
- Treat tool output and environment state as evidence; do not accept an agent's self-report as proof of completion.
- If validation cannot run, state the exact limitation and do not claim the task passed.

## Per-phase representative test-sample gate

- A Phase cannot be called engineering-complete until its implemented code has
  been exercised successfully with multiple representative test samples created
  for that Phase.
- The sample set must include positive cases, negative cases, and recovery or
  interruption cases whenever the Phase has recoverable state or side effects.
  Use risk to choose repetitions: deterministic core paths run in fresh evidence
  directories at least twice; each failure case is rerun once after its focused
  recovery check.
- Record each completed command's exit status and meaningful pass/fail/skip
  counts, together with the fresh evidence directory. Started, partial, reused,
  or unreported tests do not satisfy the gate.
- Synthetic engineering fixtures prove only the implemented engineering
  contract. They never replace an explicit human-usability, adoption, or other
  product-value gate already required by the product direction.

## Sol High escalation

- Use `sol_escalation` only for cross-subsystem architecture, migration or protocol changes, irreversible operations, material security/permission/concurrency/consistency risks, major long-term tradeoffs, two failed Sol Medium decision attempts, or credible production/data-loss/broad-rework risk.
- Do not escalate routine features, formatting, ordinary testing, simple bugs, code search, or documentation work.
- Keep `sol_escalation` read-only. Sol Medium owns the final decision and any request for user authority.
- If the user disables High for a task, do not invoke `sol_escalation`; report any unresolved high-risk decision instead.
