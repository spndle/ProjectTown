from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app import phase2_closeout as closeout


def _round(task_id: str, profile: str, trial_schema: str) -> closeout.RoundReceipt:
    return closeout.RoundReceipt(
        task_id=task_id,
        artifact_kind="plan",
        study_id=f"study-{task_id}",
        study_schema_version="v3-usability-study-v2",
        study_hash="a" * 64,
        study_file_hash="b" * 64,
        trial_schema_version=trial_schema,
        trial_record_hash="c" * 64,
        trial_file_hash="d" * 64,
        candidate_profile=profile,
        manifest_hash="e" * 64,
        result_bytes_hash="f" * 64,
        result_file_hash="1" * 64,
        result_session_hash="5" * 64,
        artifact_hash="6" * 64,
        preview_hash="7" * 64,
        citations_complete=True,
        presentation=closeout.PresentationReceipt(
            pdf_bytes_hash="2" * 64,
            pdf_file_hash="3" * 64,
            pdf_export_version="v3-material-pdf-export-v2",
            pdf_renderer_version="projecttown-reportlab-pdf-v2",
            pdf_source_artifact_hash="4" * 64,
        ),
        disposition="retained",
        structural_rewrite=False,
        citation_usable=True,
        call_observation="observed_zero",
        action_count=1,
        control_rating=4,
        improvement_reason="none",
        elapsed_seconds=120 if task_id == "T001" else 180,
        manual_baseline_seconds=1200,
        participant_evidence_presence=closeout.ParticipantEvidencePresence(
            notes=trial_schema.endswith("v3"),
            timestamp=trial_schema.endswith("v3"),
            evidence_path=trial_schema.endswith("v3"),
        ),
    )


def _receipt() -> closeout.Phase2Closeout:
    raw = {
        "schema_version": closeout.SCHEMA_VERSION,
        "policy_revision": {
            "original_task_threshold": 10,
            "revised_round_threshold": 2,
            "reason": "participant_burden",
            "scope": "longitudinal_cross_profile",
            "acceptance": "scope_limited_accepted_by_user",
            "user_decision_timestamp": None,
            "record_created_on": "2026-08-30",
        },
        "rounds": (
            _round(
                "T001", "projecttown-human-pdf-v3", "v3-usability-trial-v2"
            ).model_dump(mode="json"),
            _round(
                "T002", "projecttown-human-pdf-v9", "v3-usability-trial-v3"
            ).model_dump(mode="json"),
        ),
        "legacy_limitation": "not_a_single_profile_summary; does_not_validate_v10_or_report_or_readme; does_not_authorize_apply",
    }
    candidate = closeout.Phase2Closeout.model_validate(
        {**raw, "receipt_hash": "0" * 64}
    )
    return candidate.model_copy(
        update={"receipt_hash": closeout._hash(closeout._payload(candidate))}
    )


def test_rounds_are_strictly_ordered_and_domain_separated() -> None:
    receipt = _receipt()
    assert (
        closeout.parse_closeout_bytes(closeout.serialize_closeout(receipt)) == receipt
    )
    assert closeout.HASH_DOMAIN not in closeout.serialize_closeout(receipt).decode()
    with pytest.raises(ValidationError):
        closeout.Phase2Closeout.model_validate(
            {
                **receipt.model_dump(mode="json"),
                "rounds": list(reversed(receipt.rounds)),
            }
        )


def test_tamper_and_duplicate_key_are_rejected() -> None:
    data = closeout.serialize_closeout(_receipt())
    with pytest.raises(closeout.Phase2CloseoutError, match="rejected"):
        closeout.parse_closeout_bytes(
            data.replace(b'"control_rating":4', b'"control_rating":3')
        )
    with pytest.raises(closeout.Phase2CloseoutError, match="rejected"):
        closeout.parse_closeout_bytes(
            data.replace(b'"result_session_hash":"555', b'"result_session_hash":"955')
        )
    with pytest.raises(closeout.Phase2CloseoutError, match="rejected"):
        closeout.parse_closeout_bytes(
            data.replace(
                b'"schema_version"', b'"schema_version":"x","schema_version"', 1
            )
        )


def test_policy_is_fixed_and_does_not_claim_apply() -> None:
    receipt = _receipt()
    assert receipt.policy_revision.user_decision_timestamp is None
    assert "does_not_authorize_apply" in receipt.legacy_limitation
    with pytest.raises(ValidationError):
        closeout.PolicyRevision(
            original_task_threshold=10,
            revised_round_threshold=2,
            reason="participant_burden",
            scope="longitudinal_cross_profile",
            acceptance="scope_limited_accepted_by_user",
            user_decision_timestamp=None,
            record_created_on="not-a-date",
        )
    with pytest.raises(ValidationError):
        closeout.PolicyRevision(
            original_task_threshold=10,
            revised_round_threshold=2,
            reason="participant_burden",
            scope="longitudinal_cross_profile",
            acceptance="scope_limited_accepted_by_user",
            user_decision_timestamp=None,
            record_created_on="2026-02-30",
        )


def test_publish_is_create_only(tmp_path: Path) -> None:
    receipt = _receipt()
    closeout.publish_closeout(tmp_path, receipt)
    assert closeout.load_closeout(tmp_path) == receipt
    with pytest.raises(closeout.Phase2CloseoutError):
        closeout.publish_closeout(tmp_path, receipt)


def test_real_create_publish_load_and_verify_with_bound_builder(
    tmp_path: Path, monkeypatch
) -> None:
    rounds = (
        _round("T001", "projecttown-human-pdf-v3", "v3-usability-trial-v2"),
        _round("T002", "projecttown-human-pdf-v9", "v3-usability-trial-v3"),
    )
    monkeypatch.setattr(
        closeout,
        "_round_receipt",
        lambda spec, *_roots: rounds[0 if spec["task_id"] == "T001" else 1],
    )
    receipt = closeout.create_closeout(
        tmp_path, tmp_path, tmp_path, tmp_path, tmp_path, record_created_on="2026-08-30"
    )
    closeout.publish_closeout(tmp_path, receipt)
    loaded = closeout.load_closeout(tmp_path)
    assert closeout.verify_closeout(loaded, tmp_path, tmp_path, tmp_path, tmp_path)
