import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


def backup_database(path: Path, *, lock_timeout: float = 5.0) -> bytes:
    """Return a consistent snapshot of an existing SQLite database."""
    deadline = time.monotonic() + lock_timeout

    def check_lock(status, _remaining, _total):
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) and time.monotonic() >= deadline:
            raise TimeoutError("数据库正在被占用，请稍后重试。")

    uri = path.resolve().as_uri() + "?mode=ro"
    with (
        closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as source,
        closing(sqlite3.connect(":memory:")) as snapshot,
    ):
        source.backup(snapshot, pages=256, progress=check_lock, sleep=0.05)
        data = snapshot.serialize()
        # The backup contains all committed pages, including WAL pages. SQLite's
        # serialized in-memory copy can retain the source's WAL header markers;
        # mark this standalone file as rollback-journal format (header bytes 18–19).
        if data[18:20] == b"\x02\x02":
            data = data[:18] + b"\x01\x01" + data[20:]
        return data


@dataclass(frozen=True)
class BackupInspection:
    quick_check: str
    foreign_keys: str


def inspect_backup(data: bytes) -> BackupInspection:
    """Check a snapshot independently of the source database, without changing it."""
    with closing(sqlite3.connect(":memory:")) as snapshot:
        try:
            snapshot.deserialize(data)
            quick_check = snapshot.execute("PRAGMA quick_check").fetchall()
        except sqlite3.Error:
            return BackupInspection("unavailable", "unavailable")
        if quick_check != [("ok",)]:
            return BackupInspection("failed", "unavailable")
        try:
            foreign_keys = snapshot.execute("PRAGMA foreign_key_check").fetchone()
        except sqlite3.Error:
            return BackupInspection("ok", "unavailable")
        return BackupInspection("ok", "failed" if foreign_keys else "ok")
