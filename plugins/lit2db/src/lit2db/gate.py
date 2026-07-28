"""The write-gate predicate — ONE implementation behind TWO enforcement points.

Blueprint 7.3. The gate is the deterministic wrapper around a non-deterministic extractor,
so it must not exist twice in two subtly different forms. Both enforcement points call
`gate_reasons` here:

  * `gate_upsert` (the MCP tool, `mcp/lit2db_mcp/server.py`) — refuses to write.
  * `hooks/pretooluse_write_gate.py` (the PreToolUse hook) — refuses to let the call happen
    at all, because "deny" wins the Claude Code permission pipeline.

Defense in depth, not redundancy: the hook covers agent-issued calls (including a call to a
write tool the agent invents), the tool covers every other caller — scripts, tests, a
headless refresh run, a future storage backend.

`gate_reasons` is composed of two halves — `selection_reasons` (the mechanical conditions) and
`judge_veto_reasons` (the adversarial judge, D-079). The driver calls the first half alone to
decide which records are worth spending a judge call on; only the composed whole is ever applied
to a write. See `selection_reasons` for why that is a decomposition rather than a bypass flag.

Deliberately STDLIB-ONLY and dict-shaped. The hook is spawned as `python3 <file>` under
whatever interpreter the user's machine provides, which may have no pydantic installed;
shape validation stays in the Pydantic contracts and this module only applies the ratified
conditions. Domain-INVARIANT: the threshold is an input, never a constant decided here.
"""
from __future__ import annotations

# A deliberately CONSERVATIVE PLACEHOLDER, not a recommended value. It is set high on
# purpose: before a project calibrates against its gold set, the safe failure mode is
# auto-accepting too little (everything queues for human review) rather than too much
# (unverified values land in the ML-ready view silently). Expect the calibrated value to be
# far lower — in a pilot it landed in the low 0.7s, where 0.95 would have auto-accepted
# almost nothing. Calibration is per project and belongs in the ratified instantiation
# (`routing.auto_accept_threshold`); no domain-calibrated number is baked into this scaffold.
DEFAULT_AUTOACCEPT = 0.95

# Routes that must never reach the ML-ready view (blueprint 6 + ratified addition D1).
BLOCKING_ROUTES = ("quarantine", "human_review")

# The ONLY adversarial-judge verdict that clears the gate (D-079). Everything else — including
# the absence of a verdict — blocks. See `judge_veto_reasons`.
JUDGE_CLEARS = "supported"

# Why a record failed its adversarial check, in the words a reviewer needs. The two groups are
# kept apart deliberately: the first is a JUDGEMENT that stands and can be audited, the second is
# a RUN FAILURE. Collapsing them is how a paper once got marked done with 77 records whose judge
# had never been invoked.
_VETO_STRUCK = {
    "unsupported": "struck out by the adversarial judge: the source does not support the claim",
    "partial": "struck out by the adversarial judge: partial — the core claim holds but part of "
               "it over-reaches the evidence",
}
_VETO_UNCHALLENGED = {
    "not_run": "never challenged by the adversarial judge (not_run) — not judged is not "
               "supported, exactly as not searched is not clean",
    "unparseable": "the adversarial judge replied but no verdict could be read (unparseable); "
                   "the raw response is in the run's judge/ artifacts",
}

# Tools that write to the output DB, by bare name (see `tool_basename`).
WRITE_TOOLS = ("gate_upsert", "db_upsert")


def tool_basename(tool_name) -> str:
    """`mcp__lit2db__gate_upsert` -> `gate_upsert`.

    MCP tools reach hooks namespaced by server, so matching the bare name is what makes the
    hook fire on the tool the server actually exposes rather than on a name nothing emits.
    """
    return str(tool_name or "").rsplit("__", 1)[-1]


def is_write_tool(tool_name) -> bool:
    """Is this PreToolUse event a write to the output DB?"""
    return tool_basename(tool_name) in WRITE_TOOLS


def _enum_value(v):
    """Accept an Enum, a bare string, or None — the same field arrives as either
    depending on whether it came through Pydantic or straight off the wire."""
    return getattr(v, "value", v)


