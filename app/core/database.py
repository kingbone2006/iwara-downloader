from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import APP_DIR

DB_PATH = APP_DIR / "history.db"


class HistoryDB:
    """Track already-downloaded video IDs to skip duplicates."""

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    video_id TEXT PRIMARY KEY,
                    title TEXT,
                    author TEXT,
                    path TEXT,
                    channel TEXT DEFAULT '',
                    downloaded_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            # Migrate older DBs missing channel column
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(downloads)").fetchall()
            }
            if "channel" not in cols:
                conn.execute(
                    "ALTER TABLE downloads ADD COLUMN channel TEXT DEFAULT ''"
                )

    def is_downloaded(self, video_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM downloads WHERE video_id = ?", (video_id,)
            ).fetchone()
            return row is not None

    def get_path(self, video_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT path FROM downloads WHERE video_id = ?", (video_id,)
            ).fetchone()
            if row and row["path"]:
                return str(row["path"])
            return None

    def mark_downloaded(
        self,
        video_id: str,
        title: str,
        author: str,
        path: str,
        channel: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO downloads
                    (video_id, title, author, path, channel)
                VALUES (?, ?, ?, ?, ?)
                """,
                (video_id, title, author, path, channel or ""),
            )

    def remove(self, video_id: str) -> None:
        """Allow re-download of a previously completed/skipped id."""
        with self._connect() as conn:
            conn.execute("DELETE FROM downloads WHERE video_id = ?", (video_id,))
