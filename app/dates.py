from __future__ import annotations

from datetime import datetime, timedelta, timezone


def current_slate_date() -> str:
    """The NBA slate currently in progress / upcoming next.

    ESPN keys slates by US Eastern date; tip-offs run from ~00:00Z to ~03:30Z
    of the following UTC day. UTC-5 approximates Eastern time closely enough
    for picking the active slate (DST shifts this by an hour, which only
    matters in the early afternoon when no games are live anyway).
    """
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date().isoformat()
