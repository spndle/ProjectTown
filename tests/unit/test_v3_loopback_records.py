from __future__ import annotations

import json
import os

import pytest

from backend.app.v3_loopback_records import (
    LoopbackRecordError,
    load_record,
    make_binding,
    parse_record_bytes,
    publish_create_only,
    serialize_record,
)


def _binding(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    metadata = root.lstat()
    return make_binding(
        web_operation_id="a" * 64,
        work_root=str(root),
        work_root_device=int(metadata.st_dev),
        work_root_inode=int(metadata.st_ino),
        authorization_path=str(tmp_path / "auth.json"),
        authorization_bytes_sha256="b" * 64,
        authorization_hash="c" * 64,
        authorization_schema_version="v3-controlled-write-authorization-v1",
        controlled_operation_id="operation-001",
        material_root=str(tmp_path / "materials"),
        target_relative_path="README.md",
        target_path_sha256="d" * 64,
        target_display="README.md",
        allowed_mutations=("apply", "reconcile"),
    )


def test_create_only_canonical_binding_and_tamper_rejection(tmp_path):
    path = tmp_path / "bindings" / f"{'a' * 64}.json"
    value = _binding(tmp_path)
    path.parent.mkdir()
    publish_create_only(path.parent, path, value)
    assert load_record(path) == value
    with pytest.raises(LoopbackRecordError, match="CREATE_ONLY_CONFLICT"):
        publish_create_only(path.parent, path, value)
    path.write_bytes(path.read_bytes().replace(b"README.md", b"XREADME.md"))
    with pytest.raises(LoopbackRecordError, match="INVALID_RECORD"):
        load_record(path)


def test_binding_parser_rejects_non_objects_duplicates_and_extra_fields(tmp_path):
    value = _binding(tmp_path)
    canonical = value.model_dump(mode="json")
    samples = [
        b"[]",
        b'{"schema_version":"v3-loopback-operation-binding-v1","schema_version":"v3-loopback-operation-binding-v1"}',
        json.dumps({**canonical, "extra": True}, sort_keys=True).encode(),
    ]
    for sample in samples:
        with pytest.raises(LoopbackRecordError, match="INVALID_RECORD"):
            parse_record_bytes(sample)


def test_record_loader_rejects_hardlinks_and_noncanonical_paths(tmp_path):
    directory = tmp_path / "records"
    directory.mkdir()
    value = _binding(tmp_path)
    original = directory / "record.json"
    publish_create_only(directory, original, value)
    alias = directory / "alias.json"
    try:
        os.link(original, alias)
    except OSError:
        pytest.skip("hard links are unavailable")
    with pytest.raises(LoopbackRecordError, match="INVALID_RECORD"):
        load_record(original)
    with pytest.raises(LoopbackRecordError, match="INVALID_RECORD"):
        load_record(alias)


@pytest.mark.parametrize(
    "payload",
    [b'{"schema_version":"x","schema_version":"y"}', b"{}", b"[]"],
)
def test_record_parser_fails_closed_for_noncanonical_or_unknown_bytes(payload):
    with pytest.raises(LoopbackRecordError, match="INVALID_RECORD"):
        parse_record_bytes(payload)


def test_binding_serialization_is_canonical_and_rejects_extra_fields(tmp_path):
    value = _binding(tmp_path)
    data = serialize_record(value)
    assert parse_record_bytes(data) == value
    with pytest.raises(LoopbackRecordError, match="INVALID_RECORD"):
        parse_record_bytes(data[:-1] + b',"extra":true}')
