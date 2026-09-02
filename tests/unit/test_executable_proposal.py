from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from backend.app import executable_proposal
from backend.app.controlled_apply import prepare_apply_plan
from backend.app.executable_proposal import (
    PROPOSAL_HASH_DOMAIN,
    ExecutableProposalError,
    create_executable_proposal,
    load_executable_proposal,
    parse_executable_proposal_bytes,
    serialize_executable_proposal,
    verify_executable_proposal,
)
from backend.app.material_workflow import (
    PublicationAttentionError,
    PublicationRollbackError,
    create_draft,
    generate_result,
    publish_new_file,
    serialize_session,
)


def _ready(tmp_path: Path, body: bytes = b"# Existing README\n"):
    root, evidence = tmp_path / "materials", tmp_path / "evidence"
    root.mkdir()
    evidence.mkdir()
    target = root / "README.md"
    target.write_bytes(body)
    draft = create_draft(
        root,
        ["README.md"],
        task="Improve README with source-grounded details.",
        artifact_kind="readme",
        readme_target="README.md",
    )
    result = generate_result(root, draft, draft.contract_hash)
    result_path = evidence / "result.json"
    publish_new_file(root, result_path, serialize_session(result))
    plan_path = evidence / "plan.json"
    prepare_apply_plan(root, result_path, target, plan_path)
    return root, evidence, target, result_path, plan_path


def test_create_check_deterministic_and_never_writes_target(tmp_path: Path) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    before = target.read_bytes()
    first = create_executable_proposal(
        root, result, target, plan, evidence / "one.json"
    )
    second = create_executable_proposal(
        root, result, target, plan, evidence / "two.json"
    )
    assert serialize_executable_proposal(first) == serialize_executable_proposal(second)
    assert verify_executable_proposal(root, first, result, target, plan)
    assert load_executable_proposal(evidence / "one.json", material_root=root) == first
    assert target.read_bytes() == before
    post = base64.b64decode(first.post_image_base64)
    assert (
        post.startswith(before)
        and post.count(b"projecttown:managed-readme-section:v1:start") == 1
        and post.count(b"projecttown:managed-readme-section:v1:end") == 1
    )


@pytest.mark.parametrize("body", [b"# x\r\n", b"# x", b"\xef\xbb\xbf# x\n"])
def test_newline_bom_variants(tmp_path: Path, body: bytes) -> None:
    root, evidence, target, result, plan = _ready(tmp_path, body)
    proposal = create_executable_proposal(
        root, result, target, plan, evidence / "proposal.json"
    )
    assert base64.b64decode(proposal.post_image_base64).startswith(body)


@pytest.mark.parametrize(
    "body", [b"x\rbroken", b"x\r\ny\n", b"x\x00", b"x\xef\xbb\xbf"]
)
def test_rejects_unsafe_original_forms(tmp_path: Path, body: bytes) -> None:
    root, evidence, target, result, plan = _ready(tmp_path, body)
    with pytest.raises(ExecutableProposalError):
        create_executable_proposal(
            root, result, target, plan, evidence / "proposal.json"
        )


def test_parse_tamper_marker_and_drift_fail_closed(tmp_path: Path) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    proposal = create_executable_proposal(
        root, result, target, plan, evidence / "proposal.json"
    )
    data = serialize_executable_proposal(proposal).replace(
        b"complete_post_image", b"tampered_post_image"
    )
    with pytest.raises(ExecutableProposalError):
        parse_executable_proposal_bytes(data)
    target.write_text("# changed\n", encoding="utf-8")
    assert not verify_executable_proposal(root, proposal, result, target, plan)
    target.write_text("# Existing README\n", encoding="utf-8")
    target.write_text("x projecttown:managed-readme-section:v1\n", encoding="utf-8")
    with pytest.raises(ExecutableProposalError):
        create_executable_proposal(root, result, target, plan, evidence / "new.json")


