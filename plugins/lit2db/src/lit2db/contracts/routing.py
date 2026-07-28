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


class JudgeVerdict(str, Enum):
    """The adversarial judge's answer — an ORDINAL STATE, not a probability (D-079).

    Only `supported` clears the gate. The same distinction `ContradictionSearch` draws applies
    here and for the same reason: a record nobody challenged has not passed its challenge.
    `not_run` and `unparseable` are kept apart because they are different facts about the run —
    a stage that never executed is a run to retry, a stage that executed and replied unusably is
    a permanent, auditable denial (retrying it loops forever).
    """
    not_run = "not_run"                # never invoked
    unparseable = "unparseable"        # invoked, replied, no verdict could be read
    supported = "supported"
    partial = "partial"                # the core claim holds, something over-reaches
    unsupported = "unsupported"


# The judge's wire vocabulary is upper-case; the contract's is lower-case. One place to convert.
VERDICT_FROM_WIRE = {"SUPPORTED": JudgeVerdict.supported,
                     "PARTIAL": JudgeVerdict.partial,
                     "UNSUPPORTED": JudgeVerdict.unsupported}


class FieldValue(BaseModel):
    field_name: str
    value: object
    provenance: ProvenanceRecord
    evidence_tier: Optional[EvidenceTier] = None
    confidence: Optional[float] = None
    confidence_components: Optional[ConfidenceComponents] = None
    route: Optional[RouteDecision] = None
    # Marks a value the extractor INFERRED rather than read off the page. It no longer selects a
    # stricter judge bar — since D-079 the veto is uniform, because "the core is right but
    # something over-reaches" disqualifies a mechanical value exactly as it disqualifies an
    # inferred one. It survives as a per-value label the judge prompt and the reviewer both use.
    is_inferential: bool = False
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
    # D-067's record-level review lane needs to say WHY, in words a reviewer can act on, and
    # `failure_reason` is an enum of five machine states that cannot carry that. The pipeline
    # used to join the reasons into `failure_reason` anyway, which raised a ValidationError on
    # EVERY review-lane record — so the lane declared since v0.27.0 had never once survived
    # contact with a record that used it. Found on the first live run whose extractor set
    # `review_only`, where it killed a whole paper and its four other records.
    review_reasons: list[str] = Field(default_factory=list)
    # The adversarial judge's verdict, PER RECORD (D-036: the judge reads a reconstructed claim,
    # which is a property of the record, not of one field). Before D-079 this arrived as a
    # `c_judge` float copied onto every field — a record-level fact wearing a field-level shape,
    # inside a mean that implied it contributed. It defaults to `not_run` so the gate fails
    # CLOSED on a record nobody challenged: silence is not a pass.
    judge_verdict: JudgeVerdict = JudgeVerdict.not_run
    # The judge's weakest-supported-claim / over-reach note, for the human holding the denial.
    # The full reasoning trace stays in the run's `judge/` artifacts; this is the one line a
    # reviewer needs in front of the record itself.
    judge_note: Optional[str] = None


# --- Illustrative, non-normative default weights (blueprint 5.2). --------------------
# These are FIRST-CALIBRATION ANCHORS. The domain instantiation overrides them from the
# gold set. c_logprob defaults to 0.0 for the black-box Claude Code reference case.
#
# `c_judge` IS ABSENT ON PURPOSE (D-079) and `composite()` refuses a weight for it. The
# surviving weights are deliberately NOT re-normalized by hand: `composite()` renormalizes over
# present signals, so deleting a key preserves every remaining ratio exactly. Re-weighting them
# would be calibration, and calibration is the researcher's to ratify, not the scaffold's to
# invent while removing something else.
#
# THE SHIPPED PROFILE IS TWO SIGNALS, because two signals are what the pipeline produces.
#
# It used to declare six, then five. `c_verbal`, `c_consistency` and `c_logprob` were measured
# across 86 records / 670 scored fields and fired on **none** — so 0.35 of the declared weight
# mass, including the second-largest weight, described a contribution that never existed. The
# renormalization over present signals meant the numbers still came out "right", which is exactly
# why it survived: an inert weight is invisible until someone counts.
#
# Weights for signals nothing emits are the same defect as an agent declaring a tool it does not
# hold, and this scaffold has now shipped both. The profile states what runs. Producing the other
# three remains open and is a researcher call — but it will be a change that ADDS a weight
# alongside the code that populates it, which is the only order that cannot lie.
#
# `UNPRODUCED_SIGNALS` is not decoration: `tests/test_declarations.py` asserts nothing here
# weights one of them, so re-adding a weight without the producing code fails the suite.
UNPRODUCED_SIGNALS = ("c_verbal", "c_consistency", "c_logprob")

