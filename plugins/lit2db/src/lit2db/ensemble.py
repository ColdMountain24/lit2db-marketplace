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
    # The dash family below cost a real entity. One paper typeset
    # "neodolabella‐1(14),2,7‐triene" with U+2010 HYPHEN where another mention used plain
    # U+002D, so the same enzyme aligned as two and produced two database rows. NFKC does not
    # fold these — they are distinct characters, not compatibility forms — so nothing upstream
    # collapses them. This is squarely what normalization is for (D-035): the difference is
    # typographic, not semantic content.
    "‐": "-",   # HYPHEN
    "‑": "-",   # NON-BREAKING HYPHEN
    "‒": "-",   # FIGURE DASH
    "­": "",    # SOFT HYPHEN — invisible, and survives extraction out of PDFs
    "’": "'",   # RIGHT SINGLE QUOTATION MARK
    "‘": "'",   # LEFT SINGLE QUOTATION MARK
    "“": '"',   # LEFT DOUBLE QUOTATION MARK
    "”": '"',   # RIGHT DOUBLE QUOTATION MARK
    "′": "'",   # PRIME
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

    **Absence is a dissenting vote, never a candidate.** A missing value stays in the
    denominator — a value 1 of 3 passes proposed scores 1/3 — but it can never BE the modal.
    Letting it win the vote would make a value that two passes missed resolve to "the
    ensemble agrees there is nothing here", and the proposal would then vanish instead of
    reaching a human. One pass finding a compound the others missed is exactly the signal
    worth surfacing, not the one worth deleting.
    """
    k = len(values)
    if k == 0:
        return {"c_ensemble": None, "k": 0, "n_agreeing": 0, "modal_value": None,
                "ambiguous_modal": False, "groups": [], "n_missing": 0}

    vals = list(values)
    if expand_binomials:
        strs = [str(v) for v in vals if v is not None]
        vals = [(_expand_binomial(str(v), strs) if v is not None else None) for v in vals]

    present = [v for v in vals if v is not None]
    n_missing = k - len(present)
    if not present:
        return {"c_ensemble": None, "k": k, "n_agreeing": 0, "modal_value": None,
                "ambiguous_modal": False, "groups": [], "n_missing": n_missing}

    groups: list[dict] = []
    for v in present:
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
        "c_ensemble": top / k,          # denominator is k, so absences still cost
        "k": k,
        "n_agreeing": top,
        "n_missing": n_missing,
        "modal_value": None if ambiguous else groups[0]["representative"],
        "ambiguous_modal": ambiguous,
        "groups": [{"value": g["representative"], "n": len(g["members"])} for g in groups],
    }


def agreement_elementwise(passes: list, rel_tol: float = DEFAULT_REL_TOL, steps=DEFAULT_STEPS,
                          synonyms: Optional[dict] = None,
                          expand_binomials: bool = False) -> dict:
    """Agreement for a MULTI-VALUED field, judged per element rather than per set.

    Set-equality unanimity is the wrong bar for a field that is legitimately a list. A promiscuous
    enzyme yields several products; one pass spotting an extra trace peak makes the sets differ,
    and under set-equality that single extra element destroys the whole record — including the
    products all k passes agreed on. Measured on the terpenoid end-to-end run, `product` blocked
    5 of 9 records that way, more than any other field, despite being a NAMED ENTITY and so
    exactly the type predicted to be safe.

    So each element votes on its own:

      * an element ALL k passes proposed is unanimous and joins the auto-acceptable **core**
      * an element only some passes proposed is **deferred** to review, individually, and does
        not block the core

    **The guarantee is unchanged.** Nothing auto-accepts on less than unanimity — the bar moved
    from the set to the element, it did not drop. What changes is that a partial finding now
    reaches a human as a partial finding, instead of silently taking a whole record down with it,
    which is the same principle as absence never winning the modal vote in `agreement`.

    `c_ensemble` describes the CORE, so it is 1.0 when the core is non-empty. When no element is
    unanimous there is no core, and it falls back to the best element's fraction — which is below
    any sane bar, so the field fails, correctly.

    Returns `{c_ensemble, k, core, deferred, n_missing, empty_core}`.
    """
    k = len(passes)
    if k == 0:
        return {"c_ensemble": None, "k": 0, "core": [], "deferred": [],
                "n_missing": 0, "empty_core": True}

    def _as_list(p):
        if p is None:
            return None
        return list(p) if isinstance(p, (list, tuple, set)) else [p]

    lists = [_as_list(p) for p in passes]
    n_missing = sum(1 for p in lists if p is None)
    present = [p for p in lists if p is not None]
    if not present:
        return {"c_ensemble": None, "k": k, "core": [], "deferred": [],
                "n_missing": n_missing, "empty_core": True}

    # Group elements across passes against a representative, never chained — the same rule
    # `agreement` uses, and for the same reason: numeric tolerance is not transitive.
    buckets: list[dict] = []
    for pass_idx, plist in enumerate(lists):
        if plist is None:
            continue
        seen_this_pass = set()
        for v in plist:
            if v is None:
                continue
            for b in buckets:
                if values_agree(b["representative"], v, rel_tol, steps, synonyms):
                    target = b
                    break
            else:
                target = {"representative": v, "passes": set()}
                buckets.append(target)
            # A pass proposing the same element twice votes once.
            key = id(target)
            if key not in seen_this_pass:
                target["passes"].add(pass_idx)
                seen_this_pass.add(key)

    core, deferred = [], []
    for b in buckets:
        n = len(b["passes"])
        if n == k:
            core.append(b["representative"])
        else:
            deferred.append({"value": b["representative"], "n_agreeing": n,
                             "c_ensemble": n / k})
    deferred.sort(key=lambda d: (-d["n_agreeing"], str(d["value"])))

    best = max((len(b["passes"]) for b in buckets), default=0)
    return {
        "c_ensemble": 1.0 if core else (best / k if k else None),
        "k": k,
        "core": core,                  # auto-acceptable: every pass found these
        "deferred": deferred,          # to human review, individually
        "n_missing": n_missing,
        "empty_core": not core,
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


# --- merging k passes into one record -----------------------------------------------------
# Everything above compares values for ONE field. This merges whole passes, and the hard part
# is not comparison — it is ALIGNMENT. Three independent passes over one paper may find five
# compounds, four, and six. Before any field can be compared, you have to know which record in
# pass A is the same entity as which record in pass B, and getting that wrong silently compares
# the kcat of one enzyme against the kcat of a different one.
#
# Records are therefore aligned on the entity's IDENTITY FIELD — Stage-0.5 axis 5, "the
# canonical id that resolves two records to the same entity". Which field that is, is ratified
# substance and comes from the frozen spec; this module never guesses it.
#
# The key move: **a record a pass did not find is a missing VALUE for every one of its
# fields.** That folds record-level disagreement into the field-level machinery already built
# — `values_agree` treats None as agreeing only with None — so a compound only 1 of 3 passes
# saw cannot reach an agreement above 1/3 on any of its fields, and cannot auto-accept. No
# separate record-level score is needed, and none can drift out of sync with the field-level one.

def _fields_of(rec: dict) -> dict:
    return {f.get("field_name"): f for f in (rec.get("fields") or []) if isinstance(f, dict)}


def _id_spec(spec) -> dict:
    """Normalize a ratified identity declaration into {chain, ordinal_within}.

    Three accepted shapes, because the ratified rule is a FALLBACK CHAIN and a bare field name
    could not express it:

        "accession"                                     one field (back-compatible)
        ["accession", ["genus_species", "enzyme_name"]] chain; a level may be composite
        {"chain": [...], "ordinal_within": [...]}       chain + a tiebreak scope

    Terpenoid's T4 — *accession, else (genus_species + enzyme_name)* — is the middle form.
    """
    if spec is None:
        return {"chain": [], "ordinal_within": []}
    if isinstance(spec, str):
        return {"chain": [spec], "ordinal_within": []}
    if isinstance(spec, dict):
        return {"chain": list(spec.get("chain") or []),
                "ordinal_within": list(spec.get("ordinal_within") or [])}
    return {"chain": list(spec), "ordinal_within": []}


def _level_value(rec: dict, level, steps) -> Optional[str]:
    """One level of the chain. A composite level resolves only if EVERY part is present —
    a half-filled composite key would collide with every other half-filled one."""
    fields = _fields_of(rec)
    parts = [level] if isinstance(level, str) else list(level)
    out = []
    for name in parts:
        f = fields.get(name)
        v = normalize(f.get("value"), steps) if f else None
        if not v:
            return None
        out.append(v)
    return "|".join(out)


def _identity(rec: dict, spec, steps, ordinal: Optional[int] = None) -> Optional[tuple]:
    """Returns (identity, tier) where tier names WHICH rule resolved it, or None.

    The tier travels with the record because the rules are not equally trustworthy. Accession
    aligned 15/15 in the D-058 diagnostic where the name fallback aligned 0/15, and `ordinal`
    is weaker still — it is positional, and position is not guaranteed stable across
    independent passes. Anything aligned by ordinal is alignment the ensemble cannot vouch for,
    so it is labelled rather than blended into the same number as an accession match.
    """
    s = _id_spec(spec)
    for i, level in enumerate(s["chain"]):
        v = _level_value(rec, level, steps)
        if v:
            tier = "primary" if i == 0 else f"fallback{i}"
            return (v, tier)
    if ordinal is not None and s["ordinal_within"]:
        scope = _level_value(rec, s["ordinal_within"], steps)
        if scope:
            return (f"{scope}#ord{ordinal}", "ordinal")
    return None


def merge_passes(passes: list, identity_fields: Optional[dict] = None,
                 rel_tol: float = DEFAULT_REL_TOL, steps=DEFAULT_STEPS,
                 synonyms: Optional[dict] = None, expand_binomials: bool = False) -> dict:
    """Merge k extraction passes into one record set carrying ensemble signals.

    `passes` is k lists of ExtractedRecord-shaped dicts, one list per pass.
    `identity_fields` maps entity_type -> the field name that identifies the entity
    (e.g. {"compound": "compound_name"}). Ratified per project; never inferred here.

    Returns `{records, ensemble, k, alignment}` — merged records with `c_ensemble` /
    `c_consistency` filled in, plus a per-field agreement report for the review queue. The
    report is returned ALONGSIDE rather than stuffed into the record, because `FieldValue` is
    a frozen contract and widening it to carry debug detail is exactly the kind of quiet
    schema growth the ratification invariant exists to prevent.

    Works on plain dicts so this module stays stdlib-only, like `lit2db.gate`.
    """
    k = len(passes)
    if k == 0:
        raise ValueError("merge_passes needs at least one pass")
    identity_fields = identity_fields or {}

    # Index every pass by (entity_type, normalized identity).
    indexed, keys, id_tiers = [], [], {}
    for records in passes:
        idx = {}
        # Ordinal counts ONLY the records the chain could not identify, in source order within
        # the scope — so the nth *unnamed* enzyme of an organism lines up with the nth unnamed
        # one in another pass. Counting every record in scope instead makes the numbering
        # depend on how many named records happened to precede it, which differs per pass:
        # measured on PMC12723471, that produced 5 ordinal keys where 3 exist, 4 of them
        # matched by a single pass. Ordinal is the weakest rule here even when correct, and it
        # is labelled as such downstream so a disagreement under it can be read as a possible
        # mis-pairing rather than as evidence.
        seen_in_scope: dict = {}
        for rec in records or []:
            etype = rec.get("entity_type")
            spec = identity_fields.get(etype)
            resolved = _identity(rec, spec, steps)          # chain only, no ordinal yet
            if resolved is None:
                s = _id_spec(spec)
                scope = _level_value(rec, s["ordinal_within"], steps) \
                    if s["ordinal_within"] else None
                if scope is not None:
                    ordinal = seen_in_scope.get((etype, scope), 0)
                    seen_in_scope[(etype, scope)] = ordinal + 1
                    resolved = _identity(rec, spec, steps, ordinal=ordinal)
            ident = resolved[0] if resolved else None
            if resolved:
                id_tiers[(etype, ident)] = resolved[1]
            if ident is None:
                # No identity field ratified for this type. Positional alignment across
                # INDEPENDENT passes would be a coin flip, so it is only safe when the type
                # is single-record per pass — the one-row-per-paper case.
                same = [r for r in records if r.get("entity_type") == etype]
                if len(same) > 1:
                    raise ValueError(
                        f"entity_type {etype!r} has {len(same)} records in one pass but no "
                        f"identity field. Ratify one (Stage-0.5 axis 5) — aligning records "
                        f"across independent passes by position would compare different "
                        f"entities to each other.")
                ident = "(singleton)"
            key = (etype, ident)
            idx[key] = rec
            if key not in keys:
                keys.append(key)
        indexed.append(idx)

    merged, report = [], {}
    for key in keys:
        etype, ident = key
        present = [idx.get(key) for idx in indexed]
        field_names = []
        for rec in present:
            for name in _fields_of(rec or {}):
                if name not in field_names:
                    field_names.append(name)

        out_fields = {}
        for name in field_names:
            per_pass = [(_fields_of(rec or {}).get(name) or {}) for rec in present]
            values = [f.get("value") if f else None for f in per_pass]
            summ = summarize(values, rel_tol=rel_tol, steps=steps, synonyms=synonyms,
                             expand_binomials=expand_binomials)

            # Provenance must come from a pass that actually produced the surviving value —
            # pairing the modal value with another pass's quote would manufacture evidence.
            chosen = None
            for f, v in zip(per_pass, values):
                if v is None or not f:
                    continue
                if summ["ambiguous_modal"] or values_agree(v, summ["modal_value"],
                                                           rel_tol, steps, synonyms):
                    chosen = f
                    break
            if chosen is None:
                continue                       # no pass produced this field; nothing to emit

            out_fields[name] = {
                **{key_: chosen[key_] for key_ in ("field_name", "value", "provenance",
                                                   "evidence_tier", "is_inferential")
                   if key_ in chosen},
                "confidence_components": {
                    **(chosen.get("confidence_components") or {}),
                    "c_ensemble": summ["c_ensemble"],
                    **({"c_consistency": summ["c_consistency"]}
                       if summ["c_consistency"] is not None else {}),
                },
            }
            report[f"{etype}:{ident}:{name}"] = {
                "n_agreeing": summ["n_agreeing"], "k": k,
                "ambiguous_modal": summ["ambiguous_modal"], "groups": summ["groups"],
                "found_by_passes": sum(1 for r in present if r is not None),
            }

        if out_fields:
            seed = next(r for r in present if r is not None)
            merged.append({"record_id": seed.get("record_id"), "entity_type": etype,
                           "fields": list(out_fields.values())})

    return {"records": merged, "ensemble": report, "k": k,
            "alignment": [{"entity_type": e, "identity": i,
                           "found_by_passes": sum(1 for idx in indexed if (e, i) in idx),
                           # WHICH rule matched these records to each other. Not decoration:
                           # 'ordinal' means they were paired by order of appearance, which is
                           # not guaranteed stable across independent passes, so a disagreement
                           # under it may be a mis-pairing rather than a real disagreement.
                           "identity_tier": id_tiers.get((e, i), "singleton")}
                          for (e, i) in keys],
            "identity_tiers": {t: sum(1 for (e, i) in keys
                                      if id_tiers.get((e, i), "singleton") == t)
                               for t in sorted({*id_tiers.values(), "singleton"})}}
