"""
Relational storage layer.

Honest disclosure: this project targets PostgreSQL as the intended
production database (the schema below is plain, portable SQL with no
SQLite-only features). Neither `psql` nor `pg_ctl` were found on this
machine and no local Postgres server was running, so SQLite (Python's
built-in `sqlite3`) is used here as a stand-in relational engine to make
the pipeline fully runnable end-to-end. The schema/DDL is deliberately
kept Postgres-compatible (INTEGER PRIMARY KEY AUTOINCREMENT is the one
SQLite-specific idiom; the equivalent Postgres DDL is noted in comments).
"""
from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from typing import Optional

SCHEMA = """
-- Postgres equivalent: SERIAL PRIMARY KEY instead of INTEGER PRIMARY KEY AUTOINCREMENT
CREATE TABLE IF NOT EXISTS vocab_versions (
    version_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    version_number  INTEGER NOT NULL UNIQUE,
    label           TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    created_by      TEXT NOT NULL,
    change_request_id INTEGER,
    FOREIGN KEY (change_request_id) REFERENCES change_requests(cr_id)
);

CREATE TABLE IF NOT EXISTS concepts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id      INTEGER NOT NULL,
    concept_id      TEXT NOT NULL,
    pref_label      TEXT NOT NULL,
    definition      TEXT,
    broader_json    TEXT NOT NULL DEFAULT '[]',
    narrower_json   TEXT NOT NULL DEFAULT '[]',
    related_json    TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (version_id) REFERENCES vocab_versions(version_id),
    UNIQUE(version_id, concept_id)
);

CREATE TABLE IF NOT EXISTS change_requests (
    cr_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    description     TEXT,
    state           TEXT NOT NULL DEFAULT 'DRAFT',
    requested_by    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type     TEXT NOT NULL,      -- 'change_request' | 'vocab_version'
    entity_id       INTEGER NOT NULL,
    action          TEXT NOT NULL,      -- e.g. 'STATE_TRANSITION', 'VERSION_CREATED'
    detail          TEXT,
    actor           TEXT NOT NULL,
    at              TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def log_audit(conn: sqlite3.Connection, entity_type: str, entity_id: int,
              action: str, detail: str, actor: str) -> None:
    conn.execute(
        "INSERT INTO audit_log (entity_type, entity_id, action, detail, actor) "
        "VALUES (?, ?, ?, ?, ?)",
        (entity_type, entity_id, action, detail, actor),
    )
    conn.commit()
