"""Export reproducible v1 runtime replays from the real service stack.

The examples intentionally retain the runtime event stream, tool receipts, and
verifier evidence.  They are generated from isolated SQLite/sandbox instances;
no backend state or illustrative fixtures are used.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.agent import RuleBasedAgent
from backend.app.tools import Sandbox, build_default_registry
from backend.app.v1.gateway import ToolGateway
from backend.app.v1.models import Budget, QuestConfirm, QuestCreate
from backend.app.v1.service import V1QuestService
from backend.app.v1.storage import V1Storage

TERMINAL_OR_BLOCKED = {
    "completed",
    "budget_exhausted",
    "failed",
    "waiting_user",
    "paused",
}


def _wait(
    service: V1QuestService, quest_id: str, statuses: set[str] = TERMINAL_OR_BLOCKED
) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    state: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        state = service.get_quest(quest_id)
        if state["status"] in statuses:
            return state
        time.sleep(0.01)
    raise RuntimeError(f"quest did not stabilize: {state}")


def _create_and_start(service: V1QuestService, payload: QuestCreate) -> str:
    draft = service.create_quest(payload)
    planned = service.confirm_quest(
        draft["id"],
        QuestConfirm(
            expected_state_version=draft["state_version"],
            expected_contract_version=1,
        ),
    )
    service.start_quest(draft["id"], planned["state_version"])
    return draft["id"]


def _replay(
    service: V1QuestService,
    storage: V1Storage,
    quest_id: str,
    scenario: str,
    *,
    expected_status: str,
    expected_events: set[str],
) -> dict[str, Any]:
    final_state = service.get_quest(quest_id)
    events = service.get_events(quest_id)
    event_types = {event["event_type"] for event in events}
    if final_state["status"] != expected_status:
        raise RuntimeError(f"{scenario}: expected {expected_status}, got {final_state}")
    if not expected_events <= event_types:
        raise RuntimeError(
            f"{scenario}: missing events {expected_events - event_types}"
        )
    receipts_by_action = {
        receipt["action_id"]: receipt
        for event in events
        if (
            receipt := event["payload"].get(
                "last_receipt", event["payload"].get("patch", {}).get("last_receipt")
            )
        )
        is not None
    }
    receipts = []
    for action_id, receipt in receipts_by_action.items():
        stored = storage.get_action(action_id)
        if stored is not None:
            receipt = {
                **receipt,
                "status": stored["status"],
                "result": stored.get("result"),
                "error": stored.get("error"),
            }
        receipts.append(receipt)
    return {
        "version": "1.0.0",
        "generated_from_runtime": True,
        "scenario": scenario,
        "quest_id": quest_id,
        "final_status": final_state["status"],
        "final_error": final_state.get("error"),
        "events": events,
        "receipts": receipts,
        "evidence": service.get_evidence(quest_id),
        "verification_results": storage.list_verification_results(quest_id),
        "progress_entries": storage.list_progress(quest_id),
    }


def _normal(service: V1QuestService, storage: V1Storage) -> dict[str, Any]:
    quest_id = _create_and_start(
        service,
        QuestCreate(
            goal="Create a minimal Python CLI",
            template_id="python_starter",
            workspace="replays/normal",
        ),
    )
    _wait(service, quest_id)
    return _replay(
        service,
        storage,
        quest_id,
        "normal",
        expected_status="completed",
        expected_events={"ToolCommitted", "MilestoneVerified", "QuestCompleted"},
    )


def _response_loss(service: V1QuestService, storage: V1Storage) -> dict[str, Any]:
    draft = service.create_quest(
        QuestCreate(
            goal="Create a recoverable project brief",
            template_id="project_brief",
            workspace="replays/response-loss",
        )
    )
    planned = service.confirm_quest(
        draft["id"],
        QuestConfirm(
            expected_state_version=draft["state_version"],
            expected_contract_version=1,
        ),
    )
    original_write_file = service.tools._tools["write_file"]
    fault_armed = False

    def arm_response_loss(workspace: str, arguments: dict[str, Any]) -> Any:
        nonlocal fault_armed
        result = original_write_file(workspace, arguments)
        if not fault_armed:
            service.gateway.faults.points.add("after_effect_before_receipt")
            fault_armed = True
        return result

    service.tools._tools["write_file"] = arm_response_loss
    service.start_quest(draft["id"], planned["state_version"])
    paused = _wait(service, draft["id"], {"paused"})
    if not paused["recovery_required"]:
        raise RuntimeError("response-loss did not require recovery")
    service.resume_quest(draft["id"], paused["state_version"])
    _wait(service, draft["id"], {"completed", "failed"})
    replay = _replay(
        service,
        storage,
        draft["id"],
        "response-loss-recovery",
        expected_status="completed",
        expected_events={
            "ToolEffectUnknown",
            "RecoveryStarted",
            "RecoveryCompleted",
            "QuestCompleted",
        },
    )
    replay["recovery_evidence"] = {
        "fault_point": "after_effect_before_receipt",
        "unknown_effect_observed": True,
        "reconciled_receipt_observed": any(
            receipt.get("result", {}).get("reconciled")
            for receipt in replay["receipts"]
            if isinstance(receipt.get("result"), dict)
        ),
    }
    if not replay["recovery_evidence"]["reconciled_receipt_observed"]:
        raise RuntimeError("response-loss receipt was not reconciled")
    return replay


def _loop_blocked(service: V1QuestService, storage: V1Storage) -> dict[str, Any]:
    def pretend_write(_workspace: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"path": arguments["path"], "created": True}

    service.tools._tools["write_file"] = pretend_write
    quest_id = _create_and_start(
        service,
        QuestCreate(
            goal="Create a README while detecting a repeated false write",
            template_id="readme_builder",
            workspace="replays/loop-blocked",
            budget=Budget(max_replans=5),
        ),
    )
    _wait(service, quest_id)
    return _replay(
        service,
        storage,
        quest_id,
        "watchdog-loop-blocked",
        expected_status="waiting_user",
        expected_events={"LoopDetected"},
    )


def export_replays(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sandbox_tmp = Path("sandbox/tmp")
    sandbox_tmp.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="v1-replays-", dir=sandbox_tmp))
    written: list[Path] = []
    try:
        for filename, build in (
            ("normal.json", _normal),
            ("recovery.json", _response_loss),
            ("loop-blocked.json", _loop_blocked),
        ):
            storage = V1Storage(run_root / f"{filename}.db")
            sandbox = Sandbox(run_root / filename.removesuffix(".json"))
            tools = build_default_registry(sandbox)
            service = V1QuestService(
                storage=storage,
                agent=RuleBasedAgent(),
                sandbox=sandbox,
                tools=tools,
                gateway=ToolGateway(tools, storage),
                max_workers=1,
                watchdog_threshold=2,
            )
            try:
                payload = build(service, storage)
                path = output_dir / filename
                path.write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                written.append(path)
            finally:
                service.close()
                storage.close()
    finally:
        shutil.rmtree(run_root)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("examples/replays"))
    args = parser.parse_args()
    for path in export_replays(args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
