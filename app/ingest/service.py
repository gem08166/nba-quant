from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone

from .. import db
from ..config import REQUEST_SLEEP
from . import espn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_team(conn: sqlite3.Connection, team: dict) -> int:
    conn.execute(
        """
        INSERT INTO teams(id, abbrev, name, logo) VALUES(?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            abbrev=excluded.abbrev,
            name=excluded.name,
            logo=excluded.logo
        """,
        (team["id"], team["abbrev"], team["name"], team.get("logo")),
    )
    return team["id"]


def upsert_game(conn: sqlite3.Connection, game: dict) -> None:
    conn.execute(
        """
        INSERT INTO games(
            id, season, season_type, slate_date, tipoff, home_id, away_id,
            home_score, away_score, status, status_detail, venue, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            season=excluded.season,
            season_type=excluded.season_type,
            slate_date=excluded.slate_date,
            tipoff=excluded.tipoff,
            home_id=excluded.home_id,
            away_id=excluded.away_id,
            home_score=COALESCE(excluded.home_score, games.home_score),
            away_score=COALESCE(excluded.away_score, games.away_score),
            status=excluded.status,
            status_detail=excluded.status_detail,
            venue=excluded.venue,
            updated_at=excluded.updated_at
        """,
        (
            game["id"],
            game["season"],
            game["season_type"],
            game["slate_date"],
            game["tipoff"],
            game["home"]["id"],
            game["away"]["id"],
            game["home_score"],
            game["away_score"],
            game["status"],
            game["status_detail"],
            game["venue"],
            _now(),
        ),
    )


def insert_odds(conn: sqlite3.Connection, game_id: str, rows: list[dict], captured_at: str | None = None) -> int:
    captured_at = captured_at or _now()
    inserted = 0
    for row in rows:
        if row.get("price") is None and row.get("line") is None:
            continue
        last = conn.execute(
            """
            SELECT line, price FROM odds_snapshots
            WHERE game_id=? AND book=? AND market=? AND side=?
            ORDER BY id DESC LIMIT 1
            """,
            (game_id, row["book"], row["market"], row["side"]),
        ).fetchone()
        same_line = last is not None and last["line"] == row.get("line")
        same_price = last is not None and last["price"] == row.get("price")
        if same_line and same_price:
            continue
        conn.execute(
            """
            INSERT INTO odds_snapshots(game_id, book, market, side, line, price, captured_at, source)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                row["book"],
                row["market"],
                row["side"],
                row.get("line"),
                row.get("price"),
                captured_at,
                row.get("source", "espn"),
            ),
        )
        inserted += 1
    return inserted


def insert_injuries(conn: sqlite3.Connection, game_id: str, rows: list[dict], captured_at: str | None = None) -> int:
    captured_at = captured_at or _now()
    inserted = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO injuries(game_id, team_id, player, status, detail, impact, captured_at)
            VALUES(?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(game_id, team_id, player, status) DO UPDATE SET
                detail=excluded.detail,
                captured_at=excluded.captured_at
            """,
            (game_id, row["team_id"], row["player"], row.get("status"), row.get("detail"), captured_at),
        )
        inserted += 1
    return inserted


def refresh_game_odds(conn: sqlite3.Connection, game_id: str, slate_date: str) -> int:
    """Re-fetch one game's odds (used for closing-line capture)."""
    payload = espn.fetch_scoreboard(slate_date)
    espn.save_raw(slate_date, "scoreboard", payload)
    inserted = 0
    for event in payload.get("events") or []:
        if str(event.get("id")) != str(game_id):
            continue
        game = espn.parse_event(event, slate_date)
        upsert_team(conn, game["home"])
        upsert_team(conn, game["away"])
        upsert_game(conn, game)
        inserted = insert_odds(conn, game["id"], game["odds"])
        break
    return inserted


def upsert_player(conn: sqlite3.Connection, player: dict) -> None:
    conn.execute(
        """
        INSERT INTO players(id, name, team_id, updated_at) VALUES(?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            team_id=COALESCE(excluded.team_id, players.team_id),
            updated_at=excluded.updated_at
        """,
        (player["id"], player["name"], player.get("team_id"), _now()),
    )


def insert_pbp(
    conn: sqlite3.Connection,
    game_id: str,
    players: list[dict],
    starters: dict[int, list[int]],
    plays: list[dict],
) -> int:
    """Store one game's PBP (players, starters, plays). Idempotent per (game, seq)."""
    for player in players:
        upsert_player(conn, player)
    for team_id, player_ids in starters.items():
        for player_id in player_ids:
            conn.execute(
                "INSERT OR IGNORE INTO starters(game_id, team_id, player_id) VALUES(?, ?, ?)",
                (game_id, team_id, player_id),
            )
    for row in plays:
        conn.execute(
            """
            INSERT INTO pbp_plays(
                game_id, seq, period, clock, play_type, team_id,
                player_in, player_out, home_score, away_score
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id, seq) DO UPDATE SET
                play_type=excluded.play_type,
                player_in=excluded.player_in,
                player_out=excluded.player_out,
                home_score=excluded.home_score,
                away_score=excluded.away_score
            """,
            (
                game_id,
                row["seq"],
                row["period"],
                row["clock"],
                row["play_type"],
                row["team_id"],
                row["player_in"],
                row["player_out"],
                row["home_score"],
                row["away_score"],
            ),
        )
    return len(plays)


