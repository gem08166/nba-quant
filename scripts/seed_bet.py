"""Insert a paper bet directly (handy for smoke-testing settlement)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from app import db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--market", required=True, choices=["spread", "total", "moneyline"])
    parser.add_argument("--side", required=True)
    parser.add_argument("--line", type=float, default=None)
    parser.add_argument("--price", type=float, required=True)
    parser.add_argument("--stake", type=float, default=1.0)
    parser.add_argument("--model-prob", type=float, default=0.5)
    parser.add_argument(
        "--closing-price",
        type=float,
        default=None,
        help="also insert a fake pre-tipoff odds snapshot to simulate a closing line",
    )
    args = parser.parse_args()

    conn = db.connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO bets(game_id, market, side, line, price, stake, model_prob, ev, placed_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                args.game_id,
                args.market,
                args.side,
                args.line,
                args.price,
                args.stake,
                args.model_prob,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        if args.closing_price is not None:
            tipoff = conn.execute(
                "SELECT tipoff FROM games WHERE id=?", (args.game_id,)
            ).fetchone()["tipoff"]
            captured = "2026-11-02T23:00:00+00:00"  # safely before any tipoff
            conn.execute(
                """
                INSERT OR IGNORE INTO odds_snapshots(
                    game_id, book, market, side, line, price, captured_at, source
                ) VALUES(?, 'simulated', ?, ?, ?, ?, ?, 'simulated')
                """,
                (args.game_id, args.market, args.side, args.line, args.closing_price, captured),
            )
            print(f"simulated closing snapshot: {args.closing_price} (tipoff {tipoff})")
        conn.commit()
        print(f"bet #{cur.lastrowid} inserted")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