DEFAULT_WEIGHTS = {
    "numeric": {"c_grounded": 0.35, "c_ensemble": 0.15},
    # Inferential values lean harder on cross-pass agreement: a value the extractor reasoned to
    # rather than read is exactly where independent readings diverge, and where a lexical
    # grounding check is the weakest evidence. Same two signals, different ratio — the ratio is
    # carried over unchanged from the six-signal profile rather than re-invented, because
    # re-weighting is calibration and calibration is the researcher's to ratify.
    "inferential": {"c_grounded": 0.15, "c_ensemble": 0.25},
}

# --- The ensemble agreement bar -------------------------------------------------------
# `c_ensemble` is an agreement FRACTION over k independent extraction passes, so the only
# achievable values are j/k. It is not a continuous score, and a threshold expressed as a
# float invites settings that cannot mean anything: at k=3 anything in (0.667, 1.0] is
# identical to demanding unanimity. So the ratified knob is a PAIR OF INTEGERS — "how many
# of how many passes must agree" — and the fraction is derived. Express a knob in the units
# it can actually take, or operators will tune a dial that isn't connected to anything.
DEFAULT_ENSEMBLE_K = 3          # passes run — overridable per project
DEFAULT_MIN_AGREEING = None     # None == UNANIMITY, and it TRACKS k (see below)
MIN_ENSEMBLE_K = 2              # below this there is no ensemble to agree
_EPS = 1e-9                     # j/k is float-inexact; never compare agreement bare


def required_agreement(k: int = DEFAULT_ENSEMBLE_K,
                       min_agreeing: Optional[int] = DEFAULT_MIN_AGREEING) -> float:
    """The `c_ensemble` value that clears the bar, from the ratified integer pair.

    Unanimity (`min_agreeing is None`) is the shipped default: it is the conservative
    direction, and a dissenting pass is not self-explaining — it may have read a different
    table row, taken the mutant instead of the wild type, or hallucinated, and the fraction
    cannot tell you which. Routing that to a human is the honest response.

    **`None` means unanimity and follows k, rather than pinning an integer.** If the policy
    were stored as the literal `3`, an operator raising k from 3 to 5 for more rigour would
    silently land on 3-of-5 — a bare majority, the opposite of what they asked for. A policy
    must not change meaning because a different setting moved.
    """
    if k < MIN_ENSEMBLE_K:
        raise ValueError(
            f"ensemble_k must be >= {MIN_ENSEMBLE_K}; got {k}. One pass trivially agrees "
            f"with itself, so k=1 yields c_ensemble=1.0 and converts the agreement gate from "
            f"a block into a pass — it would assert agreement that was never measured. To run "
            f"without an ensemble, leave c_ensemble unset instead: an absent signal routes to "
            f"human_review, which fails closed.")
    if min_agreeing is None:
        min_agreeing = k
    if not (1 <= min_agreeing <= k):
        raise ValueError(f"min_agreeing must be in 1..k; got {min_agreeing} of {k}")
    return min_agreeing / k