def _as_float(v):
    """Float or None. NaN degrades to None: it must never silently clear a threshold."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def resolve_composite(tool_input):
    """The composite confidence a write call is claiming, in precedence order:

      1. the explicit `composite_confidence` argument (`gate_upsert`'s real signature),
      2. `_composite_confidence`, as stamped on the record by `score_and_route`,
      3. weakest-link (min) over per-field confidences, if every field carries one.

    Returns None when none is available, which the gate treats as a denial rather than a
    pass. Note there is no record-level `confidence` on `ExtractedRecord` — confidence is
    per-`FieldValue`, and the composite is a separate argument.
    """
    ti = tool_input if isinstance(tool_input, dict) else {}
    c = _as_float(ti.get("composite_confidence"))
    if c is not None:
        return c
    rec = ti.get("record")
    if not isinstance(rec, dict):
        return None
    c = _as_float(rec.get("_composite_confidence"))
    if c is not None:
        return c
    fields = rec.get("fields")
    if isinstance(fields, list) and fields:
        confs = [_as_float(fv.get("confidence")) for fv in fields if isinstance(fv, dict)]
        if len(confs) == len(fields) and all(c is not None for c in confs):
            return min(confs)
    return None


def resolve_threshold(tool_input=None, env=None, default: float = DEFAULT_AUTOACCEPT) -> float:
    """Auto-accept threshold: the call's own `autoaccept` arg > `LIT2DB_AUTOACCEPT` in the
    environment > the conservative placeholder. A negative value means "unset" (the tool
    signature uses -1.0 as its sentinel)."""
    a = _as_float((tool_input or {}).get("autoaccept"))
    if a is not None and a >= 0:
        return a
    e = _as_float((env or {}).get("LIT2DB_AUTOACCEPT"))
    if e is not None and e >= 0:
        return e
    return default


def judge_veto_reasons(record):
    """The adversarial judge's veto, alone. Empty list means the judge cleared this record.

    D-079. The judge used to be a `c_judge` term at weight 0.15 inside the confidence mean,
    which described it as one contributing signal among six. It never behaved like one: a
    unanimous, fully-grounded record scored 1.000 unjudged and 1.000 judged-supported, so the
    verdict could only ever LOWER the number, never raise it. That is a veto. It is now written
    as one, and applied where the other disqualifying facts are applied.

    Only `supported` clears. `partial` blocks too, and that is load-bearing rather than strict:
    under the old mean a PARTIAL verdict scored 0.885 against a 0.95 bar, i.e. it already
    blocked. If the veto tolerated it, taking `c_judge` out of the mean would have quietly
    LOOSENED the gate while the change was being described as behaviour-preserving.

    Absence blocks as well, for the reason `contradiction_search` already blocks on `not_run`:
    a record nobody challenged has not passed its challenge. Fails closed on a missing or
    unrecognized value — an unknown verdict word is treated as no verdict, never as a pass.
    """
    if not isinstance(record, dict):
        return ["record is not an object"]
    v = _enum_value(record.get("judge_verdict"))
    v = str(v).strip().lower() if v is not None else "not_run"
    if v == JUDGE_CLEARS:
        return []
    note = record.get("judge_note")
    detail = f" — {note}" if isinstance(note, str) and note.strip() else ""
    if v in _VETO_STRUCK:
        return [_VETO_STRUCK[v] + detail]
    if v in _VETO_UNCHALLENGED:
        return [_VETO_UNCHALLENGED[v]]
    return [f"unrecognized adversarial-judge verdict {v!r} — treated as no verdict, which "
            f"blocks; a value this gate cannot read must never be read as approval"]


def selection_reasons(record, composite_confidence, autoaccept: float = DEFAULT_AUTOACCEPT,
                      require_contradiction_search: bool = False, review_lane=()):
    """Every reason this record fails SELECTION — the whole gate except the judge's veto.

    Split out of `gate_reasons` for one caller: the wave driver, which needs to know who
    survives selection so it can spend an adversarial read only on those (plus a ratified random
    audit slice of the rejected, so the veto's reject-side behaviour stays measurable). Judging
    everything first and selecting afterwards is what made 84% of judge calls unable to affect
    any outcome.

    **There is no way to reach the write path through this function.** `gate_upsert` and the
    PreToolUse hook both call `gate_reasons`, which is this plus the veto, unconditionally. A
    boolean argument on the gate would have been the smaller diff and the wrong shape: the one
    predicate behind two enforcement points must not grow a way to be told to skip a condition.

    The ratified conditions, all of which must hold:
      1. composite_confidence >= the auto-accept threshold,
      2. neither the record nor any field routes to quarantine / human_review,
      3. every field carries provenance whose source_status is 'active' — a retracted or
         superseded source never lands, however confident the extraction was,
      4. no field carries counter-evidence from its own source.

    Condition 4 is a BLOCK, not a penalty. Every confidence signal scores the span the
    extractor chose to surface; a contradiction says that choice was unrepresentative, and
    averaging it into a weighted mean lets four confident signals bury one real refutation.
    Same logic as source_status: some facts disqualify a value outright.

    `require_contradiction_search` additionally blocks values whose source was never
    searched for counter-evidence — "we did not look" is not "we looked and it was clean."
    Off by default so existing pipelines keep working; switching it on is also exactly the
    control/treatment lever for measuring how often counter-evidence changes an outcome.

    Fails CLOSED: a malformed record, an absent composite, or a field with no provenance
    denies. The gate's whole value is that it cannot be talked past.
    """
    if not isinstance(record, dict):
        return ["record is not an object"]

    reasons = []
    comp = _as_float(composite_confidence)
    if comp is None:
        reasons.append(f"composite confidence missing or unusable ({composite_confidence!r})")
    elif comp < autoaccept:
        reasons.append(f"composite {comp:.3f} < auto-accept {autoaccept}")

    rec_route = _enum_value(record.get("route"))
    if rec_route in BLOCKING_ROUTES:
        failure = _enum_value(record.get("failure_reason"))
        reasons.append(f"record routed {rec_route}" + (f" ({failure})" if failure else ""))

    fields = record.get("fields")
    if not isinstance(fields, list) or not fields:
        reasons.append("record carries no fields")
        return reasons

    lane = set(review_lane or ())
    for i, fv in enumerate(fields):
        if not isinstance(fv, dict):
            reasons.append(f"field #{i} is not an object")
            continue
        name = fv.get("field_name") or f"#{i}"
        if name in lane:
            # A ratified review-lane field is one the researcher has already decided can never
            # be auto-accepted — free prose, typically. It is HELD OUT of the write rather than
            # blocking it, so it cannot block a row it is not part of. Letting it block was
            # measured to deny records whose other eight fields all scored 1.000 with unanimous
            # agreement: a field designed never to pass was vetoing every row it appeared in,
            # and the pilot's auto-accept rate would have been zero by construction rather
            # than by evidence. `gate_upsert` must strip these before writing — the guarantee
            # here is "not written", never "written unchecked".
            continue
        route = _enum_value(fv.get("route"))
        if route in BLOCKING_ROUTES:
            reasons.append(f"field '{name}' routed {route}")
        prov = fv.get("provenance")
        if not isinstance(prov, dict):
            reasons.append(f"field '{name}' carries no provenance")
            continue
        status = _enum_value(prov.get("source_status"))
        if status is not None and status != "active":
            reasons.append(f"field '{name}' source_status={status}")

        # Counter-evidence. Blocking regardless of confidence — see the docstring.
        found = fv.get("contradictions")
        if isinstance(found, list) and found:
            kinds = sorted({str(_enum_value((c or {}).get("kind")) or "other")
                            for c in found if isinstance(c, dict)})
            reasons.append(f"field '{name}' contradicted by its own source "
                           f"({len(found)}x: {', '.join(kinds)})")
        elif require_contradiction_search:
            searched = _enum_value(fv.get("contradiction_search"))
            if searched != "clean":
                reasons.append(f"field '{name}' counter-evidence search "
                               f"{searched or 'not_run'} — not searched is not clean")
    return reasons


def gate_reasons(record, composite_confidence, autoaccept: float = DEFAULT_AUTOACCEPT,
                 require_contradiction_search: bool = False, review_lane=()):
    """Every reason this record must NOT be written. An empty list means the write passes.

    THE write predicate — the one both enforcement points call (`gate_upsert` and the PreToolUse
    hook). Selection plus the adversarial judge's veto, composed here so the two can never be
    applied in different combinations by different callers.

    Fails CLOSED: a malformed record, an absent composite, a field with no provenance, or a
    record the judge never cleared all deny. The gate's whole value is that it cannot be talked
    past.
    """
    return (selection_reasons(record, composite_confidence, autoaccept,
                              require_contradiction_search, review_lane)
            + judge_veto_reasons(record))
