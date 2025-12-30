from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        PRAGMA journal_mode = WAL;

        CREATE TABLE IF NOT EXISTS documents (
            hash TEXT PRIMARY KEY,
            vault_relpath TEXT NOT NULL,
            file_size INTEGER NOT NULL,

            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,

            page_count INTEGER,
            title TEXT,
            authors TEXT,
            subject TEXT,
            keywords TEXT,
            text_sample TEXT,
            meta_json TEXT,

            category TEXT,
            category_score REAL,
            category_reason TEXT,
            categorized_at REAL
        );

        CREATE TABLE IF NOT EXISTS source_files (
            source_path TEXT PRIMARY KEY,
            source_basename TEXT,
            source_size INTEGER,
            source_mtime REAL,
            hash TEXT,

            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,

            status TEXT NOT NULL,
            error TEXT,

            FOREIGN KEY(hash) REFERENCES documents(hash)
        );

        CREATE INDEX IF NOT EXISTS idx_source_hash ON source_files(hash);
        CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);
        """
    )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def get_source(conn: sqlite3.Connection, source_path: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM source_files WHERE source_path = ?",
        (source_path,),
    ).fetchone()
    return row_to_dict(row)


def touch_source_seen(conn: sqlite3.Connection, source_path: str, *, seen_at: float) -> None:
    conn.execute(
        "UPDATE source_files SET last_seen_at = ? WHERE source_path = ?",
        (seen_at, source_path),
    )


def upsert_source(
    conn: sqlite3.Connection,
    *,
    source_path: str,
    source_basename: str | None,
    source_size: int | None,
    source_mtime: float | None,
    hash_hex: str | None,
    status: str,
    error: str | None,
    first_seen_at: float,
    last_seen_at: float,
) -> None:
    conn.execute(
        """
        INSERT INTO source_files (
            source_path, source_basename, source_size, source_mtime, hash,
            first_seen_at, last_seen_at, status, error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_path) DO UPDATE SET
            source_basename = excluded.source_basename,
            source_size = excluded.source_size,
            source_mtime = excluded.source_mtime,
            hash = excluded.hash,
            last_seen_at = excluded.last_seen_at,
            status = excluded.status,
            error = excluded.error
        """,
        (
            source_path,
            source_basename,
            source_size,
            source_mtime,
            hash_hex,
            first_seen_at,
            last_seen_at,
            status,
            error,
        ),
    )


def get_document(conn: sqlite3.Connection, hash_hex: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM documents WHERE hash = ?", (hash_hex,)).fetchone()
    return row_to_dict(row)


def upsert_document_seen(
    conn: sqlite3.Connection,
    *,
    hash_hex: str,
    vault_relpath: str,
    file_size: int,
    first_seen_at: float,
    last_seen_at: float,
) -> None:
    conn.execute(
        """
        INSERT INTO documents (hash, vault_relpath, file_size, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(hash) DO UPDATE SET
            vault_relpath = excluded.vault_relpath,
            file_size = excluded.file_size,
            last_seen_at = excluded.last_seen_at
        """,
        (hash_hex, vault_relpath, file_size, first_seen_at, last_seen_at),
    )


def update_document_metadata(
    conn: sqlite3.Connection,
    *,
    hash_hex: str,
    page_count: int | None,
    title: str | None,
    authors: str | None,
    subject: str | None,
    keywords: str | None,
    text_sample: str | None,
    meta_json: str | None,
) -> None:
    conn.execute(
        """
        UPDATE documents
        SET
            page_count = ?,
            title = ?,
            authors = ?,
            subject = ?,
            keywords = ?,
            text_sample = ?,
            meta_json = ?
        WHERE hash = ?
        """,
        (page_count, title, authors, subject, keywords, text_sample, meta_json, hash_hex),
    )


def update_document_category(
    conn: sqlite3.Connection,
    *,
    hash_hex: str,
    category: str,
    score: float,
    reason: str,
    categorized_at: float,
) -> None:
    conn.execute(
        """
        UPDATE documents
        SET category = ?, category_score = ?, category_reason = ?, categorized_at = ?
        WHERE hash = ?
        """,
        (category, score, reason, categorized_at, hash_hex),
    )


def iter_documents(
    conn: sqlite3.Connection,
    *,
    where_sql: str = "",
    params: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM documents"
    if where_sql.strip():
        sql += " WHERE " + where_sql
    sql += " ORDER BY last_seen_at DESC"
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def get_latest_source_for_hash(conn: sqlite3.Connection, hash_hex: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM source_files
        WHERE hash = ?
        ORDER BY last_seen_at DESC
        LIMIT 1
        """,
        (hash_hex,),
    ).fetchone()
    return row_to_dict(row)


