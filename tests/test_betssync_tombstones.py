"""删除墓碑：跨设备同步「注单已被删」，防止云端台账把删掉的注单合并回来。"""
import json
import sqlite3

from app.jobs import betssync


def _bet(ref, result="won", profit=0.95, line=-2.5):
    return {
        "game_id": "g1",
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
        CREATE UNIQUE INDEX idx_bets_ref ON bets(ref) WHERE ref IS NOT NULL;
        """
    )
    return conn


def _seed(conn):
    betssync.merge_rows(
        conn,
        [_bet("srv-1"), _bet("srv-2", result="lost", profit=-1.0, line=-4.5)],
    )


def test_apply_deletions_removes_by_ref():
    conn = _conn()
    _seed(conn)
    assert betssync.apply_deletions(conn, {"srv-1"}) == 1
    refs = {r["ref"] for r in conn.execute("SELECT ref FROM bets")}
    assert refs == {"srv-2"}
    # 幂等
    assert betssync.apply_deletions(conn, {"srv-1"}) == 0


class _FakeDav:
    def __init__(self, monkeypatch, deleted_refs):
        self.deleted = list(deleted_refs)
        self.writes = {}
        monkeypatch.setattr(betssync.webdav, "enabled", lambda: True)
        monkeypatch.setattr(betssync.webdav, "ensure_folders", lambda: None)
        monkeypatch.setattr(betssync.webdav, "list_files", lambda path: [])
        monkeypatch.setattr(betssync.webdav, "get_text", self._get)
        monkeypatch.setattr(betssync.webdav, "put_text", self._put)
        monkeypatch.setattr(betssync.webdav, "move", lambda a, b: True)

    def _get(self, path):
        if path == betssync.webdav.LEDGER:
            return json.dumps(
                [_bet("srv-1"), _bet("srv-2", result="lost", profit=-1.0, line=-4.5)]
            )
        if path == betssync.webdav.DELETED:
            return json.dumps({"schema": "nbaq.bets.deleted/1", "refs": self.deleted})
        return None

    def _put(self, path, body):
        self.writes[path] = body
        return True

    def push_digest_reset(self):
        betssync._save_state({})


def test_run_applies_tombstone_after_merging(monkeypatch):
    # 云端台账还带着 srv-1，但墓碑说它被删了 → 最终不能留在本地
    fake = _FakeDav(monkeypatch, ["srv-1"])
    fake.push_digest_reset()
    conn = _conn()

    stats = betssync.run(conn, pull_only=True)

    assert stats["ledger_merged"] == 2
    assert stats["deleted"] == 1
    refs = {r["ref"] for r in conn.execute("SELECT ref FROM bets")}
    assert refs == {"srv-2"}


def test_record_deletions_unions_and_persists(monkeypatch):
    fake = _FakeDav(monkeypatch, ["srv-old"])
    total = betssync.record_deletions(["srv-1", "srv-1", "", "srv-2"])
    assert total == 3  # srv-old + srv-1 + srv-2
    body = json.loads(fake.writes[betssync.webdav.DELETED])
    assert body["schema"] == "nbaq.bets.deleted/1"
    assert set(body["refs"]) == {"srv-old", "srv-1", "srv-2"}
    assert body["updated_at"]
