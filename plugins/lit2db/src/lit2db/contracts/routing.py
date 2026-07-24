"""Routing, verification, and quarantine contracts (blueprint 5 and 6).

Includes the ratified D1 addition: a wholesale-failure quarantine (dead-letter) state
distinct from field-level human review.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from .provenance import ProvenanceRecord, EvidenceTier, ConfidenceComponents


class RouteDecision(str, Enum):
    auto_accept = "auto_accept"
    cheap_repair = "cheap_repair"
    human_review = "human_review"
    quarantine = "quarantine"          # D1: record-level unusability, not field-level doubt


class FailureReason(str, Enum):
    unparseable = "unparseable"
    mapping_invalid = "mapping_invalid"
    incoherent = "incoherent"
    retracted = "retracted"
    superseded = "superseded"


class FieldValue(BaseModel):
    field_name: str
    value: object
    provenance: ProvenanceRecord
    evidence_tier: Optional[EvidenceTier] = None
    confidence: Optional[float] = None
    confidence_components: Optional[ConfidenceComponents] = None
    route: Optional[RouteDecision] = None
    is_inferential: bool = False       # inferential fields get a stricter judge bar


class ExtractedRecord(BaseModel):
    record_id: str
    entity_type: str
    fields: list[FieldValue] = Field(default_factory=list)
    # record-level routing (D1). If quarantined, failure_reason is set.
    route: Optional[RouteDecision] = None
    failure_reason: Optional[FailureReason] = None


# --- Illustrative, non-normative default weights (blueprint 5.2). --------------------
# These are FIRST-CALIBRATION ANCHORS. The domain instantiation overrides them from the
# gold set. c_logprob defaults to 0.0 for the black-box Claude Code reference case.
DEFAULT_WEIGHTS = {
    "numeric": {"c_grounded": 0.35, "c_verbal": 0.20, "c_ensemble": 0.15,
                "c_judge": 0.15, "c_consistency": 0.10, "c_logprob": 0.05},
    "inferential": {"c_judge": 0.30, "c_ensemble": 0.25, "c_grounded": 0.15,
                    "c_verbal": 0.15, "c_consistency": 0.15, "c_logprob": 0.00},
}

# --- Starting routing rules (blueprint 6, "Resolved here"). Calibrate on the gold set.
def default_route(fv: FieldValue) -> RouteDecision:
    """Reference routing logic. Deliberately simple and overridable per project."""
    c = fv.confidence_components
    if c is None:
        return RouteDecision.human_review
    grounded = c.c_grounded if c.c_grounded is not None else 0.0
    judge_pass = (c.c_judge or 0.0) >= 0.5
    ensemble = c.c_ensemble if c.c_ensemble is not None else 0.0
    if judge_pass and ensemble >= 0.999 and grounded >= 0.9:
        return RouteDecision.auto_accept
    if (0.6 <= grounded < 0.9) or (0.0 < ensemble < 0.999):
        return RouteDecision.cheap_repair
    return RouteDecision.human_review
