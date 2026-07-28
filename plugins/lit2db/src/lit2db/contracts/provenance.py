"""Unified provenance record and evidence tier.

Formalizes blueprint sections 3 (source-adapter contract / unified provenance record)
and 4 step 6 (evidence-tier ordinal). Domain-INVARIANT: no field here encodes domain
substance. Every value in the output database carries a ProvenanceRecord.
"""
from __future__ import annotations
import hashlib
import re
from datetime import datetime
from enum import Enum
from typing import Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def process_fingerprint(template: str) -> str:
    """SHA256 of the exact text that produced a value — a prompt template, or a mapping spec.

    Computed from the template at load, never written by hand. Because the digest derives from
    the text itself, **the prompt cannot change without the fingerprint changing** — which is the
    whole point, and is why `_ProvCommon.process_fingerprint` refuses anything that is not a real
    SHA256 rather than trusting a version string somebody remembered to bump.

    This is D-033's rule one level up. A corpus is defined by its query, not its name; an
    extraction is defined by its prompt, not by `"extractor@0.9.0"`. Two runs whose
    `producing_process` strings match can still have used different instructions, and today
    nothing in the record would show it. Ported from RAW's constraints #11/#12.
    """
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


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
    # RECORDED, NEVER SCORED (D-079). The adversarial judge is a VETO applied after selection,
    # not a term in this mean — see `ExtractedRecord.judge_verdict`, which is where a verdict
    # belongs now. The field survives so artifacts written before v0.32.0 still validate and the
    # value stays readable in an audit; nothing computes with it. Different MODEL, same family by
    # default (D-041) — a gap measured under this wiring is a LOWER BOUND, never "cross-family".
    c_judge: Optional[float] = None
    c_verbal: Optional[float] = None       # model verbalized confidence
    c_consistency: Optional[float] = None  # 1 - normalized self-consistency variance

    def composite(self, weights: dict[str, float]) -> float:
        """Weighted mean over PRESENT signals, renormalized. weights come from the
        gold-set-calibrated, domain-ratified weight vector (see instantiation/).

        Refuses a weight for `c_judge`. Measured before D-079: at the 0.95 bar a unanimous,
        fully-grounded record scored 1.000 unjudged and 1.000 judged-supported, so the judge
        could only ever LOWER the number — it was a veto wearing a weight, and 139 of 165 judge
        calls could not have changed any outcome. A project may override these weights from its
        instantiation, so "do not put it back" has to be enforced rather than documented: this
        project's recurring finding is that the failure happens exactly where the check isn't.
        """
        if "c_judge" in weights:
            raise ValueError(
                "c_judge is not a scored signal (D-079): the adversarial judge is a VETO "
                "applied after selection, recorded as ExtractedRecord.judge_verdict and "
                "enforced in lit2db.gate.judge_veto_reasons. Weighting it inside a mean lets a "
                "confident grounding score average away a refusal. Remove it from the weight "
                "vector; the remaining weights renormalize over present signals on their own.")
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
    # SHA256 of the prompt template (literature) or mapping spec (structured) — see
    # `process_fingerprint`. `producing_process` above is a NAME and names drift silently;
    # this is the executable thing itself. Optional so existing records stay valid, but a
    # value that is present must be a real digest: hand-bumping is refused structurally.
    process_fingerprint: Optional[str] = None
    source_status: SourceStatus = SourceStatus.active
    source_status_checked_at: Optional[datetime] = None
    confidence: Optional[float] = None
    confidence_components: Optional[ConfidenceComponents] = None

    @field_validator("process_fingerprint")
    @classmethod
    def _must_be_a_real_digest(cls, v):
        """Reject `"v2"`, `"prompt-1.3"`, or a truncated hash.

        The field only means anything if it was computed. Accepting a hand-written label would
        reproduce exactly the defect it exists to remove — a provenance string that looks
        precise, is trusted, and can be updated without the thing it describes changing.
        """
        if v is None:
            return v
        v = v.strip().lower()
        if not _SHA256.match(v):
            raise ValueError(
                f"process_fingerprint must be a SHA256 hex digest computed from the prompt "
                f"template (see process_fingerprint()), got {v[:24]!r}. A hand-written version "
                "label is the thing this field exists to replace.")
        return v


class LiteratureProvenance(_ProvCommon):
    kind: Literal["literature"] = "literature"
    doi: Optional[str] = None
    section: Optional[str] = None
    verbatim_quote: str               # load-bearing: every value carries its quote
    char_offset: int                  # load-bearing: disambiguates repeated entities

    # --- D-038: how much of the source was actually READ ---------------------------
    # Extraction reads the full source. Where a run truncates, the retained span is
    # RECORDED here; an unstated truncation default is forbidden. This is not hygiene:
    # a value grounded against 26% of a paper is not weakly verified, it is verified
    # against a DIFFERENT DOCUMENT -- and counter-evidence, by construction, most often
    # lives in the part that was cut. The BBB pilot sent `text[:12000]` (a measured 26.2%
    # of the mean source) with no trace of it in any output; a reader of that database
    # could not tell that three quarters of every paper was never seen.
    # Offsets are in the store's coordinate system -- `full.txt` (see store.py).
    source_chars_total: Optional[int] = Field(default=None, ge=0)
    source_chars_read: Optional[int] = Field(default=None, ge=0)

    @property
    def retained_fraction(self) -> Optional[float]:
        """Fraction of the source the extractor actually saw; None if not recorded."""
        if self.source_chars_total is None or self.source_chars_read is None:
            return None
        if self.source_chars_total == 0:
            return 0.0
        return min(1.0, self.source_chars_read / self.source_chars_total)

    @model_validator(mode="after")
    def _retention_is_coherent(self):
        """Both retention figures or neither, and no quote may cite unread text.

        The second rule is the load-bearing one. If `char_offset` lands past
        `source_chars_read`, the record cites a span the extractor never received --
        the offset is wrong, or the quote was fabricated. Either way the provenance is
        internally inconsistent and must not validate. This is the check that would
        have caught the BBB truncation class at write time rather than at audit time.
        """
        total, read = self.source_chars_total, self.source_chars_read
        if (total is None) != (read is None):
            raise ValueError(
                "source_chars_total and source_chars_read must be recorded together — "
                "a retained count without its denominator states nothing (D-038)")
        if total is not None and read > total:
            raise ValueError(
                f"source_chars_read ({read}) exceeds source_chars_total ({total})")
        if read is not None and self.char_offset >= read:
            raise ValueError(
                f"char_offset {self.char_offset} lies beyond the {read} chars actually "
                "read from the source: this value cites text the extractor never saw")
        return self


class StructuredProvenance(_ProvCommon):
    kind: Literal["structured"] = "structured"
    database: str
    record_id: str
    db_version: str                   # load-bearing: unpinned value is unreproducible
    snapshot_date: Optional[str] = None
    source_query: Optional[str] = None
    source_field_name: Optional[str] = None


ProvenanceRecord = Union[LiteratureProvenance, StructuredProvenance]
