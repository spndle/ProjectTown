from __future__ import annotations

from pathlib import Path

_ASSETS = Path(__file__).parents[2] / "backend" / "app" / "static" / "workspace"


def test_authoring_ui_has_safe_catalog_confirmation_and_exports() -> None:
    html = (_ASSETS / "index.html").read_text(encoding="utf-8")
    script = (_ASSETS / "app.js").read_text(encoding="utf-8")
    assert 'id="catalog-list"' in html
    assert 'id="confirm-generation"' in html
    assert 'id="readme-target"' in html
    assert 'data-export="markdown"' in html and 'data-export="pdf"' in html
    assert "crypto.randomUUID" in script
    assert '"Idempotency-Key"' in script
    assert "confirmation_phrase:draft.confirmation_phrase" in script
    assert "body.readme_target_id=readmeTarget.value" in script
    assert "source_ids" in script
    assert "needs_user_decision" in script and "State: stale" in script
    assert 'type="file"' not in html
    assert '<input type="text" name="path"' not in html


def test_authoring_ui_keeps_phase4a_read_only_browsing() -> None:
    html = (_ASSETS / "index.html").read_text(encoding="utf-8")
    script = (_ASSETS / "app.js").read_text(encoding="utf-8")
    assert 'id="task-list"' in html and 'id="preview"' in html
    assert 'request("/api/workspace/authoring/catalog")' in script
    assert "response.status===404" in script
    assert "Read-only workspace browsing is available" in script
    for forbidden in ("/apply", "/restore", "/publish", "provider", "Authorization"):
        assert forbidden not in script
