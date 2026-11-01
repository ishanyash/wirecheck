"""One place to read "today", so tests can freeze it.

Set WIRECHECK_TODAY=YYYY-MM-DD to pin the date (handy in CI), or monkeypatch
``wirecheck.clock.today`` in a test.
"""

from __future__ import annotations

import datetime as _dt
import os


def today() -> _dt.date:
    override = os.environ.get("WIRECHECK_TODAY", "").strip()
    if override:
        return _dt.date.fromisoformat(override)
    return _dt.datetime.now(_dt.timezone.utc).date()
