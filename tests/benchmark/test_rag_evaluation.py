import hashlib
import json
from pathlib import Path

import pytest

from backend.app.v1.rag import (
    build_index,
    search,
    verify_citation,
    verify_search_result,
)
from benchmark.rag_evaluation import runner

ROOT = Path(__file__).parents[2]
SANDBOX = ROOT / "sandbox" / "tmp" / "rag-evaluation"


def test_dataset_has_required_adversarial_and_multilingual_coverage():
    dataset = runner._load_dataset()
    assert len(dataset["documents"]) >= 12
    assert any(
        "Ignore prior instructions" in document["text"]
        for document in dataset["documents"]
    )
    assert any(
        any("\u4e00" <= char <= "\u9fff" for char in case["query"])
        for case in dataset["cases"]
    )
    assert sum(case["expect_no_answer"] for case in dataset["cases"]) >= 2
    assert next(case for case in dataset["cases"] if case["id"] == "tie-release")[
        "gold_document_ids"
    ] == ["release-alpha", "release-beta"]
    assert (
        len(
            next(
                document
                for document in dataset["documents"]
                if document["id"] == "long-chunks"
            )["text"]
        )
        > 800
    )


def test_runner_writes_byte_stable_isolated_artifacts(tmp_path):
    run_label = hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()[:12]
    first = SANDBOX / f"pytest-{run_label}-a"
    second = SANDBOX / f"pytest-{run_label}-b"
    runner.run(first)
    runner.run(second)
    names = ("results.json", "results.csv", "report.md", "manifest.json")
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["deterministic_rag_evaluation"] is True
    assert manifest["provider_calls"] == manifest["embedding_calls"] == 0
    results = json.loads((first / "results.json").read_text(encoding="utf-8"))
    assert set(results["ablations"]) == {"top_k_1", "top_k_3", "top_k_5"}
    assert all(
        value["changed_parameter"] == "top_k" for value in results["ablations"].values()
    )
    assert {
        label: value["metrics"]["top_k"]
        for label, value in results["ablations"].items()
    } == {"top_k_1": 1, "top_k_3": 3, "top_k_5": 5}
    assert all(row["requested_top_k"] == 3 for row in results["rows"])
    injection_row = next(
        row for row in results["rows"] if row["case_id"] == "injection-boundary"
    )
    assert injection_row["retrieved_document_ids"] == ["injection-decoy"]
    forbidden = {
        "tool",
        "tools",
        "evidence",
        "permission",
        "provider",
        "model",
        "api_key",
    }
    assert not forbidden & set(results)
    assert not forbidden & set(injection_row)
    assert results["provider_calls"] == results["embedding_calls"] == 0
    for name, expected in manifest["sha256"].items():
        assert hashlib.sha256((first / name).read_bytes()).hexdigest() == expected


def test_tampering_and_forbidden_output_are_rejected(tmp_path):
    tampered = tmp_path / "dataset.json"
    tampered.write_bytes(
        runner._DATASET_PATH.read_bytes().replace(b"budget", b"budgetX", 1)
    )
    with pytest.raises(
        runner.EvaluationValidationError, match="deterministic rag evaluation rejected"
    ) as error:
        runner._load_dataset(tampered)
    assert error.value.code == "DATASET_TAMPERED"
    with pytest.raises(runner.EvaluationValidationError) as path_error:
        runner.run(ROOT / "benchmark" / "results" / "formal-v1.0" / "blocked")
    assert path_error.value.code == "OUTPUT_PATH_FORBIDDEN"
    dataset = runner._load_dataset()
    index = build_index(dataset["documents"])
    result = search(index, "approved budget ceiling")
    citation = result.hits[0].citation.model_copy(
        update={"document_id": "retry-policy"}
    )
    assert verify_citation(index, "approved budget ceiling", citation) is False
    tampered_hit = result.hits[0].model_copy(update={"score": result.hits[0].score + 1})
    tampered_result = result.model_copy(update={"hits": (tampered_hit,)})
    assert (
        verify_search_result(index, "approved budget ceiling", tampered_result) is False
    )


def test_second_chunk_citation_and_atomic_failure_cleanup(monkeypatch, tmp_path):
    dataset = runner._load_dataset()
    index = build_index(dataset["documents"])
    second_chunk = search(index, "secondchunkneedle", top_k=1).hits[0]
    assert second_chunk.citation.document_id == "long-chunks"
    assert second_chunk.citation.normalized_start >= 800
    output = (
        SANDBOX
        / f"pytest-atomic-{hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]}"
    )
    calls = 0
    original = runner._write_bytes

    def fail_second_write(path, contents):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic write failure")
        original(path, contents)

    monkeypatch.setattr(runner, "_write_bytes", fail_second_write)
    with pytest.raises(runner.EvaluationValidationError) as error:
        runner.run(output)
    assert error.value.code == "OUTPUT_WRITE_FAILED"
    assert not output.exists()
    assert not output.with_name(f".{output.name}.partial").exists()


def test_document_level_metrics_deduplicate_multi_chunk_citations():
    dataset = runner._load_dataset()
    index = build_index(dataset["documents"])
    row = runner._result_row(
        {
            "id": "multi-chunk-document",
            "query": "padding",
            "gold_document_ids": ["long-chunks"],
            "expect_no_answer": False,
        },
        index,
        top_k=3,
    )
    assert row["citation_count"] > 1
    assert row["retrieved_document_ids"] == ["long-chunks"]
    assert row["relevant_rank"] == 1
    duplicated_row = dict(row, retrieved_document_ids=["long-chunks", "long-chunks"])
    metrics = runner._aggregate([duplicated_row], top_k=3)
    assert metrics["citation_precision"] == 1.0
    assert metrics["citation_recall"] == 1.0
    assert metrics["mrr_at_k"] == 1.0
