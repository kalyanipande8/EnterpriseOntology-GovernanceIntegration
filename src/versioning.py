"""
Real ontology versioning against the relational DB (see db.py).

Each call to create_version() inserts a brand new immutable snapshot of
every concept for that version_id. Nothing is ever updated or deleted, so
every previous version remains fully queryable (that is exercised directly
in tests/test_pipeline.py). diff_versions() computes a genuine structural
diff between any two stored versions (added/removed concepts, and per-field
changes to label/definition/broader/narrower/related on concepts present in
both).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List

from . import db as dbmod


@dataclass
class VersionDiff:
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    changed: Dict[str, Dict[str, tuple]] = field(default_factory=dict)  # cid -> field -> (old, new)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def create_version(conn: sqlite3.Connection, concepts: Dict[str, dict],
                    label: str, created_by: str,
                    change_request_id: int | None = None) -> int:
    cur = conn.execute("SELECT COALESCE(MAX(version_number), 0) + 1 FROM vocab_versions")
    next_num = cur.fetchone()[0]

    cur = conn.execute(
        "INSERT INTO vocab_versions (version_number, label, created_by, change_request_id) "
        "VALUES (?, ?, ?, ?)",
        (next_num, label, created_by, change_request_id),
    )
    version_id = cur.lastrowid

    for cid, c in concepts.items():
        conn.execute(
            "INSERT INTO concepts (version_id, concept_id, pref_label, definition, "
            "broader_json, narrower_json, related_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                version_id, cid, c["pref_label"], c.get("definition", ""),
                json.dumps(c.get("broader", [])),
                json.dumps(c.get("narrower", [])),
                json.dumps(c.get("related", [])),
            ),
        )
    conn.commit()
    dbmod.log_audit(conn, "vocab_version", version_id, "VERSION_CREATED",
                     f"version_number={next_num} label={label!r} concepts={len(concepts)}",
                     created_by)
    return version_id


def get_version_concepts(conn: sqlite3.Connection, version_id: int) -> Dict[str, dict]:
    rows = conn.execute(
        "SELECT concept_id, pref_label, definition, broader_json, narrower_json, related_json "
        "FROM concepts WHERE version_id = ?",
        (version_id,),
    ).fetchall()
    out = {}
    for r in rows:
        out[r["concept_id"]] = {
            "concept_id": r["concept_id"],
            "pref_label": r["pref_label"],
            "definition": r["definition"],
            "broader": json.loads(r["broader_json"]),
            "narrower": json.loads(r["narrower_json"]),
            "related": json.loads(r["related_json"]),
        }
    return out


def list_versions(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT version_id, version_number, label, created_at, created_by, change_request_id "
        "FROM vocab_versions ORDER BY version_number"
    ).fetchall()


def diff_versions(conn: sqlite3.Connection, version_id_a: int, version_id_b: int) -> VersionDiff:
    a = get_version_concepts(conn, version_id_a)
    b = get_version_concepts(conn, version_id_b)

    diff = VersionDiff()
    diff.added = sorted(set(b) - set(a))
    diff.removed = sorted(set(a) - set(b))

    for cid in sorted(set(a) & set(b)):
        field_changes = {}
        for field_name in ("pref_label", "definition", "broader", "narrower", "related"):
            old_v, new_v = a[cid].get(field_name), b[cid].get(field_name)
            if old_v != new_v:
                field_changes[field_name] = (old_v, new_v)
        if field_changes:
            diff.changed[cid] = field_changes

    return diff
