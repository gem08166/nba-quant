"""watch 兜底：有注单但 games 缺行的比赛日，按 placed_at 推断日期补 ingest。

背景：CI 某天持久化失败会导致整个 slate 缺失（如 2026-11-05），指向该日
比赛的注单在台账里永远显示裸 game_id。pending-day 扫描只看已入库的比赛
（JOIN games），对这种「注单存在、比赛行缺失」的情况无能为力。
"""
from app.ingest import service
from app.jobs import watch as watch_job


class _FakeConn:
    """返回预置 slate 的假连接；同时记录 SQL 里是否用了 ET 折算。"""

    def __init__(self, days):
        self.days = days
        self.sql = ""

    def execute(self, sql, params=()):
        self.sql = sql
        return self

    def fetchall(self):
        return [{"slate_date": d} for d in self.days]


def test_missing_slate_days_detected_and_sorted():
    conn = _FakeConn(["2026-11-06", "2026-11-05", None])
    days = watch_job._missing_slate_days(conn)
    assert days == ["2026-11-05", "2026-11-06"]
    # 用 UTC-5 近似 ET 比赛日（凌晨 00:00-05:00Z 的注单属于前一天）
    assert "date(b.placed_at, '-5 hours')" in conn.sql


def test_repair_missing_slates_calls_ingest_with_day_only(monkeypatch):
    calls = []

    def fake_ingest(day):
        calls.append(day)
        return {"date": day, "games": 2}

    monkeypatch.setattr(watch_job, "_missing_slate_days", lambda conn: ["2026-11-05"])
    repaired = watch_job._repair_missing_slates(object(), ingest=fake_ingest)
    # ingest_date(slate_date) 只吃日期字符串，不传 conn
    assert calls == ["2026-11-05"]
    assert repaired == ["2026-11-05"]


def test_repair_missing_slates_survives_ingest_error(monkeypatch):
    def boom(day):
        raise RuntimeError("espn down")

    monkeypatch.setattr(watch_job, "_missing_slate_days", lambda conn: ["2026-11-05"])
    assert watch_job._repair_missing_slates(object(), ingest=boom) == []


def test_service_ingest_signature_takes_date_only():
    import inspect

    params = list(inspect.signature(service.ingest_date).parameters)
    assert params[0] == "slate_date"
