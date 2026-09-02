"""Public ProjectTown application API.

The public names are loaded lazily so utility modules such as the benchmark
runner do not construct the FastAPI application merely by importing
``backend``.
"""

__all__ = ["Settings", "create_app"]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from .app import Settings, create_app

    return {"Settings": Settings, "create_app": create_app}[name]
