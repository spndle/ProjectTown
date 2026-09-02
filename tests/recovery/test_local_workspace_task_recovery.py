from __future__ import annotations

import pytest

from backend.app.local_workspace_task import (
    LocalWorkspaceTaskError,
    publish_binding,
    verify_binding,
)
from tests.unit.test_local_workspace_task import _ready


def test_stale_material_is_rejected_then_recovers_with_new_binding(tmp_path):
    value = _ready(tmp_path)
    binding, work = value["binding"], value["work"]
    publish_binding(work, binding)
    (value["material"] / "notes.md").write_text("# Changed\nEvidence", encoding="utf-8")
    with pytest.raises(LocalWorkspaceTaskError, match="MATERIAL_STALE_OR_MISMATCH"):
        verify_binding(binding, work)
