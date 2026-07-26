"""Ensemble agreement — the deterministic half of Stage 3's k extraction passes.

The extractor runs k times and proposes k values for a field. Turning those into
`c_ensemble` is **not** a judgement call, so it does not belong to an agent: it is a
comparison under a stated normalization, and it must give the same answer every time or the
signal it feeds means nothing. This module is that comparison.

## Why normalization comes first (D-035)

Under exact string equality `4.2 != 4.20`, `"4.2 uM" != "4.2 µM"`, and
`"S. coelicolor" != "Streptomyces coelicolor"`. Unanimity would then fail constantly for
formatting reasons unrelated to correctness — filling the review queue with correct values,
and turning the ensemble into a detector of typography rather than of doubt.

The bar the routing rule applies only means something if **dissent implies substance**. That
is this module's whole job: make disagreement mean disagreement.

## The domain boundary

Normalizers here are mechanisms, not content. Case folding, unicode confusables, and numeric
tolerance are domain-invariant. Anything carrying domain knowledge — that two compound names
denote one compound, that a genus abbreviation expands a particular way — is **supplied by
the caller** from the ratified instantiation (`controlled_vocab_bindings`), never baked in.
`abbreviated_binomial` is a structural pattern and ships opt-in for the same reason: a
project ratifies that the convention applies to its entities.

Deliberately STDLIB-ONLY, same constraint as `lit2db.gate` and `lit2db.accounting`.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from typing import Optional

# Default relative tolerance for numeric agreement. Matches the grounding check in the MCP
# server so a value cannot ground against its quote yet "disagree" with an identical value.
DEFAULT_REL_TOL = 0.01

# Confusables that carry no meaning difference in extracted scientific values. Greek mu and
# micro sign are the canonical offender: "µM" and "μM" are visually identical and routinely
# mixed within a single paper.
_CONFUSABLES = {
    "μ": "u",   # GREEK SMALL LETTER MU
    "µ": "u",   # MICRO SIGN
    "−": "-",   # MINUS SIGN
    "–": "-",   # EN DASH
    "—": "-",   # EM DASH
    "’": "'",   # RIGHT SINGLE QUOTATION MARK
    " ": " ",   # NO-BREAK SPACE
}

_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")
_BINOMIAL_RE = re.compile(r"^([A-Za-z])\.\s*(.+)$")

DEFAULT_STEPS = ("unicode", "confusables", "whitespace", "case")


# --- normalizers ------------------------------------------------------------------------
def _n_unicode(s: str) -> str:
    # NFKC folds superscripts, ligatures, and compatibility forms: "s⁻¹" -> "s-1".
    return unicodedata.normalize("NFKC", s)


def _n_confusables(s: str) -> str:
    for bad, good in _CONFUSABLES.items():
        s = s.replace(bad, good)
    return s


def _n_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _n_case(s: str) -> str:
    return s.lower()


def _n_punctuation(s: str) -> str:
    """Drop trailing punctuation and internal hyphenation differences. OPT-IN: for some
    fields a hyphen is meaningful (chemical names), so this is never a default."""
    return re.sub(r"[\s\-_.,;:]+", "", s)


NORMALIZERS = {
    "unicode": _n_unicode,
    "confusables": _n_confusables,
    "whitespace": _n_whitespace,
    "case": _n_case,
    "punctuation": _n_punctuation,
}


def normalize(value, steps=DEFAULT_STEPS) -> str:
    """Apply the named normalizers in order. Unknown names raise rather than silently
    no-op — a typo'd normalizer that quietly does nothing would loosen or tighten the
    agreement bar invisibly."""
    s = "" if value is None else str(value)
    for name in steps:
        fn = NORMALIZERS.get(name)
        if fn is None:
            raise ValueError(f"unknown normalizer {name!r}; known: {sorted(NORMALIZERS)}")
        s = fn(s)
    return s


def as_number(value) -> Optional[float]:
    """The value as a scalar measurement, or None if it is not one.

    DELIBERATELY STRICT, because a false positive here fabricates consensus. A permissive
    "find the first number anywhere" rule reads `2-MIB` and `2-methylisoborneol` as both
    meaning 2.0 and reports two different compounds as unanimous agreement. Two rules
    prevent that:

      * the number must be at the START — `NRRL 12345` is a strain identifier, not 12345;
      * a hyphen bonded directly to the number is a LOCANT (`2-MIB`, `4.2-4.8` as a range),
        not a measurement. Units never attach that way, so `12.4 s-1` and `4.2uM` still parse.

    Anything else falls through to string comparison, which is the fail-closed direction:
    the worst case is a human reviewing a value that two passes actually agreed on.
    """
    if isinstance(value, bool):          # bool is an int subclass; never a measurement
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = _n_whitespace(_n_confusables(str(value)))
    m = _NUM_RE.match(s)                 # match, not search: anchored at the start
    if not m or s[m.end():].startswith("-"):
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _expand_binomial(s: str, others) -> str:
    """Expand 'S. coelicolor' against a longer form seen in the same comparison group.

    Structural, not domain knowledge: it only ever expands an initial to a genus that another
    pass actually proposed. It cannot invent a name, so it can merge two passes that already
    agree in substance but never fabricate agreement between two different organisms.
    """
    m = _BINOMIAL_RE.match(s)
    if not m:
        return s
    initial, rest = m.group(1).lower(), m.group(2).strip()
    for other in others:
        parts = other.split()
        if len(parts) >= 2 and parts[0][:1].lower() == initial and \
                " ".join(parts[1:]).strip() == rest:
            return other
    return s


# --- agreement --------------------------------------------------------------------------
def values_agree(a, b, rel_tol: float = DEFAULT_REL_TOL, steps=DEFAULT_STEPS,
                 synonyms: Optional[dict] = None) -> bool:
    """Do two proposed values mean the same thing?

    Numeric comparison wins when BOTH sides parse as numbers — 4.2 and 4.20 agree, and so do
    4.2 and 4.21 at the default 1% tolerance. When either side is non-numeric the comparison
    is string-based under the named normalizers plus any caller-supplied synonym map.

    A missing value (None) agrees only with another missing value. 'The pass found nothing'
    is a real and different outcome from 'the pass found this' — collapsing them would let a
    field that two passes could not locate look unanimous.
    """
    if a is None or b is None:
        return a is None and b is None
    na, nb = as_number(a), as_number(b)
    if na is not None and nb is not None:
        denom = max(abs(na), abs(nb), 1e-12)
        return abs(na - nb) / denom <= rel_tol
    sa, sb = normalize(a, steps), normalize(b, steps)
    if synonyms:
        syn = {normalize(k, steps): normalize(v, steps) for k, v in synonyms.items()}
        sa, sb = syn.get(sa, sa), syn.get(sb, sb)
    return sa == sb


def agreement(values: list, rel_tol: float = DEFAULT_REL_TOL, steps=DEFAULT_STEPS,
              synonyms: Optional[dict] = None, expand_binomials: bool = False) -> dict:
    """Group k proposed values and report the agreement fraction.

    Returns `{c_ensemble, k, n_agreeing, modal_value, ambiguous_modal, groups}`.

    **Grouping is against a representative, never chained.** Numeric tolerance is not
    transitive — a~b and b~c does not give a~c — so single-link chaining could merge two
    values that plainly disagree via a chain of near-misses. Each value is compared to the
    representative of an existing group only, which keeps the result deterministic and
    order-stable, and makes a group mean "everything here agrees with one value".

    `ambiguous_modal` marks a tie for the largest group: at k=2 with a disagreement, or k=4
    split 2-2, there is no modal value. The fraction is still correct and still below any
    sane bar, but nothing should present a "consensus value" in that case.
    """
    k = len(values)
    if k == 0:
        return {"c_ensemble": None, "k": 0, "n_agreeing": 0, "modal_value": None,
                "ambiguous_modal": False, "groups": []}

    vals = list(values)
    if expand_binomials:
        strs = [str(v) for v in vals if v is not None]
        vals = [(_expand_binomial(str(v), strs) if v is not None else None) for v in vals]

    groups: list[dict] = []
    for v in vals:
        for g in groups:
            if values_agree(g["representative"], v, rel_tol, steps, synonyms):
                g["members"].append(v)
                break
        else:
            groups.append({"representative": v, "members": [v]})

    groups.sort(key=lambda g: len(g["members"]), reverse=True)
    top = len(groups[0]["members"])
    ambiguous = len(groups) > 1 and len(groups[1]["members"]) == top
    return {
        "c_ensemble": top / k,
        "k": k,
        "n_agreeing": top,
        "modal_value": None if ambiguous else groups[0]["representative"],
        "ambiguous_modal": ambiguous,
        "groups": [{"value": g["representative"], "n": len(g["members"])} for g in groups],
    }


def consistency(values: list) -> Optional[float]:
    """`c_consistency` = 1 - coefficient of variation across passes, for NUMERIC fields.

    Distinct from `c_ensemble` in what it sees: agreement is a count of how many passes
    matched, this is how far the dissenters strayed. Two passes reading 12.4 and one reading
    12.5 is a very different fact from one reading 1240, and the agreement fraction is 2/3
    in both cases.

    Returns None when the field is not numeric, when fewer than two passes produced a value,
    or when ANY pass came back empty. That last case matters: two passes reading 12.4 and one
    finding nothing has zero spread among the values that exist, and reporting 1.0 would hand
    the composite a maximal-confidence signal derived from a field a third of the ensemble
    could not locate — penalised once by `c_ensemble` and rewarded once here. A set with holes
    is not consistent, it is incompletely measured, and the composite degrades over PRESENT
    signals precisely so that None can say so.

    Note this shares its input with `c_ensemble`; the two are correlated by construction and
    their weights should be calibrated together, not set independently.
    """
    if not values or any(v is None for v in values):
        return None
    nums = [n for n in (as_number(v) for v in values) if n is not None]
    if len(nums) < 2 or len(nums) != len(values):
        return None
    mean = statistics.fmean(nums)
    if mean == 0:
        return 1.0 if all(n == 0 for n in nums) else 0.0
    cv = statistics.stdev(nums) / abs(mean)
    return max(0.0, min(1.0, 1.0 - cv))


def summarize(values: list, **kw) -> dict:
    """Both ensemble signals for one field, ready to drop into ConfidenceComponents."""
    agr = agreement(values, **{k: v for k, v in kw.items()
                               if k in ("rel_tol", "steps", "synonyms", "expand_binomials")})
    return {"c_ensemble": agr["c_ensemble"], "c_consistency": consistency(values),
            "modal_value": agr["modal_value"], "ambiguous_modal": agr["ambiguous_modal"],
            "k": agr["k"], "n_agreeing": agr["n_agreeing"], "groups": agr["groups"]}
