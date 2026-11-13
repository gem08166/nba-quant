from datetime import datetime, timedelta, timezone

from app.jobs.predict import consensus_market


def _row(book, market, side, line, price, hours_ago, last_seen_hours_ago=None):
    now = datetime.now(timezone.utc)
    captured = (now - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    seen = (
        (now - timedelta(hours=last_seen_hours_ago)).isoformat(timespec="seconds")
        if last_seen_hours_ago is not None
        else None
    )
    return {
        "book": book,
        "market": market,
        "side": side,
        "line": line,
        "price": price,
        "captured_at": captured,
        "last_seen_at": seen,
    }


def _book_rows(book, hours_ago, last_seen_hours_ago=None, line=-5.5):
    return [
        _row(book, "spread", "home", line, 1.91, hours_ago, last_seen_hours_ago),
        _row(book, "spread", "away", -line, 1.91, hours_ago, last_seen_hours_ago),
        _row(book, "total", "over", 220.5, 1.91, hours_ago, last_seen_hours_ago),
        _row(book, "total", "under", 220.5, 1.91, hours_ago, last_seen_hours_ago),
        _row(book, "moneyline", "home", None, 1.80, hours_ago, last_seen_hours_ago),
        _row(book, "moneyline", "away", None, 2.00, hours_ago, last_seen_hours_ago),
    ]


def test_last_seen_at_keeps_stable_lines_fresh():
    # captured 100h ago, but every book confirmed the same price 1h ago.
    rows = []
    for book in ("a", "b", "c"):
        rows += _book_rows(book, hours_ago=100, last_seen_hours_ago=1)
    cons = consensus_market(rows)
    assert cons["spread"]["n_books"] == 3
    assert cons["stale"] is False


def test_few_fresh_books_fall_back_to_full_consensus():
    # Four stale books plus one fresh book: old logic kept only the fresh one.
    rows = []
    for book in ("a", "b", "c", "d"):
        rows += _book_rows(book, hours_ago=100)
    rows += _book_rows("fresh", hours_ago=1)
    cons = consensus_market(rows)
    assert cons["spread"]["n_books"] == 5
    assert cons["total"]["n_books"] == 5
    assert cons["moneyline"]["n_books"] == 5
    assert cons["stale"] is True


def test_three_fresh_books_prefer_fresh_subset():
    rows = []
    for book in ("old-a", "old-b", "old-c", "old-d"):
        rows += _book_rows(book, hours_ago=100)
    for book in ("new-a", "new-b", "new-c"):
        rows += _book_rows(book, hours_ago=1)
    cons = consensus_market(rows)
    assert cons["spread"]["n_books"] == 3
    assert cons["stale"] is False


def test_mode_tie_returns_an_existing_line():
    """平票时不能平均出市场上不存在的半线（否则 at_line 为空 → fair=0 假 edge）。"""
    from app.jobs.predict import _mode

    assert _mode([6.5, 6.5, 6.75, 6.75]) in (6.5, 6.75)
    assert _mode([218.5, 218.5, 219.0, 219.0]) in (218.5, 219.0)
    assert _mode([220.0]) == 220.0
    assert _mode([]) is None
