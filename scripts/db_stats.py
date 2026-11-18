from __future__ import annotations

import sqlite3


def main() -> None:
    conn = sqlite3.connect("data/nba.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT substr(slate_date, 1, 7) AS month,
               AVG(home_score + away_score) AS total,
               AVG(home_score - away_score) AS margin,
               COUNT(*) AS n
        FROM games
        WHERE home_score IS NOT NULL AND season_type IN (2, 3)
        GROUP BY substr(slate_date, 1, 7)
        ORDER BY month
        """
    ).fetchall()
    for row in rows:
        print(row["month"], round(row["total"], 1), round(row["margin"], 2), row["n"])

    upcoming = conn.execute(
        """
        SELECT slate_date, COUNT(*) AS n FROM games
        WHERE status != 'STATUS_FINAL'
        GROUP BY slate_date ORDER BY slate_date DESC LIMIT 5
        """
    ).fetchall()
    print("\nupcoming slates:")
    for row in upcoming:
        print(" ", row["slate_date"], row["n"], "games")


if __name__ == "__main__":
    main()
