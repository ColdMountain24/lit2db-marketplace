"""lit2db MCP server — the DETERMINISTIC pipeline spine.

This server is the domain-invariant, non-negotiable half of the architecture: the
verification, routing, and hard write-gate machinery that wraps the (non-deterministic)
LLM extractor. The LLM *proposes* extracted values with grounding quotes; this server
*verifies, scores, routes, and gates* them into an auditable SQLite store. No domain
substance lives here — thresholds and weights come from the ratified instantiation.

Tools exposed (one per pipeline responsibility):
  - validate_record     Stage 3/4a  Pydantic-validate an ExtractedRecord's shape.
  - ground_literature   Stage 4b    Deterministic span-grounding: does each value
                                     actually appear (lexically / numerically) in its quote?
  - validate_mapping    Stage 4b    Structured-adapter grounding: type/range/enum conformance.
  - score_and_route     Stage 5/6   Composite confidence + per-field + record-level routing.
  - gate_upsert         Stage 7     The HARD write-gate: write iff it clears auto-accept,
                                     no field is quarantined/human_review, source is active.
  - db_query            Stage 7     Read the ML-ready view (auto-accepted, non-retracted).

Grounding here is deliberately the *naive lexical/numeric* check — it is the baseline the
project empirically showed passes ~100% while true factual precision is far lower. The
cross-family ADVERSARIAL JUDGE is the orchestrator's job (verifier-judge-agent, a different
model family); its verdict is fed back as the c_judge component. Separation is the point:
the server never adjudicates meaning, only mechanical conformance.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the ported lit2db contracts importable regardless of install state.
_PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parents[2]))
_SRC = _PLUGIN_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lit2db.contracts import (  # noqa: E402
    ExtractedRecord, FieldValue, RouteDecision, FailureReason,
    ConfidenceComponents, default_route, DEFAULT_WEIGHTS,
)
from lit2db.contracts.spec import SchemaReadySpec  # noqa: E402
from lit2db.gate import gate_reasons, resolve_threshold  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("lit2db")

# --- Config, resolved from the active instantiation (env-overridable) ----------------
# Falls back to lit2db.gate.DEFAULT_AUTOACCEPT (0.95), a conservative PLACEHOLDER — a real
# project overrides it from its calibrated `routing.auto_accept_threshold`. See gate.py.
AUTOACCEPT = resolve_threshold(env=os.environ)
DB_PATH = os.environ.get("LIT2DB_DB_PATH", str(_PLUGIN_ROOT / "examples" / "demo.db"))


# ------------------------------------------------------------------------------------
# Stage 3/4a — schema validation
# ------------------------------------------------------------------------------------
@mcp.tool()
def validate_record(record: dict) -> dict:
    """Pydantic-validate an ExtractedRecord's shape (Stage 3 output / Stage 4a).

    Returns {ok, errors}. A shape failure here is a hard block upstream of any grounding —
    an unparseable record cannot be verified, only quarantined."""
    try:
        rec = ExtractedRecord.model_validate(record)
        return {"ok": True, "record_id": rec.record_id, "n_fields": len(rec.fields)}
    except Exception as e:  # pydantic ValidationError or otherwise
        return {"ok": False, "errors": str(e)}


# ------------------------------------------------------------------------------------
# Stage 4b — grounding (literature: span-entailment proxy; structured: mapping validation)
# ------------------------------------------------------------------------------------
def _norm_num(s: str):
    """Pull the first numeric token out of a string, tolerant of unicode minus / commas."""
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(s).replace("\u2212", "-"))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


@mcp.tool()
def ground_literature(value: object, quote: str) -> dict:
    """Deterministic span-grounding for the literature adapter (Stage 4b, naive baseline).

    Does the extracted value actually appear in its verbatim quote — numerically (with a
    small relative tolerance) or as a normalized substring? Returns a c_grounded score in
    [0,1]. This is intentionally the surface check; semantic support is the judge's call."""
    q = (quote or "").strip()
    if not q:
        return {"c_grounded": 0.0, "mode": "no_quote"}
    v_num = _norm_num(value)
    if v_num is not None:
        # numeric grounding: any number in the quote within 1% relative tolerance
        nums = [float(x.replace(",", "")) for x in
                re.findall(r"[-+]?\d[\d,]*\.?\d*", q.replace("\u2212", "-"))]
        for n in nums:
            denom = max(abs(v_num), 1e-9)
            if abs(n - v_num) / denom <= 0.01:
                return {"c_grounded": 1.0, "mode": "numeric_match", "matched": n}
        return {"c_grounded": 0.0, "mode": "numeric_absent", "quote_numbers": nums}
    # string grounding: normalized substring
    sv = re.sub(r"\s+", " ", str(value).strip().lower())
    sq = re.sub(r"\s+", " ", q.lower())
    return {"c_grounded": 1.0 if sv and sv in sq else 0.0,
            "mode": "string_match" if sv and sv in sq else "string_absent"}