def test_recomputed_hash_cannot_forge_semantic_bindings(tmp_path: Path) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    proposal = create_executable_proposal(
        root, result, target, plan, evidence / "proposal.json"
    )
    payload = proposal.model_dump(mode="json")
    payload["target_relative_path"] = "forged.md"
    payload["selected_scope"] = ["forged.md"]
    payload["proposed_write_scope"] = ["forged.md"]
    payload.pop("proposal_hash")
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    payload["proposal_hash"] = hashlib.sha256(
        PROPOSAL_HASH_DOMAIN.encode("ascii") + b"\x00" + encoded
    ).hexdigest()
    with pytest.raises(ExecutableProposalError):
        parse_executable_proposal_bytes(
            json.dumps(
                payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )


def test_parse_rejects_duplicate_and_noncanonical_base64(tmp_path: Path) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    proposal = create_executable_proposal(
        root, result, target, plan, evidence / "proposal.json"
    )
    canonical = serialize_executable_proposal(proposal)
    with pytest.raises(ExecutableProposalError):
        parse_executable_proposal_bytes(canonical[:-1] + b',"schema_version":"x"}')
    payload = proposal.model_dump(mode="json")
    payload["post_image_base64"] += "="
    with pytest.raises(ExecutableProposalError):
        parse_executable_proposal_bytes(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )


def _rehashed_malformed(proposal, mutate):
    payload = proposal.model_dump(mode="json")
    post = bytearray(base64.b64decode(payload["post_image_base64"]))
    mutate(post, payload["append_offset_bytes"])
    before, appended = (
        bytes(post[: payload["append_offset_bytes"]]),
        bytes(post[payload["append_offset_bytes"] :]),
    )
    payload.update(
        {
            "post_image_base64": base64.b64encode(post).decode("ascii"),
            "post_image_sha256": hashlib.sha256(post).hexdigest(),
            "post_image_size_bytes": len(post),
            "appended_bytes_sha256": hashlib.sha256(appended).hexdigest(),
            "appended_bytes_size": len(appended),
            "display_diff_sha256": hashlib.sha256(
                executable_proposal._display_diff(
                    before, bytes(post), payload["target_relative_path"]
                )
            ).hexdigest(),
            "display_diff_size_bytes": len(
                executable_proposal._display_diff(
                    before, bytes(post), payload["target_relative_path"]
                )
            ),
        }
    )
    payload.pop("proposal_hash")
    payload["proposal_hash"] = hashlib.sha256(
        PROPOSAL_HASH_DOMAIN.encode("ascii")
        + b"\x00"
        + json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda post, offset: post.__setitem__(slice(offset + 2, offset + 7), b"noise"),
        lambda post, _offset: post.extend(b"noise\n"),
        lambda post, _offset: post.extend(b"\n"),
        lambda post, offset: post.__setitem__(
            slice(offset + 2, offset + 2), b"\xef\xbb\xbf"
        ),
        lambda post, offset: post.__setitem__(
            slice(offset + 2, offset + 2), "\u202e".encode()
        ),
        lambda post, offset: post.__setitem__(slice(offset + 2, offset + 2), b"\r"),
    ],
)
def test_standalone_validation_rejects_rehashed_malformed_generated_section(
    tmp_path: Path, mutate
) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    proposal = create_executable_proposal(
        root, result, target, plan, evidence / "proposal.json"
    )
    with pytest.raises(ExecutableProposalError):
        parse_executable_proposal_bytes(_rehashed_malformed(proposal, mutate))


def test_separator_and_terminal_eol_contract_for_lf_crlf_and_no_newline(
    tmp_path: Path,
) -> None:
    for name, body, separator in (
        ("lf", b"x\n", b"\n"),
        ("crlf", b"x\r\n", b"\r\n"),
        ("none", b"x", b"\n\n"),
    ):
        directory = tmp_path / name
        directory.mkdir()
        root, evidence, target, result, plan = _ready(directory, body)
        proposal = create_executable_proposal(
            root, result, target, plan, evidence / "proposal.json"
        )
        appended = base64.b64decode(proposal.post_image_base64)[len(body) :]
        assert appended.startswith(
            separator + b"<!-- projecttown:managed-readme-section:v1:start -->"
        )
        assert appended.endswith(b"-->" + (b"\r\n" if b"\r\n" in body else b"\n"))


