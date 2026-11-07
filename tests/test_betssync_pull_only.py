"""pull_only 模式：只拉不推（本机看板）。回归测试。"""
import json
import sqlite3

from app.jobs import betssync


def _bet(ref, result="open", profit=None, game="g1", line=-2.5):
    return {
        "game_id": game,
        "market": "spread",
        "side": "home",
        "line": line,
        "price": 1.95,
        "stake": 1.0,
        "model_prob": 0.55,
        "result": result,
        "profit": profit,
        "ref": ref,
        "placed_at": "2026-11-06T10:00:00+00:00",
    }


class _FakeDav:
    """最小 WebDAV 假件：inbox 一条手机注单，ledger 一条云端注单。"""

    def __init__(self, monkeypatch):
        self.inbox_rows = [_bet("local-phone-1", line=-1.5)]
        self.ledger_rows = [_bet("srv-99", result="won", profit=0.95, line=-2.5)]
        self.put_calls = []
        self.list_calls = []
        self.move_calls = []
        self.ensure_calls = 0
        monkeypatch.setattr(betssync.webdav, "enabled", lambda: True)
        monkeypatch.setattr(betssync.webdav, "ensure_folders", self._ensure)
        monkeypatch.setattr(betssync.webdav, "list_files", self._list)
        monkeypatch.setattr(betssync.webdav, "get_text", self._get)
        monkeypatch.setattr(betssync.webdav, "put_text", self._put)
        monkeypatch.setattr(betssync.webdav, "move", self._move)

    def _ensure(self):
        self.ensure_calls += 1

    def _list(self, path):
        self.list_calls.append(path)
        if path == betssync.webdav.INBOX:
            return [f"{betssync.webdav.INBOX}/p1.json"]
        return []

    def _get(self, path):
        if path == betssync.webdav.LEDGER:
            return json.dumps(self.ledger_rows)
        if path.endswith("p1.json"):
            return json.dumps(self.inbox_rows)
        return None

    def _put(self, path, body):
        self.put_calls.append(path)
        return True

    def _move(self, src, dest):
        self.move_calls.append((src, dest))
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


def test_pull_only_reads_inbox_and_ledger_but_writes_nothing(monkeypatch):
    fake = _FakeDav(monkeypatch)
    conn = _conn()

    stats = betssync.run(conn, pull_only=True)

    # 手机 inbox + 云端台账都合进本地
    assert stats["inbox_merged"] == 1
    assert stats["ledger_merged"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 2

    # 关键：pull_only 绝不写 WebDAV
    assert fake.put_calls == []
    assert fake.move_calls == []       # 不把手机文件挪走，留给 CI
    assert fake.ensure_calls == 0      # 不建目录
    assert stats.get("pushed") is False


def test_full_mode_moves_inbox_and_pushes(monkeypatch):
    fake = _FakeDav(monkeypatch)
    conn = _conn()
    # 本地先有一条，确保 push 有内容
    betssync.merge_rows(conn, [_bet("srv-1", result="won", profit=0.9, line=-3.5)])

    stats = betssync.run(conn, force_push=True)

    assert stats["inbox_merged"] == 1
    assert len(fake.move_calls) == 1   # 正常模式才把 inbox 文件挪到 done/
    assert stats["pushed"] is True
    assert betssync.webdav.LEDGER in fake.put_calls
