from pathlib import Path

from backend.app.tools import Sandbox
from backend.app.v1.models import AcceptanceCriterion, Evidence
from backend.app.v1.verifier import Verifier


def test_verifier_hashes_current_artifact_and_excludes_content(tmp_path: Path):
    sandbox = Sandbox(tmp_path)
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "README.md").write_text("# hi\n", encoding="utf-8")
    criterion = AcceptanceCriterion(
        id="doc", kind="markdown", description="doc", path="README.md"
    )
    result = Verifier(sandbox).verify(
        criterion, "ws", action_attempt="a1", event_sequence=2
    )
    assert result.passed
    assert len(result.evidence.artifact_hash or "") == 64
    assert "content" not in result.evidence.model_dump()


def test_stale_evidence_fails_after_artifact_changes(tmp_path: Path):
    sandbox = Sandbox(tmp_path)
    (tmp_path / "ws").mkdir()
    path = tmp_path / "ws" / "x.py"
    path.write_text("x = 1\n", encoding="utf-8")
    criterion = AcceptanceCriterion(
        id="py", kind="python_syntax", description="py", path="x.py"
    )
    verifier = Verifier(sandbox)
    first = verifier.verify(criterion, "ws")
    path.write_text("x = [\n", encoding="utf-8")
    stale = verifier.verify(criterion, "ws", evidence=first.evidence)
    assert not stale.passed
    assert stale.reason == "stale or tampered evidence"


def test_all_required_criteria_must_pass(tmp_path: Path):
    sandbox = Sandbox(tmp_path)
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "a.json").write_text('{"ok": true}', encoding="utf-8")
    verifier = Verifier(sandbox)
    criteria = [
        AcceptanceCriterion(
            id="json",
            kind="json_schema",
            description="json",
            path="a.json",
            required_keys=["ok"],
        ),
        AcceptanceCriterion(
            id="missing",
            kind="file_exists_nonempty",
            description="missing",
            path="none.txt",
        ),
    ]
    passed, results = verifier.verify_all(criteria, "ws")
    assert not passed and [r.passed for r in results] == [True, False]


def test_tampered_evidence_is_rejected(tmp_path: Path):
    sandbox = Sandbox(tmp_path)
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "a.md").write_text("# ok", encoding="utf-8")
    criterion = AcceptanceCriterion(
        id="doc", kind="markdown", description="doc", path="a.md"
    )
    result = Verifier(sandbox).verify(criterion, "ws")
    forged_data = result.evidence.model_dump()
    forged_data.update({"artifact_hash": "0" * 64, "passed": True})
    forged = Evidence(**forged_data)
    checked = Verifier(sandbox).verify(criterion, "ws", evidence=forged)
    assert not checked.passed