@pytest.mark.parametrize("bad_path", ["../forged.md", "README\\.md", "re\u0301adme.md"])
def test_standalone_validation_rejects_rehashed_noncanonical_scope(
    tmp_path: Path, bad_path: str
) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    proposal = create_executable_proposal(
        root, result, target, plan, evidence / "proposal.json"
    )
    payload = proposal.model_dump(mode="json")
    payload["target_relative_path"] = bad_path
    payload["selected_scope"] = [bad_path]
    payload["proposed_write_scope"] = [bad_path]
    payload.pop("proposal_hash")
    payload["proposal_hash"] = hashlib.sha256(
        PROPOSAL_HASH_DOMAIN.encode("ascii")
        + b"\x00"
        + json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    with pytest.raises(ExecutableProposalError):
        parse_executable_proposal_bytes(
            json.dumps(
                payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode()
        )


@pytest.mark.parametrize(
    "failure", [PublicationAttentionError, PublicationRollbackError]
)
def test_publication_recovery_signals_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise failure()

    monkeypatch.setattr(executable_proposal, "publish_new_file", fail)
    with pytest.raises(failure):
        create_executable_proposal(
            root, result, target, plan, evidence / "proposal.json"
        )
    assert target.read_bytes() == b"# Existing README\n"


def test_output_collision_and_external_path_required(tmp_path: Path) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    out = evidence / "proposal.json"
    create_executable_proposal(root, result, target, plan, out)
    with pytest.raises(ExecutableProposalError):
        create_executable_proposal(root, result, target, plan, out)
    with pytest.raises(ExecutableProposalError):
        load_executable_proposal(root / "inside.json", material_root=root)


def test_external_proposal_path_shapes_fail_closed(tmp_path: Path) -> None:
    root, evidence, _target, result, plan = _ready(tmp_path)
    output = evidence / "proposal.json"
    create_executable_proposal(root, result, _target, plan, output)
    with pytest.raises(ExecutableProposalError):
        load_executable_proposal(Path("proposal.json"), material_root=root)
    with pytest.raises(ExecutableProposalError):
        load_executable_proposal(evidence, material_root=root)
    with pytest.raises(ExecutableProposalError):
        load_executable_proposal(output, material_root=root / ".." / root.name)
    internal = root / "proposal.json"
    internal.write_bytes(output.read_bytes())
    with pytest.raises(ExecutableProposalError):
        load_executable_proposal(internal, material_root=root)

    link = evidence / "proposal-link.json"
    try:
        os.link(output, link)
    except OSError as error:
        pytest.skip(f"hard links unavailable on this platform: {error}")
    with pytest.raises(ExecutableProposalError):
        load_executable_proposal(link, material_root=root)


def test_symlinked_external_proposal_is_rejected_when_supported(tmp_path: Path) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    output = evidence / "proposal.json"
    create_executable_proposal(root, result, target, plan, output)
    link = evidence / "proposal-link.json"
    try:
        link.symlink_to(output)
    except OSError as error:
        pytest.skip(f"symlinks unavailable on this platform: {error}")
    with pytest.raises(ExecutableProposalError):
        load_executable_proposal(link, material_root=root)


def test_size_limits_have_exact_boundary_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    before = target.read_bytes()
    session, _ = executable_proposal._result(root, result)
    post, _appended, _eol = executable_proposal._compose(before, session)
    monkeypatch.setattr(executable_proposal, "_MAX_POST_IMAGE_BYTES", len(post))
    proposal = create_executable_proposal(
        root, result, target, plan, evidence / "boundary.json"
    )
    serialized = serialize_executable_proposal(proposal)
    monkeypatch.setattr(executable_proposal, "_MAX_PROPOSAL_BYTES", len(serialized))
    assert serialize_executable_proposal(proposal) == serialized
    assert parse_executable_proposal_bytes(serialized) == proposal
    monkeypatch.setattr(executable_proposal, "_MAX_PROPOSAL_BYTES", len(serialized) - 1)
    with pytest.raises(ExecutableProposalError):
        serialize_executable_proposal(proposal)
    monkeypatch.setattr(executable_proposal, "_MAX_POST_IMAGE_BYTES", len(post) - 1)
    with pytest.raises(ExecutableProposalError):
        create_executable_proposal(
            root, result, target, plan, evidence / "too-big.json"
        )
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    "body",
    [
        b"\xff",
        b"unsafe\x01",
        b"unsafe\x7f",
        b"unsafe\xc2\x80",
        b"unsafe\xd8\x9c",  # U+061C
        b"unsafe\xe2\x80\x8e",  # U+200E
        b"unsafe\xe2\x80\x8f",  # U+200F
        b"unsafe\xe2\x80\xaa",  # U+202A
        b"unsafe\xe2\x80\xae",  # U+202E
        b"unsafe\xe2\x81\xa6",  # U+2066
        b"unsafe\xe2\x81\xa9",  # U+2069
        b"internal\xef\xbb\xbfbom",
    ],
)
def test_original_text_rejects_encoding_controls_bidi_and_internal_bom(
    body: bytes,
) -> None:
    with pytest.raises(ExecutableProposalError) as rejected:
        executable_proposal._validate_original(body)
    assert rejected.value.code in {
        "INVALID_UTF8",
        "UNSAFE_ORIGINAL_TEXT",
        "INVALID_BOM",
    }


def test_source_result_plan_and_mixed_binding_drift_leave_target_unchanged(
    tmp_path: Path,
) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    before = target.read_bytes()
    result.write_bytes(result.read_bytes() + b" ")
    with pytest.raises(ExecutableProposalError):
        create_executable_proposal(
            root, result, target, plan, evidence / "result-drift.json"
        )
    assert target.read_bytes() == before

    independent = tmp_path / "independent"
    independent.mkdir()
    _root2, _evidence2, target2, result2, plan2 = _ready(independent)
    with pytest.raises(ExecutableProposalError):
        create_executable_proposal(
            root, result2, target, plan2, evidence / "mixed.json"
        )
    assert target.read_bytes() == before
    assert target2.read_bytes() == b"# Existing README\n"

    # Recreate a canonical Result/plan, then drift the selected source after planning.
    root3, evidence3 = tmp_path / "source-materials", tmp_path / "source-evidence"
    root3.mkdir()
    evidence3.mkdir()
    target3 = root3 / "README.md"
    source3 = root3 / "source.md"
    target3.write_text("# Existing README\n", encoding="utf-8")
    source3.write_text("stable source\n", encoding="utf-8")
    draft3 = create_draft(
        root3,
        ["README.md", "source.md"],
        task="Improve README with source-grounded details.",
        artifact_kind="readme",
        readme_target="README.md",
    )
    result3 = generate_result(root3, draft3, draft3.contract_hash)
    result3_path = evidence3 / "result.json"
    publish_new_file(root3, result3_path, serialize_session(result3))
    plan3 = evidence3 / "plan.json"
    prepare_apply_plan(root3, result3_path, target3, plan3)
    source3.write_text("drifted source\n", encoding="utf-8")
    with pytest.raises(ExecutableProposalError):
        create_executable_proposal(
            root3, result3_path, target3, plan3, evidence3 / "x.json"
        )
    assert target3.read_bytes() == b"# Existing README\r\n"


@pytest.mark.parametrize("race", ["plan", "result"])
def test_prepublication_binding_race_rejects_without_output_or_target_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    before = target.read_bytes()
    original_target = executable_proposal._target_bytes
    calls = 0

    def change_after_first_target(*args: object):
        nonlocal calls
        value = original_target(*args)
        calls += 1
        if calls == 1:
            changed = plan if race == "plan" else result
            changed.write_bytes(changed.read_bytes() + b" ")
        return value

    monkeypatch.setattr(executable_proposal, "_target_bytes", change_after_first_target)
    output = evidence / f"{race}-race.json"
    with pytest.raises(ExecutableProposalError):
        create_executable_proposal(root, result, target, plan, output)
    assert not output.exists()
    assert target.read_bytes() == before


def test_post_publication_check_attention_retains_record_then_fresh_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    before = target.read_bytes()
    output = evidence / "attention.json"
    monkeypatch.setattr(
        executable_proposal, "verify_executable_proposal", lambda *_: False
    )
    with pytest.raises(PublicationAttentionError):
        create_executable_proposal(root, result, target, plan, output)
    assert output.exists()
    assert target.read_bytes() == before
    monkeypatch.undo()
    recovered = create_executable_proposal(
        root, result, target, plan, evidence / "recovered.json"
    )
    assert verify_executable_proposal(root, recovered, result, target, plan)
    assert target.read_bytes() == before


def test_rehashed_live_binding_tamper_and_json_shapes_fail_closed(
    tmp_path: Path,
) -> None:
    root, evidence, target, result, plan = _ready(tmp_path)
    proposal = create_executable_proposal(
        root, result, target, plan, evidence / "proposal.json"
    )
    payload = proposal.model_dump(mode="json")
    payload["apply_plan_hash"] = "0" * 64
    payload.pop("proposal_hash")
    payload["proposal_hash"] = executable_proposal._proposal_hash(payload)
    forged = parse_executable_proposal_bytes(
        executable_proposal._canonical_json(payload)
    )
    assert not verify_executable_proposal(root, forged, result, target, plan)
    for invalid in (
        b"{",
        b"[]",
        json.dumps(
            {**payload, "unexpected": True}, sort_keys=True, separators=(",", ":")
        ).encode(),
        json.dumps(
            {**payload, "selected_scope": "README.md"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(),
    ):
        with pytest.raises(ExecutableProposalError):
            parse_executable_proposal_bytes(invalid)
