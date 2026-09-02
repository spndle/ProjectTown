# Phase 3E v4 participant-instance Study handoff

This is a start prompt only. Do not create a Study, Round, Summary, User RC,
authorization, Apply, Restore, or release unless the User gives a new, exact
authorization with unique external roots.

## Protocol boundary

`projecttown-phase3e-rc-v4` uses
`participant_instance_plus_engineering_acceptance_plus_user_v1`. The only human
evaluation gate is the named Participant performing both instance tests:
`R1-CONTROLLED-APPLY` and `R2-REPORT-EXPORT`. There is no Independent Human
Reviewer in v4. Sol's `EngineeringAcceptanceV4` is a separately hashed,
non-human technical record and cannot stand in for participant evidence. User RC
is a later, explicit decision and cannot be inferred from either record.

Canonical lineage:

- manifest: `examples/v3-phase-3/projecttown-phase3e-manifest-v4.json`
- manifest SHA-256: `24ce4fef9e069a92026790ca9fa859fca9480b3beb696d90caefb36a66521aa4`
- Study/Round/Summary/User RC: additive v4 schemas and `/v4` hash domains
- engineering acceptance: `v3-phase3e-engineering-acceptance-v4`, hash domain
  `projecttown/v3/phase3e-engineering-acceptance/v4`

The v3 Study protocol is frozen policy-hold history. Preserve its canonical
bytes and allow only read/check/status; do not create new v3 Round, Summary, or
User RC records.

## Required participant evidence

For each round, collect an explicitly re-attested participant evidence file and
create a `ParticipantEvidenceV4` record with: identity label, retained/not-kept
disposition, elapsed seconds, actions, notes, canonical timestamp, evidence
path, `control_rating` 1–5, `citation_usable`, and `structural_rewrite`.
Do not infer or generate any of these values. Both round records must bind the
same participant and the Study's participant_count is one.

R1 may reuse the frozen v3 Apply→Restore chain only after an explicit v4
predecessor binding records the v3 Study hash and each exact path/hash. Those
files remain immutable historical evidence; they do not authorize a v4 Apply or
Restore. The prior participant chat answer is not v4 evidence until re-attested
with the new rating and boolean fields.

R2 remains the fixed local Report Export instance task and must bind the fixed
source-set/manifest, Result, preview, citations, and PDF evidence.

## Engineering and User gates

For each round, Sol records separate `EngineeringAcceptanceV4`: PASS/FAIL,
verifier label, checks, notes/actions, canonical timestamp, evidence path,
citation traceability/usability, and blocking-defect result. Summary may become
`criteria_met_awaiting_user_rc_acceptance` only when both rounds have verified
bindings, retained participant outcomes, rating at least threshold, usable
citations, no structural rewrite, engineering PASS, and no blocker.

Only then may the User explicitly record ACCEPT/RETAIN/REVISE/DISCARD/STOP.
Neither engineering verification nor participant retention authorizes Apply,
Publish, Release, or VERSION changes.