def load_game_pbp(conn: sqlite3.Connection, game_id: str) -> tuple[dict[int, list[int]], list[dict]]:
    starters_rows = conn.execute(
        "SELECT team_id, player_id FROM starters WHERE game_id=? ORDER BY team_id", (game_id,)
    ).fetchall()
    starters: dict[int, list[int]] = {}
    for row in starters_rows:
        starters.setdefault(int(row["team_id"]), []).append(int(row["player_id"]))
    plays = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM pbp_plays WHERE game_id=? ORDER BY seq", (game_id,)
        )
    ]
    return starters, plays


def ingest_game_pbp(conn: sqlite3.Connection, game_id: str, slate_date: str, save_raw: bool = True) -> dict:
    """Fetch one game's summary and store players/starters/plays for RAPM."""
    summary = espn.fetch_summary(game_id)
    if save_raw:
        espn.save_raw(slate_date, f"summary_{game_id}", summary)

    game = conn.execute(
        "SELECT home_id, away_id FROM games WHERE id=?", (game_id,)
    ).fetchone()
    if game is None:
        raise ValueError(f"game {game_id} not in DB; ingest the slate first")

    players = espn.parse_players(summary)
    starters = espn.parse_starters(summary, int(game["home_id"]), int(game["away_id"]))
    plays = espn.parse_plays(summary)
    insert_pbp(conn, game_id, players, starters, plays)
    return {"game_id": game_id, "players": len(players), "plays": len(plays)}


def backfill_pbp(start: str, end: str, fresh: bool = False, progress: bool = True) -> dict:
    """Fetch PBP for all finals in [start, end]; skips games that already have it."""
    conn = db.connect()
    stats = {"fetched": 0, "skipped": 0, "errors": 0}
    try:
        games = conn.execute(
            """
            SELECT id, slate_date FROM games
            WHERE slate_date BETWEEN ? AND ?
              AND status='STATUS_FINAL'
              AND home_score IS NOT NULL AND away_score IS NOT NULL
            ORDER BY slate_date
            """,
            (start, end),
        ).fetchall()
        have = {row["game_id"] for row in conn.execute("SELECT DISTINCT game_id FROM pbp_plays")}
        todo = [g for g in games if fresh or g["id"] not in have]
        stats["skipped"] = len(games) - len(todo)
        if progress:
            print(f"[pbp] {start}..{end}: finals={len(games)} cached={stats['skipped']} to fetch={len(todo)}")
        for i, game in enumerate(todo, 1):
            try:
                ingest_game_pbp(conn, game["id"], game["slate_date"], save_raw=True)
                conn.commit()
                stats["fetched"] += 1
            except Exception as exc:  # noqa: BLE001 - keep going on single-game failures
                stats["errors"] += 1
                if progress:
                    print(f"[pbp] ERROR {game['id']}: {type(exc).__name__}: {exc}")
                conn.rollback()
            time.sleep(REQUEST_SLEEP)
            if progress and (i % 25 == 0 or i == len(todo)):
                print(f"[pbp] {i}/{len(todo)}")
    finally:
        conn.close()
    return stats


def ingest_date(slate_date: str, with_injuries: bool = True) -> dict:
    """Fetch one ESPN slate (US date) and store games, odds and injuries."""
    payload = espn.fetch_scoreboard(slate_date)
    espn.save_raw(slate_date, "scoreboard", payload)
    events = payload.get("events") or []

    stats = {"date": slate_date, "games": 0, "odds": 0, "injuries": 0, "errors": 0}
    conn = db.connect()
    try:
        for event in events:
            try:
                game = espn.parse_event(event, slate_date)
            except Exception:
                stats["errors"] += 1
                continue

            upsert_team(conn, game["home"])
            upsert_team(conn, game["away"])
            upsert_game(conn, game)
            stats["games"] += 1
            stats["odds"] += insert_odds(conn, game["id"], game["odds"])

            if with_injuries and game["status"] != "STATUS_FINAL":
                try:
                    injuries = espn.ingest_summary_injuries(game["id"], slate_date)
                    stats["injuries"] += insert_injuries(conn, game["id"], injuries)
                except Exception:
                    stats["errors"] += 1
        conn.commit()
    finally:
        conn.close()
    return stats
