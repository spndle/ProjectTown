"""Explicit live smoke test; normal pytest must not create a provider client."""

from __future__ import annotations

import os

import pytest

from benchmark.real_model_evaluation.runner import run_evaluation

pytestmark = pytest.mark.skipif(
    os.environ.get("PROJECTTOWN_RUN_LIVE_MODEL_TESTS") != "1",
    reason="real model tests require PROJECTTOWN_RUN_LIVE_MODEL_TESTS=1",
)


def test_openai_gpt5_mini_real_eval_requires_runner_gates(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result = run_evaluation(
        live=True,
        database_path=tmp_path / "real-eval.db",
        results_root=tmp_path / "results",
    )
    if os.environ.get("PROJECTTOWN_REAL_MODEL_EVAL_ENABLED") != "true":
        assert result["status"] == "NOT_RUN"
        assert result["reason"] == "ENV_OPT_IN_REQUIRED"
    elif result["status"] == "NOT_RUN":
        assert result["status"] == "NOT_RUN"
        assert result["reason"] in {
            "OPENAI_CONNECTION_REQUIRED",
            "SECRET_BASE_URL_DENIED",
            "SECRET_CONNECTION_PARTIAL",
            "SECRET_LOCAL_FILE_INVALID",
            "SECRET_LOCAL_FILE_MALFORMED",
            "SECRET_LOCAL_FILE_MISSING",
            "SECRET_LOCAL_FILE_PERMISSIONS_INVALID",
            "SECRET_LOCAL_FILE_PROFILE_DENIED",
            "SECRET_SOURCE_INVALID",
            "SECRET_SOURCE_MIXING_DENIED",
        }
    else:
        assert result["status"] in {"COMPLETED", "FAILED"}
