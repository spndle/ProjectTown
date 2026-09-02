# Phase 2 two-round closeout receipt

`v3-phase2-closeout-v1` is an additive, create-only receipt for the explicitly
approved two-round longitudinal cross-profile decision.  It is not a Summary,
does not relax the existing ten-task single-study Summary contract, and does
not authorize target-writing Apply or Publish.

The receipt binds frozen T001 v3 and T002 v9 study/trial/result/PDF evidence by
hash, including result-session, artifact, preview and citation-completeness
bindings plus the exact elapsed/manual-baseline measurements. It verifies the
current manifests and presentation bindings, and records
that the user reduced the threshold from ten tasks to two rounds because of
participant burden.  No participant notes or evidence paths are included in
CLI status output.

Create and check require explicit absolute roots for both frozen studies and
their sibling work roots.  The output is always the direct child
`phase2-closeout.json` of a new receipt root and cannot overwrite an existing
receipt.
