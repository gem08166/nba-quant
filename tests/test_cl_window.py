"""捕捉窗口判定（cl_window）测试。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def _make_db(tmp_path, rows):
    path = tmp_path / "nba.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE games (slate_date TEXT, tipoff TEXT, status TEXT)"
    )
    conn.executemany(
        "INSERT INTO games (slate_date, tipoff, status) VALUES (?,?,?)", rows
    )
    conn.commit()
    conn.close()
    return path


SLATE = "2099-01-01"


def test_no_games(tmp_path):
    from scripts import cl_window

    db = _make_db(tmp_path, [])
    state, detail = cl_window.window_state(slate=SLATE, db_path=db)

    assert state == "no_games"
    assert "no games" in detail


def test_all_games_final(tmp_path):
    from scripts import cl_window

    past = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    db = _make_db(
        tmp_path,
        [
            (SLATE, past, "STATUS_FINAL"),
            (SLATE, past, "STATUS_FINAL"),
        ],
    )
    state, _ = cl_window.window_state(slate=SLATE, db_path=db)

    assert state == "all_final"


def test_before_window(tmp_path):
    from scripts import cl_window

    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    db = _make_db(tmp_path, [(SLATE, soon, "STATUS_SCHEDULED")])

    state, _ = cl_window.window_state(slate=SLATE, db_path=db)

    assert state == "before"


def test_active_window(tmp_path):
    from scripts import cl_window

    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    db = _make_db(tmp_path, [(SLATE, soon, "STATUS_SCHEDULED")])

    state, detail = cl_window.window_state(slate=SLATE, db_path=db)

    assert state == "active"
    assert "window" in detail


def test_after_window(tmp_path):
    from scripts import cl_window

    past = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    db = _make_db(tmp_path, [(SLATE, past, "STATUS_IN_PROGRESS")])

    state, _ = cl_window.window_state(slate=SLATE, db_path=db)

    assert state == "after"


def test_unparsable_tipoff(tmp_path):
    from scripts import cl_window

    db = _make_db(tmp_path, [(SLATE, "not-a-time", "STATUS_SCHEDULED")])

    state, _ = cl_window.window_state(slate=SLATE, db_path=db)

    assert state == "no_tipoffs"


def test_postponed_games_are_ignored(tmp_path):
    from scripts import cl_window

    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    db = _make_db(
        tmp_path,
        [
            (SLATE, soon, "STATUS_POSTPONED"),
            (SLATE, soon, "STATUS_CANCELED"),
        ],
    )

    state, _ = cl_window.window_state(slate=SLATE, db_path=db)

    assert state == "no_games"


def test_missing_db_is_safe(tmp_path):
    from scripts import cl_window

    state, detail = cl_window.window_state(slate=SLATE, db_path=tmp_path / "nope.db")

    assert state == "no_games"
    assert "db unavailable" in detail
