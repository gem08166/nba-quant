from __future__ import annotations

import json
import sqlite3


def main() -> None:
    conn = sqlite3.connect("data/nba.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.*, g.slate_date, h.abbrev AS home, a.abbrev AS away
        FROM predictions p
        JOIN games g ON g.id = p.game_id
        JOIN teams h ON h.id = g.home_id
        JOIN teams a ON a.id = g.away_id
        WHERE g.slate_date = '2026-11-04'
        ORDER BY p.id DESC
        """
    ).fetchall()
    seen = set()
    for row in rows:
        if row["game_id"] in seen:
            continue
        seen.add(row["game_id"])
        details = json.loads(row["details"])
        print(f"{row['away']} @ {row['home']}  model {row['pred_margin']:+.1f} / {row['pred_total']:.1f}  lines {details['lines']}")
        for edge in details.get("edges", []):
            print(
                f"   {edge['market']:9s} {edge['side']:5s} line={edge['line']} price={edge['price']:.2f} "
                f"p={edge['model_prob']:.3f} fair={edge['fair_prob']:.3f} edge={edge['edge']:+.3f} ev={edge['ev']:+.3f} signal={edge['signal']}"
            )


if __name__ == "__main__":
    main()
