"""
core/persistence.py

Remembers which .duckdb files the user has imported, so they're still
there the next time the app opens - no re-importing every session.

Storage: a small JSON file (just a list of absolute paths) in the
OS-standard per-app data directory:
  - Windows: %APPDATA%\\<Org>\\<App>\\sessions.json
  - macOS:   ~/Library/Application Support/<App>/sessions.json
  - Linux:   ~/.local/share/<App>/sessions.json

Uses Qt's QStandardPaths rather than a new dependency (platformdirs
etc.) since PySide6 is already a hard requirement - it resolves the
right directory per OS automatically, respecting
QApplication.setOrganizationName/setApplicationName (set in main.py).

Deliberately dumb: no database, no locking, no schema - just a JSON
list. If a remembered file has been moved or deleted, the caller
(MainWindow) is expected to drop it from the list silently rather
than showing an error, per the "just remove the entry" behaviour
this exists to support.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from PySide6.QtCore import QStandardPaths

_STORE_FILENAME = "sessions.json"


def _store_path() -> Path:
    data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not data_dir:
        # Extremely unlikely (no home dir resolvable at all), but fall
        # back to cwd rather than crash on startup over a nice-to-have.
        data_dir = "."
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / _STORE_FILENAME


def load_paths() -> List[str]:
    """Returns the remembered list of .duckdb paths, most-recently-added
    last. Never raises - a missing or corrupt store just means "no
    history yet"."""
    path = _store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(p) for p in data]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_paths(paths: List[str]) -> None:
    """Overwrites the store with exactly this list (order preserved,
    duplicates removed). Best-effort: a write failure (e.g. read-only
    filesystem) is swallowed rather than surfaced, since losing import
    history isn't worth interrupting the user over."""
    seen = set()
    deduped = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    try:
        _store_path().write_text(json.dumps(deduped, indent=2), encoding="utf-8")
    except OSError:
        pass
