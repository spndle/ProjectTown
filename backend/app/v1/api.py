from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field

from ..errors import AppError
from .evaluation import run as run_benchmark
from .models import (
    ArtifactReview,
    DecisionCreate,
    FailureNavigationResponse,
    QuestConfirm,
    QuestControl,
    QuestCreate,
    QuestStatus,
)
from .service import V1QuestService
from .storage import V1Storage


class BenchmarkRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = Field(default="smoke", pattern="^(smoke|formal)$")
    seed: int = Field(default=1729, ge=0, le=2_147_483_647)


class BenchmarkManager:
    def __init__(self, storage: V1Storage, output_root: Path) -> None:
        self.storage = storage
        self.output_root = output_root.resolve()

    def create(self, request: BenchmarkRunRequest) -> dict[str, Any]:
        run_id = f"benchmark_{uuid.uuid4().hex[:12]}"
        initial = {
            "status": "running",
            "profile": request.profile,
            "seed": request.seed,
            "runtime_simulation": True,
        }
        self.storage.create_benchmark_run(run_id, initial)
        output = self.output_root / run_id
        try:
            rows = run_benchmark(request.profile, output, request.seed)
            summary = _summarize_benchmark(rows)
            completed = {
                **initial,
                "status": "completed",
                "row_count": len(rows),
                "summary": summary,
                "artifacts": {
                    "csv": str(output / "results.csv"),
                    "json": str(output / "results.json"),
                    "svg": str(output / "success.svg"),
                    "report": str(output / "report.md"),
                },
            }
            self.storage.update_benchmark_run(run_id, completed)
            for key, metrics in summary["configurations"].items():
                self.storage.append_benchmark_result(
                    f"{run_id}:{key}",
                    run_id,
                    {"configuration": key, **metrics},
                )
        except Exception as exc:
            failed = {
                **initial,
                "status": "failed",
                "error": {
                    "code": "BENCHMARK_FAILED",
                    "message": "Benchmark execution failed",
                    "exception_type": type(exc).__name__,
                },
            }
            self.storage.update_benchmark_run(run_id, failed)
            raise
        result = self.storage.get_benchmark_run(run_id)
        assert result is not None
        return result

    def get(self, run_id: str) -> dict[str, Any]:
        result = self.storage.get_benchmark_run(run_id)
        if result is None:
            raise AppError(
                "BENCHMARK_RUN_NOT_FOUND",
                f"Benchmark run '{run_id}' was not found",
                status_code=404,
            )
        result["configuration_results"] = self.storage.list_benchmark_results(run_id)
        return result


