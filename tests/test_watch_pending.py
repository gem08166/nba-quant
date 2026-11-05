"""watch 僵尸未结算兜底：SQL 语义验证（有 open 注单的历史「进行中」日会被选中）。"""
import sqlite3


def test_pending_day_query_semantics():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE games (id TEXT PRIMARY KEY, slate_date TEXT, status TEXT);
        CREATE TABLE bets (id INTEGER PRIMARY KEY, game_id TEXT, result TEXT);
        INSERT INTO games VALUES ('g1','2026-11-04','STATUS_FINAL');
        INSERT INTO games VALUES ('g2','2026-11-04','STATUS_IN_PROGRESS');
        INSERT INTO games VALUES ('g3','2026-11-05','STATUS_SCHEDULED');
        INSERT INTO games VALUES ('g4','2026-11-03','STATUS_IN_PROGRESS');
        INSERT INTO bets VALUES (1,'g1','open');   -- g1 已完赛未结算：结算路径能处理，不在此列
        INSERT INTO bets VALUES (2,'g2','open');   -- g2 进行中（历史日）→ 应选中 11-04
        INSERT INTO bets VALUES (3,'g3','open');   -- 当日 → 排除（slate_date < ? 条件）
        INSERT INTO bets VALUES (4,'g1','won');
        INSERT INTO bets VALUES (5,'g4','open');   -- 前天进行中 → 应选中 11-03
        """
    )
    pending = [
        r["slate_date"]
        for r in conn.execute(
            """
            SELECT DISTINCT g.slate_date FROM bets b
            JOIN games g ON g.id = b.game_id
            WHERE b.result = 'open'
              AND g.slate_date < ?
              AND g.status != 'STATUS_FINAL'
            """,
            ("2026-11-05",),
        )
    ]
    assert sorted(pending) == ["2026-11-03", "2026-11-04"]