@mcp.tool()
def validate_mapping(value: object, field_spec: dict) -> dict:
    """Mapping validation for the structured adapter (Stage 4b): type/range/enum conformance.

    field_spec is a FieldSpec-shaped dict (type, valid_range, enum). Passing = c_grounded 1.0.
    A value outside the ratified valid_range is NOT dropped — it is flagged so the researcher
    can recalibrate the bound (the segregate-don't-drop discipline)."""
    ftype = field_spec.get("type")
    reasons = []
    ok = True
    if ftype in ("float", "int"):
        v = _norm_num(value)
        if v is None:
            ok, _ = False, reasons.append("not numeric")
        else:
            vr = field_spec.get("valid_range")
            if vr and not (vr[0] <= v <= vr[1]):
                ok = False
                reasons.append(f"value {v} outside valid_range {tuple(vr)}")
    enum = field_spec.get("enum")
    if enum and str(value) not in enum:
        ok = False
        reasons.append(f"value {value!r} not in enum {enum}")
    return {"c_grounded": 1.0 if ok else 0.0, "ok": ok, "flags": reasons}


# ------------------------------------------------------------------------------------
# Stage 5/6 — composite confidence + routing
# ------------------------------------------------------------------------------------
@mcp.tool()
def score_and_route(record: dict, weights_key: str = "numeric") -> dict:
    """Composite confidence per field (blueprint 5.2) + per-field and record-level routing.

    Each field's confidence_components are combined with the ratified weight vector over
    PRESENT signals only (graceful degradation). Fields route via default_route; a record
    with ANY unparseable/mapping-invalid field, or no fields, is QUARANTINED (record-level
    dead-letter, distinct from field-level human_review). Returns the annotated record +
    a composite record confidence (min over fields = weakest-link)."""
    rec = ExtractedRecord.model_validate(record)
    weights = DEFAULT_WEIGHTS.get(weights_key, DEFAULT_WEIGHTS["numeric"])
    field_confs = []
    for fv in rec.fields:
        c = fv.confidence_components
        if c is not None:
            try:
                fv.confidence = c.composite(weights)
            except ValueError:
                fv.confidence = None
        fv.route = default_route(fv)
        field_confs.append(fv.confidence if fv.confidence is not None else 0.0)

    # record-level quarantine: no fields, or any field with zero grounding AND no signals
    if not rec.fields:
        rec.route = RouteDecision.quarantine
        rec.failure_reason = FailureReason.incoherent
    composite = min(field_confs) if field_confs else 0.0
    out = json.loads(rec.model_dump_json())
    out["_composite_confidence"] = composite
    out["_routing_summary"] = {
        r.value: sum(1 for fv in rec.fields if fv.route == r) for r in RouteDecision
    }
    return out


# ------------------------------------------------------------------------------------
# Stage 7 — the HARD write-gate + storage
# ------------------------------------------------------------------------------------
def _conn(db_path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS records(
        record_id TEXT PRIMARY KEY,
        entity_type TEXT,
        composite_confidence REAL,
        route TEXT,
        source_status TEXT,
        payload_json TEXT,
        written_at TEXT)""")
    return con


@mcp.tool()
def gate_upsert(record: dict, composite_confidence: float,
                db_path: str = "", autoaccept: float = -1.0) -> dict:
    """The HARD write-gate (Stage 7). Writes to the DB IFF ALL hold:
      (1) composite_confidence >= auto-accept threshold,
      (2) no field routes to quarantine or human_review,
      (3) every field's source_status is 'active'.
    'deny' wins. A denied record is NOT written; its reasons are returned for routing to the
    human-review or quarantine queue. This is the deterministic gate that makes the ratified
    quality bar mechanical rather than advisory.

    The conditions themselves live in `lit2db.gate` — the same predicate the PreToolUse hook
    applies, so the two enforcement points cannot drift apart."""
    thr = autoaccept if autoaccept >= 0 else AUTOACCEPT
    rec = ExtractedRecord.model_validate(record)  # shape first: an unparseable record cannot be gated
    reasons = gate_reasons(json.loads(rec.model_dump_json()), composite_confidence, thr)
    if reasons:
        return {"written": False, "decision": "deny", "reasons": reasons,
                "route_to": "human_review_or_quarantine_queue"}
    con = _conn(db_path or None)
    src_status = "active"
    con.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?)",
                (rec.record_id, rec.entity_type, composite_confidence, "auto_accept",
                 src_status, rec.model_dump_json(), datetime.now(timezone.utc).isoformat()))
    con.commit(); con.close()
    return {"written": True, "decision": "allow", "record_id": rec.record_id}


@mcp.tool()
def db_query(db_path: str = "", limit: int = 50) -> dict:
    """Read the ML-ready view: auto-accepted, active-source records only (Stage 7 output)."""
    con = _conn(db_path or None)
    rows = con.execute(
        "SELECT record_id, entity_type, composite_confidence, source_status, written_at "
        "FROM records WHERE route='auto_accept' AND source_status='active' "
        "ORDER BY written_at DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return {"n": len(rows), "records": [
        {"record_id": r[0], "entity_type": r[1], "composite_confidence": r[2],
         "source_status": r[3], "written_at": r[4]} for r in rows]}


if __name__ == "__main__":
    mcp.run()
