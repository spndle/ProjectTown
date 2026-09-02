# ProjectTown T002 runbook v8 human-study handoff

This is a start prompt only.  It does not create a Study, Trial, Result, PDF or
Summary.  The operator must first choose a new unique Study root and sibling
work root, then obtain explicit participant confirmation before any create-only
write.

Use `projecttown-human-pdf-v8`, manifest
`examples/v3-phase-2/projecttown-trial-manifest-v8.json`, generator
`deterministic-grounded-plan-v7`, exporter `v3-material-pdf-export-v7`, and
renderer `projecttown-reportlab-pdf-v7`.  The v8 procedure is new; it verifies
the frozen v7 candidate whose presentation lineage is generator/exporter/
renderer v6.  Do not replace or regenerate that historical candidate in place.

The v8 PDF is intentionally four pages: summary/binding/inventory; flow,
three boundaries and Independent Study contract; the two-part Verification Matrix;
then states, PASS/FAIL, User Gate, citations and offline boundary. Bind
`expected_page_count` to `4`; four pages are expected rather than a fallback.

Before running, bind `binding_id`, `working_directory`, `candidate_path`,
`preview_path` (the text/JSON record, not a visual artifact), `manifest_path`,
`prior_study_evidence_path`, `historical_evidence_root`, `fresh_root`,
`fresh_evidence_root`, `test_command`, `fresh_result_output_path`,
`fresh_result_evidence_label`, `expected_page_count`,
`approved_hash_provenance_tuple_source`, and `planned_study_evidence_output`.
Set `run_binding_preflight_result` to `pending`, `passed`, or `blocked`.
Nothing may leave `Initial State: BLOCK` unless M00 is `PREFLIGHT PASS`.

The PDF and preview display scan-safe `PATH_REF[...]` and `COMMAND_REF[...]`
values only. They do not expose the bound absolute paths or exact command. The
canonical Input, Output, and Historical values (including the exact command)
remain in the create-only Result JSON under `draft.constraints`; M00 and M01
verify and execute those canonical values. A planned Study output is displayed
only as a reference and this handoff does not authorize creating it.

The Verification Matrix retains all ten fields. Its compact labels are defined in
the PDF scan note: `Mandatory` means Mandatory Verification, `Conditional` means
Conditional Release Action, and `RP` means Reviewer-defined verification policy.

The participant alone supplies notes, timestamp, and the exact PDF evidence
path.  The reviewer records the Independent Study disposition only; the User
alone decides Accept, Retain, Discard, Apply, or Publish.  M07 pending means
WAITING USER.  M08 without authorization means NOT AUTHORIZED / DO NOT EXECUTE,
not a verification failure.
