"""Stage 7 — the gated write, and the ML-ready view.

The write PREDICATE lives in `gate.py` and is deliberately stdlib-only, because the PreToolUse
hook runs it under whatever interpreter the user's machine provides. This module is the other
half: what happens once the predicate says yes. It lives in the library for the same reason
`scoring.py` does — the MCP tool and the headless driver are both callers, and a function two
callers reach by loading a *server file* as a module is not a library function.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .contracts import ExtractedRecord
from .gate import DEFAULT_AUTOACCEPT, gate_reasons


def connect(db_path: str) -> sqlite3.Connection:
    """The output store. `record_id` is the primary key — see `upsert` for what that costs."""
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS records(
        record_id TEXT PRIMARY KEY,
        entity_type TEXT,
        composite_confidence REAL,
        route TEXT,
        source_status TEXT,
        payload_json TEXT,
        written_at TEXT)""")
    return con


def upsert(record: dict, composite_confidence: float, db_path: str,
           autoaccept: float = DEFAULT_AUTOACCEPT,
           require_contradiction_search: bool = False,
           review_lane: list | None = None) -> dict:
    """The HARD write-gate. Writes IFF `gate_reasons` returns nothing.

    'deny' wins. A denied record is NOT written and its reasons come back for routing to the
    human-review or quarantine queue. This is what makes the ratified quality bar mechanical
    rather than advisory.

    **A COLLIDING `record_id` IS REFUSED, not silently replaced.** The table keys on
    `record_id`, and `INSERT OR REPLACE` would let a second record under the same id overwrite
    the first with no error, no reason string, and nothing in any artifact to show a row had
    gone. That is not hypothetical: `merge_passes` returned 15 records under 11 ids on
    PMC10325987, and record ids are per-source ordinals, so `ts6` exists in more than one paper.
    It has never fired only because every paper carrying duplicates wrote zero records.

    Re-writing the SAME record is still allowed — a re-run must be idempotent — so the check is
    on the payload, not on the id alone. A genuine collision is a denial with a reason, which is
    the only outcome consistent with everything else this gate does.
    """
    lane = set(review_lane or [])
    rec = ExtractedRecord.model_validate(record)  # shape first: an unparseable record cannot be gated
    reasons = gate_reasons(json.loads(rec.model_dump_json()), composite_confidence, autoaccept,
                           require_contradiction_search=require_contradiction_search,
                           review_lane=lane)
    if reasons:
        return {"written": False, "decision": "deny", "reasons": reasons,
                "route_to": "human_review_or_quarantine_queue"}

    # A ratified review-lane field is HELD, not written. Stripping it here is what makes the
    # exemption in `gate_reasons` safe: the field stops blocking the row precisely because it is
    # not part of the row. Writing it while exempting it from the checks would be the worst of
    # both — unverified prose in the database, under a passing gate.
    held = [fv for fv in rec.fields if fv.field_name in lane]
    if held:
        rec = rec.model_copy(update={"fields": [fv for fv in rec.fields
                                                if fv.field_name not in lane]})
        if not rec.fields:
            return {"written": False, "decision": "deny",
                    "reasons": ["every field is in the review lane — nothing left to write"],
                    "route_to": "human_review_or_quarantine_queue"}

    payload = rec.model_dump_json()
    con = connect(db_path)
    try:
        prior = con.execute("SELECT payload_json FROM records WHERE record_id = ?",
                            (rec.record_id,)).fetchone()
        if prior is not None and prior[0] != payload:
            return {"written": False, "decision": "deny",
                    "reasons": [f"record_id '{rec.record_id}' is already held by a DIFFERENT "
                                f"record — writing would silently replace it. Record ids are "
                                f"per-source ordinals and are not unique across sources; "
                                f"qualify the id before writing."],
                    "route_to": "human_review_or_quarantine_queue"}
        con.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?)",
                    (rec.record_id, rec.entity_type, composite_confidence, "auto_accept",
                     "active", payload, datetime.now(timezone.utc).isoformat()))
        con.commit()
    finally:
        con.close()
    return {"written": True, "decision": "allow", "record_id": rec.record_id,
            "held_for_review": [fv.field_name for fv in held]}


def query(db_path: str, entity_type: str = "", limit: int = 100) -> dict:
    """The ML-ready view: auto-accepted, active-source records only."""
    con = connect(db_path)
    try:
        sql = ("SELECT record_id, entity_type, composite_confidence, source_status, written_at "
               "FROM records WHERE route = 'auto_accept' AND source_status = 'active'")
        args: list = []
        if entity_type:
            sql += " AND entity_type = ?"
            args.append(entity_type)
        sql += " ORDER BY written_at DESC LIMIT ?"
        args.append(limit)
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return {"n": len(rows),
            "records": [{"record_id": r[0], "entity_type": r[1], "composite_confidence": r[2],
                         "source_status": r[3], "written_at": r[4]} for r in rows]}