def _summarize_benchmark(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[f"{row['config']}:{row['ablation']}"].append(row)
    configurations: dict[str, dict[str, float | int]] = {}
    for key, items in sorted(groups.items()):
        count = len(items)
        configurations[key] = {
            "runs": count,
            "success_rate": sum(item["success"] for item in items) / count,
            "progress_rate": sum(item["progress"] for item in items) / count,
            "false_completion_rate": sum(item["false_completion"] for item in items)
            / count,
            "loop_rate": sum(item["loop_rate"] for item in items) / count,
            "duplicate_side_effects": sum(
                item["duplicate_side_effects"] for item in items
            ),
        }
    return {
        "runtime_simulation": True,
        "configurations": configurations,
        "limitations": [
            "No external model or token measurement was performed.",
            "Results are a deterministic local fault-matrix simulation.",
        ],
    }


def install_v1_routes(
    application: FastAPI,
    *,
    prefix: str,
    service: V1QuestService,
    storage: V1Storage,
    benchmark_output_root: Path,
    websocket_poll_seconds: float,
) -> BenchmarkManager:
    benchmark = BenchmarkManager(storage, benchmark_output_root)

    @application.get(f"{prefix}/health", tags=["v1-system"])
    def runtime_health() -> dict[str, Any]:
        return {
            "status": "ok" if storage.ping() else "degraded",
            "runtime": "event-sourced-v1",
            "api_version": "v2",
            "deployment": "single-node-sqlite",
            "recovery": service.recovery_summary,
        }

    @application.post(
        f"{prefix}/quests",
        status_code=status.HTTP_201_CREATED,
        tags=["v1-quests"],
    )
    def create_quest(payload: QuestCreate) -> dict[str, Any]:
        return service.create_quest(payload)

    @application.get(f"{prefix}/quests", tags=["v1-quests"])
    def list_quests(
        q: str | None = None,
        status_filter: list[QuestStatus] | None = Query(  # noqa: B008
            default=None, alias="status"
        ),
        offset: int = Query(default=0, ge=0),
        limit: int | None = Query(default=None, ge=1, le=100),
    ) -> dict[str, Any]:
        if q is None and status_filter is None and offset == 0 and limit is None:
            items = service.list_quests()
            return {"items": items, "total": len(items)}
        items, total = service.search_quests(
            q=q,
            statuses=[item.value for item in status_filter or []],
            offset=offset,
            limit=limit,
        )
        return {"items": items, "total": total}

    @application.get(f"{prefix}/templates", tags=["v1-quests"])
    def list_templates() -> dict[str, Any]:
        items = service.list_templates()
        return {"items": items, "total": len(items)}

    @application.get(
        f"{prefix}/quests/{{quest_id}}/failure",
        response_model=FailureNavigationResponse,
        tags=["v1-quests"],
    )
    def get_failure_navigation(quest_id: str) -> dict[str, Any]:
        return service.get_failure_navigation(quest_id)

    @application.get(f"{prefix}/quests/{{quest_id}}", tags=["v1-quests"])
    def get_quest(quest_id: str) -> dict[str, Any]:
        return service.get_quest(quest_id)

    @application.post(f"{prefix}/quests/{{quest_id}}/confirm", tags=["v1-quests"])
    def confirm_quest(quest_id: str, payload: QuestConfirm) -> dict[str, Any]:
        return service.confirm_quest(quest_id, payload)

    @application.post(
        f"{prefix}/quests/{{quest_id}}/run",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["v1-quests"],
    )
    def run_quest(quest_id: str, payload: QuestControl) -> dict[str, Any]:
        state = service.start_quest(quest_id, payload.expected_state_version)
        return {
            "quest_id": quest_id,
            "status": state["status"],
            "state_version": state["state_version"],
            "message": "Quest execution started",
        }

    @application.post(
        f"{prefix}/quests/{{quest_id}}/pause",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["v1-quests"],
    )
    def pause_quest(quest_id: str, payload: QuestControl) -> dict[str, Any]:
        return service.pause_quest(quest_id, payload.expected_state_version)

    @application.post(
        f"{prefix}/quests/{{quest_id}}/resume",
        status_code=status.HTTP_202_ACCEPTED,
        tags=["v1-quests"],
    )
    def resume_quest(quest_id: str, payload: QuestControl) -> dict[str, Any]:
        return service.resume_quest(quest_id, payload.expected_state_version)

    @application.post(
        f"{prefix}/quests/{{quest_id}}/decisions",
        tags=["v1-decisions"],
    )
    def create_decision(quest_id: str, payload: DecisionCreate) -> dict[str, Any]:
        return service.submit_decision(quest_id, payload)

    @application.get(f"{prefix}/quests/{{quest_id}}/decisions", tags=["v1-decisions"])
    def list_decisions(quest_id: str) -> dict[str, Any]:
        items = service.get_decisions(quest_id)
        return {"items": items, "total": len(items)}

    @application.get(f"{prefix}/quests/{{quest_id}}/events", tags=["v1-events"])
    def get_events(
        quest_id: str,
        after_sequence: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        items = service.get_events(quest_id, after_sequence)
        return {"items": items, "total": len(items)}

    @application.get(f"{prefix}/quests/{{quest_id}}/evidence", tags=["v1-evidence"])
    def get_evidence(quest_id: str) -> dict[str, Any]:
        items = service.get_evidence(quest_id)
        return {"items": items, "total": len(items)}

    @application.get(f"{prefix}/quests/{{quest_id}}/artifacts", tags=["v1-artifacts"])
    def get_artifacts(quest_id: str) -> dict[str, Any]:
        return service.get_artifacts(quest_id)

    @application.get(
        f"{prefix}/quests/{{quest_id}}/artifacts/{{artifact_id}}/preview",
        tags=["v1-artifacts"],
    )
    def preview_artifact(quest_id: str, artifact_id: str) -> dict[str, Any]:
        return service.preview_artifact(quest_id, artifact_id)

    @application.post(
        f"{prefix}/quests/{{quest_id}}/artifacts/review", tags=["v1-artifacts"]
    )
    def review_artifacts(quest_id: str, payload: ArtifactReview) -> dict[str, Any]:
        return service.review_artifacts(quest_id, payload)

    @application.post(
        f"{prefix}/benchmark/runs",
        status_code=status.HTTP_201_CREATED,
        tags=["v1-benchmark"],
    )
    def create_benchmark_run(payload: BenchmarkRunRequest) -> dict[str, Any]:
        return benchmark.create(payload)

    @application.get(f"{prefix}/benchmark/runs/{{run_id}}", tags=["v1-benchmark"])
    def get_benchmark_run(run_id: str) -> dict[str, Any]:
        return benchmark.get(run_id)

    async def stream_quest(websocket: WebSocket, quest_id: str) -> None:
        try:
            state = service.get_quest(quest_id)
        except AppError:
            await websocket.close(code=4404, reason="Quest not found")
            return
        raw_resume = websocket.query_params.get("resume_after", "0")
        try:
            resume_after = int(raw_resume)
        except ValueError:
            await websocket.close(code=4400, reason="Invalid resume_after")
            return
        if resume_after < 0 or resume_after > state["state_version"]:
            await websocket.close(code=4409, reason="Event sequence gap")
            return
        await websocket.accept()
        await websocket.send_json(
            {
                "type": "snapshot",
                "quest": state,
                "last_sequence": state["state_version"],
                "delivery": "ordered-at-least-once",
            }
        )
        last_sequence = resume_after
        try:
            while True:
                events = service.get_events(quest_id, last_sequence)
                for event in events:
                    await websocket.send_json({"type": "event", "event": event})
                    last_sequence = event["sequence"]
                await asyncio.sleep(websocket_poll_seconds)
        except WebSocketDisconnect:
            return

    application.websocket(f"{prefix}/ws/quests/{{quest_id}}")(stream_quest)
    application.websocket("/ws/quests/{quest_id}")(stream_quest)
    return benchmark
