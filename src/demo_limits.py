"""
Usage caps for the public demo.

Two layers, because each covers a gap in the other:
  - a PER-SESSION cap keeps one visitor from monopolising the demo, but it
    lives in Streamlit's session state and resets on page refresh, so it is
    not a real spending control;
  - a GLOBAL DAILY cap is the actual budget guard. It lives in a small JSON
    file so it survives refreshes and new sessions.

The daily counter file is best-effort: the deployed filesystem is ephemeral,
so an app restart resets the count, and two simultaneous requests can race on
the read-modify-write (last write wins). Both are acceptable for a soft cap on
a portfolio demo -- the goal is preventing casual overuse of a small prepaid
API balance, not airtight accounting.

Disabled unless DEMO_LIMITS_ENABLED=true, so local development is unaffected.
"""

import json
import os
import tempfile
from datetime import date
from pathlib import Path

ENABLED = os.getenv("DEMO_LIMITS_ENABLED", "false").lower() == "true"
SESSION_LIMIT = int(os.getenv("DEMO_SESSION_LIMIT", "5"))
DAILY_LIMIT = int(os.getenv("DEMO_DAILY_LIMIT", "120"))

# /tmp rather than the project directory: writable on every host, and nothing
# here is worth persisting across deploys.
_COUNTER_PATH = Path(tempfile.gettempdir()) / "nl2sql_demo_usage.json"


def _read_daily_count() -> int:
    """Today's count, or 0 if the file is missing, stale, or unreadable."""
    try:
        data = json.loads(_COUNTER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if data.get("date") != date.today().isoformat():
        return 0  # yesterday's count -- treat as a fresh day
    return int(data.get("count", 0))


def _write_daily_count(count: int) -> None:
    try:
        _COUNTER_PATH.write_text(
            json.dumps({"date": date.today().isoformat(), "count": count}),
            encoding="utf-8",
        )
    except Exception:
        pass  # a failed write must never block a query


def check(session_used: int) -> tuple:
    """
    Returns (allowed, reason, session_remaining, daily_remaining).
    `reason` is None when allowed, otherwise a message for the user.
    """
    if not ENABLED:
        return True, None, None, None

    daily_used = _read_daily_count()
    session_left = max(0, SESSION_LIMIT - session_used)
    daily_left = max(0, DAILY_LIMIT - daily_used)

    if daily_left <= 0:
        return (
            False,
            "The demo has hit its daily query limit. It resets tomorrow — "
            "or clone the repo and run it with your own API key.",
            session_left,
            0,
        )
    if session_left <= 0:
        return (
            False,
            "You've used this session's %d queries. Refresh to start a new "
            "session, or clone the repo to run it without limits." % SESSION_LIMIT,
            0,
            daily_left,
        )
    return True, None, session_left, daily_left


def record() -> None:
    """Increment the global daily counter. Call once per completed query."""
    if ENABLED:
        _write_daily_count(_read_daily_count() + 1)