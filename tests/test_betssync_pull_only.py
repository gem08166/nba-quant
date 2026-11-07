"""pull_only 模式：只拉不推。回归测试。"""
import sqlite3

from app.jobs import betssync


class _FakeDav:
    """最小 WebDAV 假件：ledger 有一条云端注单，验证 pull_only 时不回推。"""

    def __init__(self, monkeypatch):
        self.ledger_rows = [
            {
                "game_id": "g1",
                "market": "spread",
                "side": "home",
                "line": -2.5,
                "price": 1.95,
                "stake": 1.0,
                "result": "won",
                "profit": 0.95,
                "ref": "srv-99",
                "placed_at": "2026-11-06T10:00:00+00:00",
            }
        ]
        self.put_calls = []
        self.list_calls = []
        self.ensure_calls = 0
        monkeypatch.setattr(betssync.webdav, "enabled", lambda: True)
        monkeypatch.setattr(betssync.webdav, "ensure_folders", self._ensure)
        monkeypatch.setattr(betssync.webdav, "list_files", self._list)
        monkeypatch.setattr(betssync.webdav, "get_text", lambda path: None)
        monkeypatch.setattr(
            betssync.webdav, "put_text", self._put
        )

    def _ensure(self):
        self.ensure_calls += 1

    def _list(self, path):
        self.list_calls.append(path)
        return []

    def _put(self, path, body):
        self.put_calls.append(path)
        return True
    def _put(self, path, body):
        self.put_calls.append(path)
        return True


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, side TEXT, line REAL,
            price REAL, stake REAL, model_prob REAL, ev REAL, placed_at TEXT,
            result TEXT, profit REAL, closing_line REAL, closing_price REAL,
            clv REAL, clv_line REAL, note TEXT, ref TEXT
        );
        CREATE TABLE games (
            id TEXT PRIMARY KEY, slate_date TEXT, status TEXT,
            home_id INTEGER, away_id INTEGER,
            home_score INTEGER, away_score INTEGER, tipoff TEXT
        );
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT, name TEXT);
        INSERT INTO teams VALUES (1,'LAL','Lakers'),(2,'BOS','Celtics');
        INSERT INTO games VALUES ('g1','2026-11-06','STATUS_FINAL',2,1,100,90,NULL);
        """
    )
    return conn


def test_pull_only_merges_in_but_never_pushes(monkeypatch):
    fake = _FakeDav(monkeypatch)
    conn = _conn()

    # 云端台账 → 本地
    monkeypatch.setattr(
        betssync.webdav, "get_text", lambda path: (
            __import__("json").dumps(fake.ledger_rows) if path == betssync.webdav.LEDGER else None
        )
    )
    stats = betssync.run(conn, pull_only=True)

    assert stats["ledger_merged"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 1
    # 关键：pull_only 不回推、不碰 inbox（不会把手机的注单吃掉）、不建目录
    assert fake.put_calls == []
    assert fake.list_calls == []
    assert fake.ensure_calls == 0
    assert stats.get("pushed") is False


def test_full_mode_still_pushes(monkeypatch):
    fake = _FakeDav(monkeypatch)
    conn = _conn()
    monkeypatch.setattr(betssync.webdav, "get_text", lambda path: None)
    monkeypatch.setattr(betssync.webdav, "list_files", lambda path: [])

    stats = betssync.run(conn, force_push=True)

    assert stats["pushed"] is True
    assert betssync.webdav.LEDGER in fake.put_calls
