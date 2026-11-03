from __future__ import annotations

"""数据库备份：SQLite VACUUM INTO 生成一致性快照，滚动保留最近 N 份。"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import DB_PATH, ROOT

BACKUP_DIR = Path(os.getenv("NBAQ_BACKUP_DIR", ROOT / "data" / "backup"))
KEEP = int(os.getenv("NBAQ_BACKUP_KEEP", "7"))


def backup_database(keep: int | None = None) -> Path | None:
    """备份 nba.db 到 data/backup/，返回备份文件路径。失败返回 None（不抛出）。"""
    import sqlite3

    keep = keep or KEEP
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = BACKUP_DIR / f"nba-{stamp}.db"
        source = sqlite3.connect(str(DB_PATH))
        try:
            source.execute("VACUUM INTO ?", (str(target),))
        finally:
            source.close()
        _prune(keep)
        return target
    except Exception:  # noqa: BLE001 - 备份失败不能拖垮主流程
        return None


def _prune(keep: int) -> None:
    backups = sorted(
        (p for p in BACKUP_DIR.glob("nba-*.db") if re.match(r"nba-\d{8}-\d{6}\.db", p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
