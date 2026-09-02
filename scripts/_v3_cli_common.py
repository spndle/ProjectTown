"""Shared strict CLI primitives for the early v3 command-line interfaces."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import NoReturn


class CliError(ValueError):
    """A stable command-line rejection with a caller-supplied code."""

    def __init__(self, code: str = "INVALID_ARGUMENTS") -> None:
        self.code = code


class CliParser(argparse.ArgumentParser):
    """Convert argparse syntax failures into the stable CLI error contract."""

    def error(self, _message: str) -> NoReturn:
        raise CliError()


def canonical_absolute_path(raw: str, code: str) -> Path:
    """Accept only absolute paths already written in the platform form."""

    path = Path(raw)
    if not path.is_absolute() or raw != str(path):
        raise CliError(code)
    return path
