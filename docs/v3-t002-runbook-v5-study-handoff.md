# T002 runbook v5 Study handoff

Only after a new User authorization, create a new unique Study root and sibling work root.
Use `projecttown-human-pdf-v5` with `projecttown-trial-manifest-v5.json`, generate a
new generator-v4 Result and exporter-v4 PDF, and never reuse frozen v4 evidence.

The participant evaluates the fixed T002 task only. Record disposition, structural rewrite,
citation usability, control rating, improvement reason, actions, active elapsed time, notes,
timestamp and evidence path. The Study cannot decide Accept, Retain, Discard, Apply or Publish;
these remain an explicit User Gate. A Summary remains awaiting User acceptance.

For each completed v5 Trial, pass `--participant-notes`,
`--participant-timestamp` (strict timezone-bearing RFC 3339 form), and
`--participant-evidence-path` (the exact external PDF also supplied as `--pdf-export`).
Do not create a Trial from inferred feedback or a copied PDF. A workflow_failed or
abandoned v5 Trial still needs explicit notes and timestamp, but has no participant PDF
evidence path or presentation binding.
