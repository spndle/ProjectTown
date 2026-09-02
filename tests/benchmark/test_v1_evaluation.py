import hashlib
import json
from pathlib import Path

from backend.app.v1.evaluation import (
    ABLATIONS,
    CONFIGS,
    agent_view,
    configuration_fingerprint,
    expected_run_count,
    load_catalog,
    run,
    score_metrics,
)

ROOT = Path(__file__).parents[2]


def test_catalog_shape():
    q = load_catalog(ROOT / "benchmark/quests/catalog.json")
    assert len(q) == 30
    for fam in {x["family"] for x in q}:
        xs = [x for x in q if x["family"] == fam]
        assert len(xs) == 10
        assert [x["length"] for x in xs].count("short") == 4
        assert [x["length"] for x in xs].count("medium") == 3
        assert [x["length"] for x in xs].count("long") == 3
        assert all(
            x["required_actions"] == {"short": 5, "medium": 10, "long": 20}[x["length"]]
            for x in xs
        )
        assert all(len(x["action_plan"]) == x["required_actions"] for x in xs)
        assert all(x["goal"] and x["expected_artifacts"] for x in xs)


def test_gold_boundary_and_fingerprint():
    q = load_catalog(ROOT / "benchmark/quests/catalog.json")[0]
    assert "gold_constraints" not in agent_view(q)
    assert "external_verifier" not in agent_view(q)
    assert configuration_fingerprint("B3") == configuration_fingerprint("B3")
    assert len(CONFIGS) == 5 and len(ABLATIONS) >= 7
    assert expected_run_count("formal") == 4320


def test_metrics_failure_boundaries():
    x = score_metrics(success=True, progress=9, false_completion=True)
    assert x["success"] == 0 and x["progress"] == 1.0
    assert score_metrics(success=False, progress=-1)["progress"] == 0.0


def test_cli_artifacts(tmp_path):
    rows = run("smoke", tmp_path)
    assert len(rows) == expected_run_count("smoke") == 150
    assert len({row["workspace_id"] for row in rows}) == len(rows)
    assert {p.suffix for p in tmp_path.iterdir()} >= {".csv", ".json", ".svg", ".md"}
    assert json.loads((tmp_path / "results.json").read_text())
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "[Raw results (CSV)](results.csv)" in report
    assert "[Raw results (JSON)](results.json)" in report
    assert "[Success-rate chart](success.svg)" in report
    assert "[Reproducibility manifest](manifest.json)" in report
    assert manifest["profile"] == "smoke"
    assert manifest["seed"] == 1729
    assert manifest["row_count"] == len(rows)
    assert manifest["command"] == (
        "python -m backend.app.v1.evaluation --output "
        "<output> --profile smoke --seed 1729"
    )
    for name, expected_hash in manifest["sha256"].items():
        contents = (tmp_path / name).read_bytes()
        assert b"\r\n" not in contents
        assert hashlib.sha256(contents).hexdigest() == expected_hash


def test_formal_seed_1729_regeneration_matches_committed_artifacts(tmp_path):
    regenerated = tmp_path / "formal-v1.0"
    run("formal", regenerated, seed=1729)
    committed = ROOT / "benchmark" / "results" / "formal-v1.0"
    manifest = json.loads((committed / "manifest.json").read_text(encoding="utf-8"))
    regenerated_manifest = json.loads(
        (regenerated / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["profile"] == "formal"
    assert manifest["seed"] == 1729
    assert manifest["row_count"] == expected_run_count("formal")
    assert regenerated_manifest == manifest
    for name, expected_hash in manifest["sha256"].items():
        regenerated_bytes = (regenerated / name).read_bytes()
        assert regenerated_bytes == (committed / name).read_bytes()
        assert hashlib.sha256(regenerated_bytes).hexdigest() == expected_hash
