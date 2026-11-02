"""Locate a consistent PostgreSQL 17+ toolchain on Linux and macOS."""

import os
import re
import shutil
import subprocess
from pathlib import Path


def postgres_tool(name):
    configured = os.environ.get("PG_BIN")
    if configured:
        candidates = [Path(configured) / name]
    else:
        candidates = [Path(path) for path in [shutil.which(name)] if path]
        for base, pattern in (
            ("/opt/homebrew/opt", "postgresql@*/bin"),
            ("/usr/local/opt", "postgresql@*/bin"),
            ("/usr/lib/postgresql", "*/bin"),
        ):
            candidates.extend(directory / name for directory in Path(base).glob(pattern))
    versions = []
    for path in candidates:
        if path.is_file():
            output = subprocess.check_output([str(path), "--version"], text=True)
            major = int(re.search(r"\b(\d+)\.", output)[1])
            if major >= 17:
                versions.append((major, str(path)))
    if not versions:
        raise RuntimeError(
            f"Install PostgreSQL 17+ tools ({name}); add them to PATH or set PG_BIN. See docs/OPERATIONS.md"
        )
    return max(versions)[1]
