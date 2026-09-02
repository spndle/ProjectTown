# ProjectTown v10 T002 Human Study start prompt

This is a start-prompt-only handoff. It does not create, reserve, or authorize
a Study, Trial, Result, PDF, or Summary.

## Preconditions to verify before asking a participant

- Confirm that the offline v10 engineering QA report exists and binds the
  measured `expected_page_count=3`, candidate SHA-256, Result SHA-256, manifest
  SHA-256, generator `deterministic-grounded-plan-v9`, exporter
  `v3-material-pdf-export-v9`, and renderer `projecttown-reportlab-pdf-v9`.
- Use the engineering reference only for verification: PDF SHA-256
  `dc72b42fdeee8102a04de2fa9f0b0c8c6a4f24a264a26748e8a96fb7aeb61e12`
  and manifest SHA-256
  `6b95a1731fc88824e140c746966c8df5a33dd93ad2a05ded60423277d01390bf`.
  These values are not participant evidence and do not create a Study.
- Confirm that the selected v10 PDF is a fresh, create-only Result/PDF under a
  new work root. Do not use a v8 or v9 historical PDF as a v10 participant
  artifact.
- Confirm that the intended Study ID, Study root, and sibling work root do not
  exist. Do not write into any historical root.
- Keep provider, embedding, MCP, network/egress, and paid API calls at zero.

## Study setup

Create a new unique v10 human Study only after explicit user authorization.
Use `candidate_profile=projecttown-human-pdf-v10` and the committed v10
manifest. Evaluate fixed task `T002`:

> 制定一次本地交付复验计划，区分可重跑验证、不可重跑历史证据和用户持有的发布事项

Use a 20-minute manual baseline. Generate a new canonical Result and PDF for
the new sibling work root before the participant opens it. Do not reuse an
engineering candidate as participant evidence and do not copy any v9 rating,
disposition, or notes.

## Participant record contract

The participant must personally provide all of the following for a completed
PDF Trial:

- `participant_notes` (including explicit `暂无` when applicable);
- `participant_timestamp` as an explicit timezone-bearing RFC 3339 value;
- `participant_evidence_path`, the canonical absolute path of the exact PDF
  opened and rated;
- actions, active elapsed time, manual baseline, control rating, structural
  rewrite, citation usability, disposition, and improvement reason.

Use `trial-create` with the v10 profile and all participant fields. The CLI
must bind the evidence path to the same canonical PDF supplied to
`--pdf-export`, verify its bytes/presentation binding, and reject missing or
mismatched evidence. Preserve create-only behavior; never overwrite a Trial.

## Interpretation boundary

Engineering tests, PDF rendering, and proxy visual QA demonstrate only the
engineering contract. An Independent Study disposition and a User disposition
remain separate. A v10 Summary can be no stronger than
`criteria_met_unanchored_awaiting_user_acceptance`; no step here authorizes
Phase 3, Apply, Publish, Retain, or Discard.
