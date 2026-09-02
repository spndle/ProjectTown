"""Utilities for predictable title cleanup."""

import re


def slugify(title: str) -> str:
    """Return lowercase words joined by one hyphen."""
    words = re.findall(r"[a-z0-9]+", title.lower())
    return "-".join(words)
