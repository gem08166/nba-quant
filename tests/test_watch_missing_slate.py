"""watch 兜底：有注单但 games 缺行的比赛日，按 placed_at 推断日期补 ingest。

背景：CI 某天持久化失败会导致整个 slate 缺失（如 2026-11-05），指向该日
比赛的注单在台账里永远显示裸 game_id。pending-day 扫描只看已入库的比赛
（JOIN games），对这种「注单存在、比赛行缺失」的情况无能为力。
"""
from app.ingest import service
from app.jobs import watch as watch_job


def test_missing_slate_days_detected_and_sorted():
    class _FakeConn:
        def execute(self, sql, params=()):
            return self

        def fetchall(self):
            return [
                {"slate_date": "2026-11-06"},
                {"slate_date": "2026-11-05"},
                {"slate_date": None},
            ]

    days = watch_job._missing_slate_days(_FakeConn())
    assert days == ["2026-11-05", "2026-11-06"]


def test_repair_missing_slates_calls_ingest(monkeypatch):
    calls = []

    def fake_ingest(conn, day):
        calls.append(day)
        return {"date": day, "games": 2}

    monkeypatch.setattr(watch_job, "_missing_slate_days", lambda conn: ["2026-11-05"])
    monkeypatch.setattr(service, "ingest_date", fake_ingest)
    watch_job._repair_missing_slates(object(), ingest=fake_ingest)
    assert calls == ["2026-11-05"]
