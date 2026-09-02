from backend.app.agent import RuleBasedAgent
from backend.app.runtime import SparseMessageBus
from backend.app.v1.models import QuestCreate
from backend.app.v1.orchestration import build_handoff, compile_draft, replan_plan


def test_compile_python_draft_is_deterministic_dag_with_criteria():
    payload = QuestCreate(goal="create a python cli")
    first = compile_draft(RuleBasedAgent(), payload, "quest-1")
    second = compile_draft(RuleBasedAgent(), payload, "quest-1")
    assert first == second
    nodes = first[3]["milestones"]
    ids = {n["id"] for n in nodes}
    for node in nodes:
        assert node["id"] not in node["dependencies"]
        assert set(node["dependencies"]) <= ids
    assert first[3]["metadata"]["branches"]
    assert any(c["kind"] == "python_syntax" for c in first[2]["acceptance_criteria"])
    assert first[4] == ["planner", "executor", "verifier"]
    assert compile_draft(
        RuleBasedAgent(), QuestCreate(goal="small doc", force_multi_agent=True), "q"
    )[4] == ["planner", "executor", "verifier"]


def test_replan_preserves_completed_prefix_and_hits_budget():
    plan = compile_draft(
        RuleBasedAgent(), QuestCreate(goal="create a python cli"), "quest-1"
    )[3]
    plan["milestones"][0]["status"] = "completed"
    plan["milestones"][0]["evidence_ids"] = ["evidence-fixed"]
    state = {"plan": plan, "replans": 0}
    result = replan_plan(state, plan["milestones"][1]["id"], "syntax failure", budget=1)
    assert result["plan"]["version"] == 2
    assert result["plan"]["milestones"][0] == plan["milestones"][0]
    assert result["plan"]["milestones"][1]["id"] != plan["milestones"][1]["id"]
    suffix = result["plan"]["milestones"][1:]
    ids = {m["id"] for m in suffix} | {result["plan"]["milestones"][0]["id"]}
    assert all(set(m["dependencies"]) <= ids for m in suffix)
    assert replan_plan(
        {"plan": plan, "replans": 1}, plan["milestones"][1]["id"], "again", budget=1
    )["requires_user"]


def test_handoff_is_sparse_and_artifact_first():
    handoff = build_handoff("q", 2, ["e2", "e1", "e1"], "result")
    assert handoff["evidence_ids"] == ["e1", "e2"]
    assert "reasoning" not in handoff


def test_sparse_message_bus_deduplicates_identical_handoff():
    bus = SparseMessageBus()
    first = bus.publish(
        "q", 1, "planner", "executor", ["e1"], "done", {"artifact": "x"}
    )
    duplicate = bus.publish(
        "q", 2, "planner", "executor", ["e1"], "done", {"artifact": "x"}
    )
    assert first.accepted
    assert not duplicate.accepted
    assert duplicate.reason == "duplicate_no_new_evidence"
