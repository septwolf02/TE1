"""Locations of optional resources bundled in the frozen Windows application."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


def production_build() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundled_resource(*parts: str) -> Path | None:
    if not production_build():
        return None
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    path = root.joinpath("resources", *parts)
    return path if path.is_file() else None


def tool_path(name: str) -> str | None:
    if production_build():
        bundled = bundled_resource("bin", name + (".exe" if os.name == "nt" else ""))
        if bundled:
            return str(bundled)
    return shutil.which(name)
