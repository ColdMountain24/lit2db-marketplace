"""Unified provenance record and evidence tier.

Formalizes blueprint sections 3 (source-adapter contract / unified provenance record)
and 4 step 6 (evidence-tier ordinal). Domain-INVARIANT: no field here encodes domain
substance. Every value in the output database carries a ProvenanceRecord.
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Literal, Optional, Union
from pydantic import BaseModel, Field


# --- Source status (ratified addition D2: retraction / supersession) --------------
class SourceStatus(str, Enum):
    active = "active"
    retracted = "retracted"
    superseded = "superseded"
    corrected = "corrected"


# --- Evidence tier: a multidimensional ordinal (blueprint 4.6), NOT a single bool --
# Values are ordinal labels; the domain instantiation ratifies the exact vocabularies.
class StudyDesign(str, Enum):
    measured = "measured"          # wet-lab / experimental measurement
    predicted = "predicted"        # computational / in-silico  -- NEVER pooled with measured
    simulated = "simulated"
    categorical = "categorical"
    unknown = "unknown"


class EvidenceTier(BaseModel):
    """The six-dimension ordinal. Kept in lockstep with protocol Step C axis 3."""
    study_design: StudyDesign
    directness: Literal["direct", "indirect", "unknown"] = "unknown"
    consistency: Literal["consistent", "inconsistent", "single", "unknown"] = "unknown"
    risk_of_bias: Literal["low", "some", "high", "unknown"] = "unknown"
    effect_direction: Literal["positive", "negative", "null", "na"] = "na"
    certainty: Literal["high", "moderate", "low", "very_low", "unknown"] = "unknown"


# --- Confidence composite (blueprint 5.2) ----------------------------------------
class ConfidenceComponents(BaseModel):
    """Per-field signals. Any may be None; the composite degrades gracefully
    (blueprint 5.2 graceful-degradation clause). c_logprob is None for black-box models."""
    c_logprob: Optional[float] = None      # mean token logprob (gray-box only)
    c_ensemble: Optional[float] = None     # agreement fraction across ensemble
    c_grounded: Optional[float] = None     # entailment (lit) or mapping-validation pass (structured)
    c_judge: Optional[float] = None        # adversarial judge pass, different family
    c_verbal: Optional[float] = None       # model verbalized confidence
    c_consistency: Optional[float] = None  # 1 - normalized self-consistency variance

    def composite(self, weights: dict[str, float]) -> float:
        """Weighted mean over PRESENT signals, renormalized. weights come from the
        gold-set-calibrated, domain-ratified weight vector (see instantiation/)."""
        num = den = 0.0
        for k, w in weights.items():
            v = getattr(self, k, None)
            if v is not None:
                num += w * v
                den += w
        if den == 0.0:
            raise ValueError("no confidence signals present; cannot form composite")
        return num / den


# --- Unified provenance record (blueprint 3), discriminated by source type --------
class _ProvCommon(BaseModel):
    source_id: str
    retrieval_timestamp: datetime
    producing_process: str            # extracting model+version, or mapping-spec+version
    source_status: SourceStatus = SourceStatus.active
    source_status_checked_at: Optional[datetime] = None
    confidence: Optional[float] = None
    confidence_components: Optional[ConfidenceComponents] = None


class LiteratureProvenance(_ProvCommon):
    kind: Literal["literature"] = "literature"
    doi: Optional[str] = None
    section: Optional[str] = None
    verbatim_quote: str               # load-bearing: every value carries its quote
    char_offset: int                  # load-bearing: disambiguates repeated entities


class StructuredProvenance(_ProvCommon):
    kind: Literal["structured"] = "structured"
    database: str
    record_id: str
    db_version: str                   # load-bearing: unpinned value is unreproducible
    snapshot_date: Optional[str] = None
    source_query: Optional[str] = None
    source_field_name: Optional[str] = None


ProvenanceRecord = Union[LiteratureProvenance, StructuredProvenance]
