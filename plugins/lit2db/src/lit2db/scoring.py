"""Stage 5/6 — composite confidence and routing. THE implementation, not a copy of one.

WHY THIS MODULE EXISTS. This logic used to live inside the MCP server's `score_and_route` tool,
which made the MCP layer the only way to reach it. The headless driver needed it too, so
`run_wave.py` and `replay.py` both did:

    spec = importlib.util.spec_from_file_location("srv", PLUGIN / "mcp/lit2db_mcp/server.py")

— loading a *tool server* as a module to borrow a function out of it. That is the shape of the
project's root defect in miniature: the library declared the pipeline (`stages/`, nine empty
functions) while the pipeline itself lived somewhere else, reachable only by a path nobody would
guess. Scoring is library work. The MCP tool is one caller of it, and so is the driver.

Domain-INVARIANT: weights and thresholds arrive as arguments. Nothing here decides them.
"""
from __future__ import annotations

import json

from .contracts import (DEFAULT_WEIGHTS, ExtractedRecord, FailureReason, RouteDecision,
                        default_route, required_agreement)


def score_and_route(record: dict, weights_key: str = "numeric",
                    ensemble_k: int = 0, ensemble_min_agreeing: int = 0,
                    review_lane: list | None = None) -> dict:
    """Composite confidence per field + per-field and record-level routing.

    Each field's `confidence_components` are combined with the ratified weight vector over
    PRESENT signals only (graceful degradation). Fields route via `default_route`; a record with
    no fields is QUARANTINED (record-level dead-letter, distinct from field-level human_review).
    Returns the annotated record plus a record composite (min over fields = weakest link).

    **This is SELECTION, and it is two mechanical signals** — grounding and cross-pass agreement
    — not a composite over six verification signals (D-079). The adversarial judge is not scored
    here; it vetoes what survives, at the gate. Everything this function returns is a statement
    about what the pipeline could check by itself.

    `ensemble_k` / `ensemble_min_agreeing` express the agreement bar as "how many of how many
    passes must agree", the units the signal can actually take, since `c_ensemble` is quantized
    to j/k. Leave both 0 for the shipped default (unanimity at whatever k is). k must be >= 2:
    one pass agrees with itself, so k=1 would assert agreement nobody measured.
    """
    rec = ExtractedRecord.model_validate(record)
    weights = DEFAULT_WEIGHTS.get(weights_key, DEFAULT_WEIGHTS["numeric"])
    # ensemble_min_agreeing=0 means "unset" -> unanimity at whatever k is, so raising k
    # tightens the bar instead of silently loosening it to a majority.
    min_agreement = (required_agreement(ensemble_k, ensemble_min_agreeing or None)
                     if ensemble_k else 1.0)
    # `ensemble_k=0` is the contract's own way of saying "single pass, no agreement measured"
    # (D-095). Routing must then decide without it — otherwise every field routes to
    # human_review and a k=1 run writes nothing, which is exactly what was measured. What keeps
    # this honest is `gate.single_pass_problems`, which refuses a single-pass configuration that
    # has not set a completeness minimum in agreement's place.
    require_ensemble = bool(ensemble_k)
    lane = set(review_lane or ())
    field_confs = []
    for fv in rec.fields:
        c = fv.confidence_components
        if c is not None:
            try:
                fv.confidence = c.composite(weights)
            except ValueError:
                fv.confidence = None
        fv.route = default_route(fv, min_agreement, require_ensemble)
        # A ratified review-lane field is scored and routed like any other — its confidence
        # stays visible — but it is EXCLUDED from the record composite, because the composite is
        # the weakest link among the fields the record will actually be written with. A field
        # the researcher has already ruled can never auto-accept is not one of those, and
        # including it makes the record's score a measure of the field we agreed to hold.
        if fv.field_name not in lane:
            field_confs.append(fv.confidence if fv.confidence is not None else 0.0)

    if not rec.fields:
        rec.route = RouteDecision.quarantine
        rec.failure_reason = FailureReason.incoherent
    composite = min(field_confs) if field_confs else 0.0
    out = json.loads(rec.model_dump_json())
    out["_composite_confidence"] = composite
    out["_review_lane"] = sorted(lane & {fv.field_name for fv in rec.fields})
    out["_routing_summary"] = {
        r.value: sum(1 for fv in rec.fields if fv.route == r) for r in RouteDecision
    }
    return out
