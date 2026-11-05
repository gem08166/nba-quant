"""过时信号：开赛/完赛后或超龄未更新时自动过期。"""
from datetime import datetime, timedelta, timezone

import app.api as api


def _row(status="STATUS_SCHEDULED", tipoff_hours=5):
    tip = (datetime.now(timezone.utc) + timedelta(hours=tipoff_hours)).isoformat(timespec="seconds")
    return {
        "id": "g1",
        "tipoff": tip,
        "status": status,
        "status_detail": "",
        "season": "2026-27",
        "home_id": 1,
        "home_abbrev": "LAL",
        "home_name": "Los Angeles Lakers",
        "away_id": 2,
        "away_abbrev": "BOS",
        "away_name": "Boston Celtics",
        "home_score": None,
        "away_score": None,
        "live_home_score": None,
        "live_away_score": None,
    }


def _prediction(created_hours_ago=1, signal=True, bettable=True):
    created = (datetime.now(timezone.utc) - timedelta(hours=created_hours_ago)).isoformat(timespec="seconds")
    return {
        "pred_margin": 3.0,
        "sigma_margin": 11.0,
        "pred_total": 220.0,
        "sigma_total": 15.0,
        "home_win_prob": 0.55,
        "created_at": created,
        "details": {
            "edges": [
                {
                    "market": "spread",
                    "side": "home",
                    "line": -2.5,
                    "price": 1.95,
                    "ev": 0.08,
                    "edge": 0.06,
                    "signal": signal,
                    "bettable": bettable,
                    "created_at": created,
                }
            ]
        },
    }


def test_signal_expires_when_game_started():
    payload = api._game_payload(_row(status="STATUS_IN_PROGRESS", tipoff_hours=-1), _prediction())
    edge = payload["edges"][0]
    assert edge["expired"] is True
    assert edge["signal"] is False
    assert edge["bettable"] is False
    assert "比赛已开始" in edge["expired_reason"]


def test_signal_expires_when_game_final():
    payload = api._game_payload(_row(status="STATUS_FINAL", tipoff_hours=-3), _prediction())
    assert payload["edges"][0]["expired"] is True


def test_fresh_pregame_signal_survives():
    payload = api._game_payload(_row(tipoff_hours=5), _prediction(created_hours_ago=0.2))
    edge = payload["edges"][0]
    assert edge.get("expired") is None
    assert edge["signal"] is True


def test_stale_signal_expires_by_age():
    payload = api._game_payload(_row(tipoff_hours=5), _prediction(created_hours_ago=13))
    edge = payload["edges"][0]
    assert edge["expired"] is True
    assert "小时" in edge["expired_reason"]
    assert edge["signal"] is False


def test_tipoff_passed_expires_even_if_status_scheduled():
    payload = api._game_payload(_row(status="STATUS_SCHEDULED", tipoff_hours=-0.5), _prediction())
    assert payload["edges"][0]["expired"] is True