# --- What values the composite can actually TAKE ---------------------------------------
def achievable_composites(weights: dict[str, float], k: int = DEFAULT_ENSEMBLE_K,
                          grounding: tuple = (0.0, 1.0),
                          signals: tuple = ("c_grounded", "c_ensemble")) -> list[float]:
    """Every distinct composite a field can score, given which signals actually materialize.

    The accept bar has been discussed all project as a continuous dial and has never been one.
    `c_ensemble` is quantized to j/k, so the composite lands on a short LATTICE — and two
    thresholds between adjacent rungs are the same threshold. Under the shipped profile with
    grounding and agreement present the score is `0.7*g + 0.3*e`, and with grounding binary
    that is ten rungs of which only 1.000 clears 0.95.

    Removing `c_judge` coarsened this from steps of 1/13 to steps of 1/10 (D-079, a known and
    accepted consequence). It is a function rather than a comment so a test can assert it: a
    weight change that quietly made the top rung unreachable would auto-accept nothing at all,
    by construction rather than by evidence, and should fail something.

    **`grounding` defaults to binary because that is the case the 1/10 claim is about**, not
    because grounding is always binary. `ground_literature` returns a fraction for a partial
    lexical match, and real runs do contain values like 0.150 and 0.850. Those land BETWEEN the
    rungs — the lattice is a floor on how coarse the score is, never a claim that every score
    sits on it. Pass the grounding values you actually observe to see the real spacing.
    """
    agreement = tuple(j / k for j in range(k + 1))
    grid = {"c_grounded": tuple(grounding), "c_ensemble": agreement}
    present = [s for s in signals if s in weights]
    den = sum(weights[s] for s in present)
    if den == 0.0:
        return []
    out = set()
    for combo in _product(*(grid.get(s, (0.0, 1.0)) for s in present)):
        out.add(round(sum(weights[s] * v for s, v in zip(present, combo)) / den, 10))
    return sorted(out)


def _product(*pools):
    """itertools.product, inlined: this module is imported by stdlib-only consumers."""
    result = [[]]
    for pool in pools:
        result = [x + [y] for x in result for y in pool]
    return [tuple(r) for r in result]


# --- Starting routing rules (blueprint 6, "Resolved here"). Calibrate on the gold set.
def default_route(fv: FieldValue, min_agreement: float = 1.0,
                  require_ensemble: bool = True) -> RouteDecision:
    """Reference routing logic. Deliberately simple and overridable per project.

    `min_agreement` is the ensemble bar, normally derived from the instantiation's ratified
    (k, min_agreeing) pair via `required_agreement`. Default is unanimity.

    **This is SELECTION, and the judge is not part of it (D-079).** Routing asks the two
    mechanical questions the pipeline can answer for itself — is the value in the text, and did
    independent readings agree — and the adversarial judge then vetoes what survives, in
    `lit2db.gate`. Reading the verdict here as well would spend a judge call on every record
    before anything knew which records could be affected by one; measured, 139 of 165 could not.

    Removing it does NOT loosen anything: an unjudged record used to be stopped here (no
    `c_judge` -> `human_review`) and is now stopped at the gate instead, which is where every
    other disqualifying fact already lives — a retracted source, a contradiction from the value's
    own paper. Same denial, stated in the place that denies.
    """
    # A contradiction outranks every confidence signal. If the source itself argues against
    # the value, no amount of grounding or ensemble agreement redeems it — those both measured
    # a span the extractor chose.
    if fv.contradictions:
        return RouteDecision.human_review
    c = fv.confidence_components
    if c is None:
        return RouteDecision.human_review
    grounded = c.c_grounded if c.c_grounded is not None else 0.0
    ensemble = c.c_ensemble if c.c_ensemble is not None else 0.0

    # SINGLE-PASS ROUTING (D-100 / V-001). With one reading there is no agreement to require,
    # and demanding it anyway routed EVERY field to human_review — measured, k=1 wrote zero
    # records across 21 gate settings. This does not make k=1 laxer for free: `require_ensemble`
    # is False only when the operator declared a single-pass run, and `gate.single_pass_problems`
    # REFUSES that configuration unless a completeness minimum is set to take agreement's place.
    # The substitution is structural, not a flag that skips a condition.
    #
    # Measured before it was believed: same 55 papers, same prompt, same conditions, k=1 opus
    # reached 39% precision / 14.7% recall against k=3 mixed's 33% / 13.3%, at a third the cost.
    if not require_ensemble:
        if grounded >= 0.9:
            return RouteDecision.auto_accept
        return (RouteDecision.cheap_repair if grounded >= 0.6
                else RouteDecision.human_review)

    if ensemble >= min_agreement - _EPS and grounded >= 0.9:
        return RouteDecision.auto_accept
    if (0.6 <= grounded < 0.9) or (0.0 < ensemble < min_agreement - _EPS):
        return RouteDecision.cheap_repair
    return RouteDecision.human_review
