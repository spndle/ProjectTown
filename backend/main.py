"""Uvicorn entry point kept separate from the side-effect-free app factory."""

from .app.main import create_app

app = create_app()

__all__ = ["app", "create_app"]
