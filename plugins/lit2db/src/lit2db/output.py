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
from .gate import DEFAULT_AUTOACCEPT, DEFAULT_MIN_POPULATED_FIELDS, gate_reasons


def connect(db_path: str) -> sqlite3.Connection:
    """The output store: TWO tables, deliberately not one with a status column.

    `records` is the ML-ready table. Everything in it cleared the gate — that sentence is
    literally true, which is what makes the database worth trusting and what the PreToolUse hook
    protects.

    `candidates` is everything the pipeline produced, gated or not, with its score, route,
    decision and reasons. It is the large database: the pipeline is meant to ACCELERATE curation,
    not replace it, and a record that missed the bar still arrives with its quote, character
    offset, grounding score, agreement fraction and judge verdict attached — which is most of the
    work of confirming it. Discarding those was throwing away the majority of the acceleration.

    **Two tables rather than one flagged table, on this project's own evidence.** A shipped BBB
    database was found holding 18 rejected-but-present records, because a single table with a
    status column relies on every future reader remembering to filter. Separation cannot be
    forgotten: `db_query` reads `records` and nothing else.
    """
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS records(
        record_id TEXT PRIMARY KEY,
        entity_type TEXT,
        composite_confidence REAL,
        route TEXT,
        source_status TEXT,
        payload_json TEXT,
        written_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS candidates(
        record_id TEXT,
        source_id TEXT,
        entity_type TEXT,
        composite_confidence REAL,
        decision TEXT,
        reasons_json TEXT,
        judge_verdict TEXT,
        payload_json TEXT,
        recorded_at TEXT,
        PRIMARY KEY (record_id, source_id))""")
    # `adjudications` — what a human said about a candidate. A THIRD table, for the same reason
    # there are already two: it is a different kind of fact. `records` is what the gate accepted,
    # `candidates` is what the pipeline produced, and this is what a person concluded. None of
    # them can be derived from the others.
    #
    # This is the calibration set, accumulated rather than commissioned. The gold-set item sat on
    # the ladder for weeks as a blocker because it was framed as work to commission; a researcher
    # confirming candidates is work they were going to do anyway, and this is the table that
    # stops that work from evaporating the moment they close the terminal.
    con.execute("""CREATE TABLE IF NOT EXISTS adjudications(
        record_id TEXT,
        source_id TEXT,
        verdict TEXT,
        note TEXT,
        adjudicator TEXT,
        adjudicated_at TEXT,
        PRIMARY KEY (record_id, source_id))""")
    return con


def upsert(record: dict, composite_confidence: float, db_path: str,
           autoaccept: float = DEFAULT_AUTOACCEPT,
           require_contradiction_search: bool = False,
           review_lane: list | None = None,
           min_populated_fields: int = DEFAULT_MIN_POPULATED_FIELDS) -> dict:
    """The HARD write-gate. Writes IFF `gate_reasons` returns nothing.

    'deny' wins. A denied record is NOT written and its reasons come back for routing to the
    human-review or quarantine queue. This is what makes the ratified quality bar mechanical
    rather than advisory.

    **THE STORED ID IS SOURCE-QUALIFIED: `<source_id>:<record_id>`** (D-091). Extractors number
    records per paper, so `cpd1` is the first compound in *some* paper and two papers collide by
    construction. This is not hypothetical and it is no longer prospective: on the compound
    pilot's fourth paper, sulfadixiamycins A and B — composite 1.000, supported by the
    adversarial judge — were refused because `cpd1`/`cpd2` were already held by corvol ethers A
    and B from a different paper. Under a bare `INSERT OR REPLACE` the corvols would have been
    silently overwritten: no error, no reason string, nothing in any artifact to show a row had
    gone.

    The local id stays in the payload, because "the first compound in this paper" is a true fact
    about the source and worth keeping; the qualified form is what the table keys on and what
    `db_query` returns.

    **The collision check survives qualification, now aimed at the case it can still catch:**
    two DIFFERENT records under one id from ONE source, which `merge_passes` really does produce
    (15 records under 11 ids on PMC10325987). Qualification cannot help there — same source, same
    local id — so the refusal is still the answer.

    Re-writing the SAME record is still allowed — a re-run must be idempotent — so the check is
    on the payload, not on the id alone. A genuine collision is a denial with a reason, which is
    the only outcome consistent with everything else this gate does.
    """
    lane = set(review_lane or [])
    rec = ExtractedRecord.model_validate(record)  # shape first: an unparseable record cannot be gated
    reasons = gate_reasons(json.loads(rec.model_dump_json()), composite_confidence, autoaccept,
                           require_contradiction_search=require_contradiction_search,
                           review_lane=lane, min_populated_fields=min_populated_fields)
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
    sources = {fv.provenance.source_id for fv in rec.fields
               if getattr(fv.provenance, "source_id", None)}
    if len(sources) > 1:
        # One record, two sources: the unit of analysis says a record belongs to one source, so
        # this is a defect upstream rather than something to pick a winner for.
        return {"written": False, "decision": "deny",
                "reasons": [f"record's fields cite {len(sources)} different sources "
                            f"({sorted(sources)}) — a record belongs to one source, so it "
                            f"cannot be identified by one"],
                "route_to": "human_review_or_quarantine_queue"}
    stored_id = f"{sources.pop()}:{rec.record_id}" if sources else rec.record_id

    con = connect(db_path)
    try:
        prior = con.execute("SELECT payload_json FROM records WHERE record_id = ?",
                            (stored_id,)).fetchone()
        if prior is not None and prior[0] != payload:
            return {"written": False, "decision": "deny",
                    "reasons": [f"record_id '{stored_id}' is already held by a DIFFERENT record "
                                f"— writing would silently replace it. The id is already "
                                f"source-qualified, so this is a collision WITHIN one source: "
                                f"the merge produced two different records under one id."],
                    "route_to": "human_review_or_quarantine_queue"}
        con.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?)",
                    (stored_id, rec.entity_type, composite_confidence, "auto_accept",
                     "active", payload, datetime.now(timezone.utc).isoformat()))
        con.commit()
    finally:
        con.close()
    return {"written": True, "decision": "allow", "record_id": stored_id,
            "local_record_id": rec.record_id,
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


def record_candidate(record: dict, composite_confidence: float, gate_result: dict,
                     db_path: str, source_id: str = "") -> dict:
    """Record a record in the CANDIDATE pool, whatever the gate decided about it.

    Not a gated write and deliberately not named like one: `lit2db.gate.WRITE_TOOLS` does not
    list it, so the PreToolUse hook leaves it alone. It cannot be used to reach the ML-ready
    table — that is a different table, and `query` reads only `records`.

    `source_id` qualifies the id because record ids are per-source ordinals: `ts6` exists in more
    than one paper, so the candidate pool keys on the pair. The ML-ready table's collision refusal
    stays as it is — that one is a real hazard and must stay loud.
    """
    payload = json.dumps(record, sort_keys=True, default=str)
    con = connect(db_path)
    try:
        con.execute("INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?,?,?,?,?)",
                    (str(record.get("record_id", "")), str(source_id),
                     str(record.get("entity_type", "")), composite_confidence,
                     str((gate_result or {}).get("decision", "unknown")),
                     json.dumps((gate_result or {}).get("reasons") or []),
                     str(record.get("judge_verdict", "not_run")), payload,
                     datetime.now(timezone.utc).isoformat()))
        con.commit()
    finally:
        con.close()
    return {"recorded": True, "record_id": record.get("record_id"), "source_id": source_id}


# What a human can honestly say about a candidate. THREE answers, not two (ratified 2026-07-28).
#
# `cant_tell` is not hedging and it is not a missing label — it is the most common honest answer
# in this corpus. 60% of the collaborator's compounds are named only in full texts we cannot
# obtain, so a reviewer reading an abstract genuinely cannot rule on them. Folding those into
# `wrong` would blame the extractor for a paywall and would bias every calibrated bar downward
# by exactly the fraction of the literature we cannot reach.
#
# Same distinction the pipeline already draws in two other places: `contradiction_search.not_run`
# is not `clean`, and `JudgeVerdict.not_run` is not `supported`. "We could not check" is a third
# state everywhere else in this system; it is a third state here too.
ADJUDICATION_VERDICTS = ("right", "wrong", "cant_tell")


def record_adjudication(record_id: str, source_id: str, verdict: str, db_path: str,
                        note: str = "", adjudicator: str = "researcher") -> dict:
    """Record a human's judgement of one candidate. The calibration set, one row at a time.

    NOT a gated write and deliberately not shaped like one: `lit2db.gate.WRITE_TOOLS` does not
    list it, so the PreToolUse hook leaves it alone, and it touches neither `records` nor
    `candidates`. A human saying "this one is right" must not put a row in the ML-ready table —
    that is a claim about the GATE's accuracy, and using it to bypass the gate would destroy the
    measurement it exists to produce. Confirming a record and writing it are different acts and
    they stay in different tables.

    Re-adjudicating replaces the previous verdict: a reviewer is allowed to change their mind,
    and the alternative is a table where the same record appears with two answers and nothing
    says which is current.

    Fails CLOSED on an unrecognized verdict rather than storing it. A free-text verdict would
    make the calibration table silently incomplete — the same reason `failure_reason` is an enum.
    """
    v = str(verdict or "").strip().lower()
    if v not in ADJUDICATION_VERDICTS:
        return {"recorded": False,
                "reason": f"verdict {verdict!r} is not one of {list(ADJUDICATION_VERDICTS)}; "
                          f"a verdict this table cannot read must not be stored as one"}
    con = connect(db_path)
    try:
        con.execute("INSERT OR REPLACE INTO adjudications VALUES (?,?,?,?,?,?)",
                    (str(record_id), str(source_id), v, str(note or ""), str(adjudicator),
                     datetime.now(timezone.utc).isoformat()))
        con.commit()
    finally:
        con.close()
    return {"recorded": True, "record_id": record_id, "source_id": source_id, "verdict": v}


def adjudications(db_path: str, verdict: str = "") -> dict:
    """Everything a human has ruled on, joined to the signals the gate saw.

    This join IS the calibration set: one row per adjudicated candidate carrying both the
    verdict and the evidence the gate had when it decided. `lit2db.calibration` turns it into a
    precision table; nothing else needs to know how it was gathered.
    """
    con = connect(db_path)
    try:
        sql = ("SELECT a.record_id, a.source_id, a.verdict, a.note, a.adjudicator, "
               "c.composite_confidence, c.decision, c.judge_verdict, c.entity_type, "
               "c.payload_json "
               "FROM adjudications a LEFT JOIN candidates c "
               "ON a.record_id = c.record_id AND a.source_id = c.source_id")
        args: list = []
        if verdict:
            sql += " WHERE a.verdict = ?"
            args.append(verdict)
        sql += " ORDER BY a.source_id, a.record_id"
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        payload = {}
        if r[9]:
            try:
                payload = json.loads(r[9])
            except (TypeError, ValueError):
                payload = {}
        out.append({"record_id": r[0], "source_id": r[1], "verdict": r[2], "note": r[3],
                    "adjudicator": r[4], "composite_confidence": r[5], "gate_decision": r[6],
                    "judge_verdict": r[7], "entity_type": r[8],
                    "n_populated_fields": len(_populated_names(payload)),
                    "populated_fields": _populated_names(payload)})
    counts = {v: sum(1 for o in out if o["verdict"] == v) for v in ADJUDICATION_VERDICTS}
    return {"n": len(out), "counts": counts, "adjudications": out}


def _populated_names(payload: dict) -> list:
    """Completeness, read off the stored payload. Imported from the gate rather than
    reimplemented — a second definition of "populated" would let the calibration table and the
    write predicate disagree about the very signal being calibrated."""
    from .gate import populated_fields
    return populated_fields(payload if isinstance(payload, dict) else {})


def review_queue(db_path: str, source_id: str = "", limit: int = 100,
                 order: str = "best_first", unadjudicated_only: bool = False) -> dict:
    """The candidate pool — what a human would confirm, best-first.

    Best-first because the point is acceleration: the near-misses are where a minute of a
    researcher's attention converts into a row, and a queue sorted worst-first spends that
    attention on the records least likely to survive it.

    Returns the denial reasons alongside each row, so a reviewer sees WHY it stopped short
    without opening a run artifact.

    `unadjudicated_only` hides what a human has already ruled on. Without it a second sitting
    re-asks the same questions in the same order, which is how a review loop stops being used
    after the first session — and an unused review loop produces no calibration set at all.
    """
    con = connect(db_path)
    try:
        sql = ("SELECT record_id, source_id, entity_type, composite_confidence, decision, "
               "reasons_json, judge_verdict FROM candidates WHERE decision != 'allow'")
        args: list = []
        if unadjudicated_only:
            sql += (" AND NOT EXISTS (SELECT 1 FROM adjudications a "
                    "WHERE a.record_id = candidates.record_id "
                    "AND a.source_id = candidates.source_id)")
        if source_id:
            sql += " AND source_id = ?"
            args.append(source_id)
        sql += (" ORDER BY composite_confidence DESC" if order == "best_first"
                else " ORDER BY composite_confidence ASC")
        sql += " LIMIT ?"
        args.append(limit)
        rows = con.execute(sql, args).fetchall()
        total = con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        accepted = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        judged = con.execute("SELECT COUNT(*) FROM adjudications").fetchone()[0]
    finally:
        con.close()
    return {"n": len(rows), "candidates_total": total, "ml_ready_total": accepted,
            "adjudicated_total": judged,
            "queue": [{"record_id": r[0], "source_id": r[1], "entity_type": r[2],
                       "composite_confidence": r[3], "decision": r[4],
                       "reasons": json.loads(r[5] or "[]"), "judge_verdict": r[6]}
                      for r in rows]}
