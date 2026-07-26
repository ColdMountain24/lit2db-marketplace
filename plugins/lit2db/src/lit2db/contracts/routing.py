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


class ContradictionKind(str, Enum):
    """How a span from the same source undermines an extracted value."""
    conflicting_value = "conflicting_value"    # a different number/label for the same thing
    scope_mismatch = "scope_mismatch"          # measured under conditions the schema excludes
    superseded = "superseded"                  # a later passage corrects or retracts it
    negated = "negated"                        # the claim is asserted then denied
    unmet_condition = "unmet_condition"        # holds only under a caveat not carried through
    other = "other"


class ContradictionEvidence(BaseModel):
    """A span from the SAME source that argues AGAINST the extracted value.

    The grounding quote answers "is this value in the paper?" — which is a question the
    extractor got to choose the evidence for. This answers the one nobody was asking:
    "does the paper, read as a whole, still support it?" Cherry-picking is invisible to any
    check that only inspects the span the extractor selected.
    """
    verbatim_quote: str                # load-bearing: a human must be able to re-read it
    char_offset: int
    kind: ContradictionKind
    explanation: str                   # why this undermines the value, in one sentence


class ContradictionSearch(str, Enum):
    """Whether counter-evidence was looked for — distinct from whether it was found.
    'not_run' is NOT the same as 'clean', and conflating them is how a pipeline claims a
    rigor it never exercised."""
    not_run = "not_run"
    clean = "clean"                    # searched, nothing found — the expected outcome
    found = "found"


class FieldValue(BaseModel):
    field_name: str
    value: object
    provenance: ProvenanceRecord
    evidence_tier: Optional[EvidenceTier] = None
    confidence: Optional[float] = None
    confidence_components: Optional[ConfidenceComponents] = None
    route: Optional[RouteDecision] = None
    is_inferential: bool = False       # inferential fields get a stricter judge bar
    # Counter-evidence (Stage 4c'). Deliberately NOT folded into confidence_components: a
    # weighted mean lets four confident signals average away one real contradiction. A
    # credible contradiction is a BLOCKING condition at the gate, like a retracted source.
    contradiction_search: ContradictionSearch = ContradictionSearch.not_run
    contradictions: list[ContradictionEvidence] = Field(default_factory=list)


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
    # A contradiction outranks every confidence signal. If the source itself argues against
    # the value, no amount of grounding, ensemble agreement, or judge approval redeems it —
    # those all measured a span the extractor chose.
    if fv.contradictions:
        return RouteDecision.human_review
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
