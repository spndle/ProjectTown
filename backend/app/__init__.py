"""Public application factory for ProjectTown, loaded without side effects."""

__all__ = ["Settings", "create_app"]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from .config import Settings
    from .main import create_app

    return {"Settings": Settings, "create_app": create_app}[name]
