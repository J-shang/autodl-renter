"""Disposable source checkout: all default private files stay in .local/."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def user_home() -> Path:
    override = os.environ.get("AUTODL_RENT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT / ".local"


def default_auth_file() -> Path:
    return user_home() / "auth.json"
