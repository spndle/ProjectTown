"""Provider-free deterministic RAG evaluation.

This harness evaluates only explicit synthetic documents.  It does not load
credentials, construct a model client, emit Evidence, or make network calls.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from backend.app.runtime import stable_hash
from backend.app.v1.rag import (
    RANKER_VERSION,
    RETRIEVER_VERSION,
    RAGValidationError,
    build_index,
    search,
    verify_citation,
    verify_search_result,
)

_SCHEMA_VERSION = 1
_TOP_K = 3
_ROOT = Path(__file__).resolve().parents[2]
_DATASET_PATH = Path(__file__).with_name("dataset.json")
_SANDBOX_ROOT = _ROOT / "sandbox" / "tmp" / "rag-evaluation"
_FORMAL_ROOT = _ROOT / "benchmark" / "results" / "formal-v1.0"
_REAL_MODEL_ROOT = _ROOT / "benchmark" / "results" / "real-model"
_EXPECTED_DATASET_SHA256 = (
    "34505cf5592f301fb638036d32863c1d300f100ca29a6bfedfaa51b1624fa3bf"
)


class EvaluationValidationError(ValueError):
    """Stable failure for invalid/tampered evaluation inputs or output paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("deterministic rag evaluation rejected")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _require_string(value: Any, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise EvaluationValidationError("INVALID_DATASET")
    return value


def _load_dataset(
    path: Path = _DATASET_PATH, *, require_canonical_hash: bool = True
) -> dict[str, Any]:
    try:
        raw_bytes = path.read_bytes()
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise EvaluationValidationError("INVALID_DATASET") from None
    if require_canonical_hash and _sha256_bytes(raw_bytes) != _EXPECTED_DATASET_SHA256:
        raise EvaluationValidationError("DATASET_TAMPERED")
    if not isinstance(parsed, Mapping) or set(parsed) != {
        "schema_version",
        "dataset_id",
        "documents",
        "cases",
    }:
        raise EvaluationValidationError("INVALID_DATASET")
    if parsed["schema_version"] != _SCHEMA_VERSION:
        raise EvaluationValidationError("INVALID_DATASET")
    _require_string(parsed["dataset_id"])
    documents = parsed["documents"]
    cases = parsed["cases"]
    if (
        not isinstance(documents, list)
        or len(documents) < 12
        or not isinstance(cases, list)
        or len(cases) < 8
    ):
        raise EvaluationValidationError("INVALID_DATASET")
    document_ids: set[str] = set()
    for document in documents:
        if not isinstance(document, Mapping) or set(document) != {
            "id",
            "revision",
            "text",
            "metadata",
        }:
            raise EvaluationValidationError("INVALID_DATASET")
        document_id = _require_string(document["id"])
        if (
            document_id in document_ids
            or not isinstance(document["revision"], int)
            or isinstance(document["revision"], bool)
        ):
            raise EvaluationValidationError("INVALID_DATASET")
        _require_string(document["text"])
        if not isinstance(document["metadata"], Mapping):
            raise EvaluationValidationError("INVALID_DATASET")
        document_ids.add(document_id)
    case_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, Mapping) or set(case) != {
            "id",
            "query",
            "gold_document_ids",
            "expect_no_answer",
        }:
            raise EvaluationValidationError("INVALID_DATASET")
        case_id = _require_string(case["id"])
        if case_id in case_ids or not isinstance(case["expect_no_answer"], bool):
            raise EvaluationValidationError("INVALID_DATASET")
        _require_string(case["query"])
        gold = case["gold_document_ids"]
        if (
            not isinstance(gold, list)
            or len(gold) != len(set(gold))
            or any(item not in document_ids for item in gold)
        ):
            raise EvaluationValidationError("INVALID_DATASET")
        if case["expect_no_answer"] != (len(gold) == 0):
            raise EvaluationValidationError("INVALID_DATASET")
        case_ids.add(case_id)
    return dict(parsed)


def _safe_output_root(output_root: Path) -> Path:
    try:
        resolved = output_root.resolve(strict=False)
        sandbox = _SANDBOX_ROOT.resolve(strict=False)
        formal = _FORMAL_ROOT.resolve(strict=False)
        real_model = _REAL_MODEL_ROOT.resolve(strict=False)
    except OSError:
        raise EvaluationValidationError("INVALID_OUTPUT_PATH") from None
    if (
        resolved == formal
        or formal in resolved.parents
        or resolved == real_model
        or real_model in resolved.parents
    ):
        raise EvaluationValidationError("OUTPUT_PATH_FORBIDDEN")
    if resolved == sandbox or sandbox not in resolved.parents:
        raise EvaluationValidationError("OUTPUT_PATH_OUTSIDE_SANDBOX")
    return resolved


