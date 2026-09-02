# ProjectTown T002 runbook v7 human Study handoff

Use this handoff only after the User supplies a new, unique Study ID, Study root,
and sibling work root. Do not create a Study automatically.

Use candidate profile `projecttown-human-pdf-v7` and
`projecttown-trial-manifest-v7.json`. Recreate the fixed T002 task from its
unchanged manifest sources and constraints. Generate the Result with
`deterministic-grounded-plan-v6` and the PDF with
`v3-material-pdf-export-v6` / `projecttown-reportlab-pdf-v6` in the new work root.

Before the run, bind all ten Run Binding constraints through existing
`--constraint key=value`: run_binding_candidate_path,
run_binding_preview_path, run_binding_manifest_path,
run_binding_historical_evidence_root, run_binding_fresh_root,
run_binding_fresh_evidence_root, run_binding_test_command,
run_binding_expected_page_count,
run_binding_approved_hash_provenance_tuple_source, and
run_binding_study_evidence_path.
Do not use placeholders for a human run.

Record participant notes, participant-confirmed RFC3339 timestamp, and the actual
participant evidence PDF path in TrialV3. The participant alone supplies rating,
disposition, actions, elapsed time, manual baseline, and any retain/discard choice.
Summary can remain only `criteria_met_unanchored_awaiting_user_acceptance`; it does
not authorize Accept, Apply, Publish, Retain, or Discard.
