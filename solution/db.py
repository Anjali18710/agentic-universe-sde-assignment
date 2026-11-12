"""Database connection and small helpers."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def connect(db_dir: Path) -> sqlite3.Connection:
    db_dir.mkdir(parents=True, exist_ok=True)
    gitignore = db_dir / ".gitignore"
    if not gitignore.exists():  # never let the database or key file be committed by accident
        gitignore.write_text("*\n")
    conn = sqlite3.connect(db_dir / "ledger.db", isolation_level=None)  # we manage transactions
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_SQL.read_text())
    return conn


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class FileChangedError(RuntimeError):
    pass


def already_ingested(conn, kind: str, workflow_id: str, path: Path, digest: str) -> bool:
    """True if this exact file was ingested before. Same name + different content is an error:
    silently re-processing it could double-count rows, silently skipping it could lose data."""
    row = conn.execute(
        "SELECT sha256 FROM ingested_files WHERE kind=? AND workflow_id=? AND file_name=?",
        (kind, workflow_id, path.name)).fetchone()
    if row is None:
        return False
    if row[0] != digest:
        raise FileChangedError(
            f"{path} was already ingested with different contents; refusing to re-ingest it")
    return True


def register_file(conn, kind: str, workflow_id: str, path: Path, digest: str) -> int:
    cur = conn.execute(
        "INSERT INTO ingested_files (kind, workflow_id, file_name, sha256, source_path, ingested_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (kind, workflow_id, path.name, digest, str(path),
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return cur.lastrowid