def _metric_ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 6)


def _stable_unique_document_ids(values: Sequence[str]) -> list[str]:
    """Preserve first citation order while measuring documents exactly once."""

    return list(dict.fromkeys(values))


def _result_row(case: Mapping[str, Any], index: Any, *, top_k: int) -> dict[str, Any]:
    query = str(case["query"])
    result = search(index, query, top_k=top_k)
    if not verify_search_result(index, query, result):
        raise EvaluationValidationError("RETRIEVAL_BUNDLE_INVALID")
    citations = [hit.citation for hit in result.hits]
    if not all(verify_citation(index, query, citation) for citation in citations):
        raise EvaluationValidationError("CITATION_INVALID")
    retrieved_ids = _stable_unique_document_ids(
        [citation.document_id for citation in citations]
    )
    gold_ids = list(case["gold_document_ids"])
    gold_set = set(gold_ids)
    relevant_rank = next(
        (rank for rank, item in enumerate(retrieved_ids, start=1) if item in gold_set),
        None,
    )
    return {
        "case_id": case["id"],
        "query_hash": result.query_hash,
        "requested_top_k": result.requested_top_k,
        "expect_no_answer": case["expect_no_answer"],
        "gold_document_ids": gold_ids,
        "retrieved_document_ids": retrieved_ids,
        "retrieved_citation_hashes": [
            stable_hash(item.model_dump(mode="json")) for item in citations
        ],
        "retrieval_bundle_hash": result.bundle_hash,
        "citation_count": len(citations),
        "relevant_rank": relevant_rank,
    }


def _aggregate(rows: Sequence[Mapping[str, Any]], *, top_k: int) -> dict[str, Any]:
    answer_rows = [row for row in rows if not row["expect_no_answer"]]
    no_answer_rows = [row for row in rows if row["expect_no_answer"]]
    gold_total = sum(
        len(_stable_unique_document_ids(row["gold_document_ids"]))
        for row in answer_rows
    )
    retrieved_total = sum(
        len(_stable_unique_document_ids(row["retrieved_document_ids"]))
        for row in answer_rows
    )
    true_positive = sum(
        len(
            set(_stable_unique_document_ids(row["gold_document_ids"]))
            & set(_stable_unique_document_ids(row["retrieved_document_ids"]))
        )
        for row in answer_rows
    )
    recall_at_k = _metric_ratio(true_positive, gold_total)
    citation_precision = _metric_ratio(true_positive, retrieved_total)
    citation_recall = recall_at_k
    citation_f1 = round(
        0.0
        if citation_precision + citation_recall == 0
        else 2
        * citation_precision
        * citation_recall
        / (citation_precision + citation_recall),
        6,
    )
    mrr_at_k = round(
        sum(
            0.0
            if not (
                rank := next(
                    (
                        position
                        for position, document_id in enumerate(
                            _stable_unique_document_ids(row["retrieved_document_ids"]),
                            start=1,
                        )
                        if document_id
                        in set(_stable_unique_document_ids(row["gold_document_ids"]))
                    ),
                    None,
                )
            )
            else 1.0 / rank
            for row in answer_rows
        )
        / len(answer_rows),
        6,
    )
    no_answer_correct = sum(
        len(_stable_unique_document_ids(row["retrieved_document_ids"])) == 0
        for row in no_answer_rows
    )
    false_positives = sum(
        len(_stable_unique_document_ids(row["retrieved_document_ids"])) > 0
        for row in no_answer_rows
    )
    return {
        "top_k": top_k,
        "answer_case_count": len(answer_rows),
        "no_answer_case_count": len(no_answer_rows),
        "recall_at_k": recall_at_k,
        "mrr_at_k": mrr_at_k,
        "citation_precision": citation_precision,
        "citation_recall": citation_recall,
        "citation_f1": citation_f1,
        "no_answer_accuracy": _metric_ratio(no_answer_correct, len(no_answer_rows)),
        "no_answer_false_positive_rate": _metric_ratio(
            false_positives, len(no_answer_rows)
        ),
    }


def _csv_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    fields = [
        "case_id",
        "query_hash",
        "requested_top_k",
        "expect_no_answer",
        "gold_document_ids",
        "retrieved_document_ids",
        "retrieved_citation_hashes",
        "retrieval_bundle_hash",
        "citation_count",
        "relevant_rank",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: json.dumps(row[key], ensure_ascii=False, separators=(",", ":"))
                if isinstance(row[key], list)
                else row[key]
                for key in fields
            }
        )
    return output.getvalue().encode("utf-8")


