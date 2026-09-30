"""
Real change-management workflow: a change request moves through a genuinely
enforced state machine, with every transition (and every rejection of an
invalid transition) written to the audit_log table.

States:  DRAFT -> UNDER_REVIEW -> APPROVED -> PUBLISHED
                              \-> REJECTED
                UNDER_REVIEW -> DRAFT (sent back for rework)

Invalid transitions (e.g. DRAFT -> PUBLISHED, or anything out of a terminal
state PUBLISHED/REJECTED) raise InvalidTransitionError and are NOT applied;
an audit record is still written for the rejected attempt.
"""
from __future__ import annotations

import sqlite3

from . import db as dbmod

DRAFT = "DRAFT"
UNDER_REVIEW = "UNDER_REVIEW"
APPROVED = "APPROVED"
PUBLISHED = "PUBLISHED"
REJECTED = "REJECTED"

ALL_STATES = {DRAFT, UNDER_REVIEW, APPROVED, PUBLISHED, REJECTED}

# Allowed transitions: from_state -> set of to_states
ALLOWED = {
    DRAFT: {UNDER_REVIEW},
    UNDER_REVIEW: {APPROVED, REJECTED, DRAFT},
    APPROVED: {PUBLISHED},
    PUBLISHED: set(),   # terminal
    REJECTED: set(),    # terminal
}


class InvalidTransitionError(Exception):
    pass


def create_change_request(conn: sqlite3.Connection, title: str, description: str,
                           requested_by: str) -> int:
    cur = conn.execute(
        "INSERT INTO change_requests (title, description, state, requested_by) "
        "VALUES (?, ?, ?, ?)",
        (title, description, DRAFT, requested_by),
    )
    conn.commit()
    cr_id = cur.lastrowid
    dbmod.log_audit(conn, "change_request", cr_id, "CREATED",
                     f"title={title!r} state={DRAFT}", requested_by)
    return cr_id


def get_state(conn: sqlite3.Connection, cr_id: int) -> str:
    row = conn.execute("SELECT state FROM change_requests WHERE cr_id = ?", (cr_id,)).fetchone()
    if row is None:
        raise ValueError(f"No change request with id {cr_id}")
    return row["state"]


def transition(conn: sqlite3.Connection, cr_id: int, to_state: str, actor: str,
               reason: str = "") -> str:
    """Attempt to move change request cr_id to to_state. Enforces ALLOWED
    transitions. Every attempt (successful or not) is written to audit_log.
    Raises InvalidTransitionError on an illegal transition; the DB state is
    left unchanged in that case."""
    if to_state not in ALL_STATES:
        raise ValueError(f"Unknown state: {to_state}")

    current = get_state(conn, cr_id)
    if to_state not in ALLOWED[current]:
        dbmod.log_audit(
            conn, "change_request", cr_id, "TRANSITION_REJECTED",
            f"from={current} to={to_state} reason={reason!r} -- not an allowed transition",
            actor,
        )
        raise InvalidTransitionError(
            f"Illegal transition for change request {cr_id}: {current} -> {to_state}"
        )

    conn.execute(
        "UPDATE change_requests SET state = ?, updated_at = datetime('now') WHERE cr_id = ?",
        (to_state, cr_id),
    )
    conn.commit()
    dbmod.log_audit(
        conn, "change_request", cr_id, "STATE_TRANSITION",
        f"from={current} to={to_state} reason={reason!r}",
        actor,
    )
    return to_state


def get_audit_trail(conn: sqlite3.Connection, cr_id: int):
    return conn.execute(
        "SELECT * FROM audit_log WHERE entity_type = 'change_request' AND entity_id = ? "
        "ORDER BY audit_id",
        (cr_id,),
    ).fetchall()