def _report_bytes(
    dataset: Mapping[str, Any],
    index_hash: str,
    metrics: Mapping[str, Any],
    ablations: Mapping[str, Any],
) -> bytes:
    lines = [
        "# Phase 2A deterministic RAG evaluation",
        "",
        "- deterministic_rag_evaluation: true",
        "- provider_calls: 0",
        "- embedding_calls: 0",
        "- latency_not_measured_in_deterministic_run: true",
        "- citation_precision_semantics: document-attribution level",
        f"- dataset_id: {dataset['dataset_id']}",
        f"- dataset_sha256: {_EXPECTED_DATASET_SHA256}",
        f"- index_hash: {index_hash}",
        f"- retriever_version: {RETRIEVER_VERSION}",
        f"- ranker_version: {RANKER_VERSION}",
        "",
        "## Metrics",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in metrics.items())
    lines.extend(["", "## Single-variable top-k ablations", ""])
    lines.extend(
        f"- {label}: {json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
        for label, value in ablations.items()
    )
    lines.extend(
        [
            "",
            "Artifacts: [results.json](results.json), [results.csv](results.csv), [manifest.json](manifest.json).",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _write_bytes(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)


def _write_artifacts_atomically(output: Path, artifacts: Mapping[str, bytes]) -> None:
    if output.exists() or output.is_symlink():
        raise EvaluationValidationError("OUTPUT_PATH_ALREADY_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.partial")
    if partial.exists() or partial.is_symlink():
        raise EvaluationValidationError("OUTPUT_PATH_ALREADY_EXISTS")
    partial.mkdir()
    try:
        for name, contents in artifacts.items():
            _write_bytes(partial / name, contents)
        partial.replace(output)
    except (OSError, RuntimeError):
        for name in artifacts:
            candidate = partial / name
            if candidate.exists() and candidate.is_file():
                candidate.unlink()
        if partial.exists() and partial.is_dir():
            partial.rmdir()
        raise EvaluationValidationError("OUTPUT_WRITE_FAILED") from None


def run(output_root: Path) -> dict[str, Any]:
    """Write four byte-stable artifacts to an approved temporary sandbox directory."""

    output = _safe_output_root(output_root)
    dataset = _load_dataset()
    try:
        index = build_index(dataset["documents"])
    except RAGValidationError:
        raise EvaluationValidationError("INDEX_BUILD_FAILED") from None
    rows = [_result_row(case, index, top_k=_TOP_K) for case in dataset["cases"]]
    metrics = _aggregate(rows, top_k=_TOP_K)
    ablations = {
        f"top_k_{top_k}": {
            "changed_parameter": "top_k",
            "top_k": top_k,
            "metrics": _aggregate(
                [_result_row(case, index, top_k=top_k) for case in dataset["cases"]],
                top_k=top_k,
            ),
        }
        for top_k in (1, _TOP_K, 5)
    }
    results = {
        "schema_version": _SCHEMA_VERSION,
        "deterministic_rag_evaluation": True,
        "provider_calls": 0,
        "embedding_calls": 0,
        "latency_not_measured_in_deterministic_run": True,
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": _EXPECTED_DATASET_SHA256,
        "index_hash": index.index_hash,
        "retriever_version": RETRIEVER_VERSION,
        "ranker_version": RANKER_VERSION,
        "metrics": metrics,
        "ablations": ablations,
        "rows": rows,
    }
    results_bytes = _canonical_json(results)
    csv_bytes = _csv_bytes(rows)
    report_bytes = _report_bytes(dataset, index.index_hash, metrics, ablations)
    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "deterministic_rag_evaluation": True,
        "provider_calls": 0,
        "embedding_calls": 0,
        "latency_not_measured_in_deterministic_run": True,
        "dataset_sha256": _EXPECTED_DATASET_SHA256,
        "index_hash": index.index_hash,
        "retriever_version": RETRIEVER_VERSION,
        "ranker_version": RANKER_VERSION,
        "command": "python -m benchmark.rag_evaluation.runner --output <sandbox-output>",
        "sha256": {
            "results.json": _sha256_bytes(results_bytes),
            "results.csv": _sha256_bytes(csv_bytes),
            "report.md": _sha256_bytes(report_bytes),
        },
    }
    manifest_bytes = _canonical_json(manifest)
    _write_artifacts_atomically(
        output,
        {
            "results.json": results_bytes,
            "results.csv": csv_bytes,
            "report.md": report_bytes,
            "manifest.json": manifest_bytes,
        },
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run provider-free deterministic RAG evaluation"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = run(args.output)
    except EvaluationValidationError as error:
        print(json.dumps({"status": "REJECTED", "code": error.code}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {"status": "COMPLETED", "manifest": manifest},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
