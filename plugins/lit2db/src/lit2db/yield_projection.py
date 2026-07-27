"""What fraction of records will actually auto-accept — and, when the answer is bad, WHY.

A run that writes almost nothing is a legitimate result: the gate refusing is the thesis working.
But it is indistinguishable, from the outside, from a schema whose fields cannot be verified. The
difference is *which field blocked*, and nothing reported that until this module existed.

The projection is cheap and can run on any scored records you already have, so the yield question
gets answered BEFORE committing a corpus-sized budget rather than after. Run against the single
terpenoid end-to-end artifact it produced, at zero token cost:

    projected auto-accept 2/9 (22%)
    product          blocks 5/9      <- the binding constraint
    enzyme_name      blocks 4/9
    source_organism  blocks 4/9
    substrate        blocks 2/9

That inverted the working assumption. Excusing prose `function` to the review lane is what lifts
the yield off zero — but the field that blocks most records is `product`, a NAMED ENTITY, exactly
the type predicted to be safe. Promiscuous enzymes make it multi-valued, so k passes must agree on
a SET, and set agreement under unanimity is strictly harder than scalar agreement. Modelling the
domain correctly made the field harder to auto-accept. That is a real trade, and the point of this
module is that you can see it before you pay for it.

Domain-INVARIANT: field names are opaque labels. The review lane comes from the ratified spec
(`FieldSpec.auto_acceptable`), never from a guess about what a field means.

Deliberately STDLIB-ONLY, like `gate` and `accounting`.
"""
from __future__ import annotations

from collections import Counter

# The signals a field must clear to be eligible for auto-accept. Both are mechanical: grounding
# is lexical, agreement is computed across passes and never judged (D-035).
REQUIRED_SIGNALS = ("c_grounded", "c_ensemble")


def review_lane_from_spec(spec) -> set:
    """Field names the ratified spec marks as human-review output by design.

    Accepts a SchemaReadySpec or a plain dict, so this works before and after validation.
    """
    fields = getattr(spec, "fields", None)
    if fields is None and isinstance(spec, dict):
        fields = spec.get("fields", [])
    out = set()
    for f in fields or []:
        name = getattr(f, "name", None) if not isinstance(f, dict) else f.get("name")
        ok = getattr(f, "auto_acceptable", None) if not isinstance(f, dict) \
            else f.get("auto_acceptable", True)
        if name and ok is False:
            out.add(name)
    return out


def optional_fields_from_spec(spec) -> set:
    """Fields the ratified spec marks `required: False`.

    Absent, they are excused; present, they clear the bar like anything else. Without this, a
    record is blocked by the ABSENCE of a field the spec itself calls optional — which is not the
    gate working, it is the gate misreading the schema.
    """
    fields = getattr(spec, "fields", None)
    if fields is None and isinstance(spec, dict):
        fields = spec.get("fields", [])
    out = set()
    for f in fields or []:
        name = getattr(f, "name", None) if not isinstance(f, dict) else f.get("name")
        req = getattr(f, "required", None) if not isinstance(f, dict) else f.get("required", True)
        if name and req is False:
            out.add(name)
    return out


def _is_absent(field_value: dict) -> bool:
    v = field_value.get("value")
    return v is None or v == "" or v == [] or v == {}


def _clears(field_value: dict, bar: float) -> bool:
    cc = field_value.get("confidence_components") or {}
    return all(cc.get(sig) is not None and cc[sig] >= bar for sig in REQUIRED_SIGNALS)


def project(records, *, review_lane=(), optional=(), bar: float = 1.0) -> dict:
    """Project the auto-accept yield over already-scored records.

    `review_lane` — fields excused by design (see `review_lane_from_spec`). They are reported
    separately, never counted as blockers: a prose field routing to review is the schema working.

    `bar` — the level each required signal must reach. 1.0 is unanimity + full grounding, the
    shipped default; lowering it is a ratified setting (D-034), not a runtime convenience.
    """
    review_lane, optional = set(review_lane), set(optional)
    per_record, blockers, review_hits = [], Counter(), Counter()
    absent_optional = Counter()
    n_auto = 0

    for r in records:
        blocking = []
        for fv in r.get("fields", []):
            name = fv.get("field_name")
            if name in review_lane:
                if not _clears(fv, bar):
                    review_hits[name] += 1
                continue
            if name in optional and _is_absent(fv):
                absent_optional[name] += 1     # excused: the spec calls it optional
                continue
            if not _clears(fv, bar):
                blocking.append(name)
        auto = not blocking
        n_auto += auto
        for b in blocking:
            blockers[b] += 1
        per_record.append({"record_id": r.get("record_id"), "auto_accept": auto,
                           "blocked_by": blocking})

    n = len(per_record)
    return {
        "n_records": n,
        "n_auto_accept": n_auto,
        "yield_fraction": (n_auto / n) if n else 0.0,
        "bar": bar,
        "review_lane": sorted(review_lane),
        # Ranked: the top entry is what to fix, or what to re-ratify the bar for.
        "blocking_fields": blockers.most_common(),
        "review_lane_routed": review_hits.most_common(),
        "optional_absent": absent_optional.most_common(),
        "per_record": per_record,
    }


def explain(projection: dict) -> str:
    """A few lines an operator or a run manifest can print verbatim."""
    n, a = projection["n_records"], projection["n_auto_accept"]
    pct = 100 * projection["yield_fraction"]
    lines = [f"projected auto-accept {a}/{n} ({pct:.0f}%) at bar={projection['bar']}"]
    if projection["blocking_fields"]:
        lines.append("blocked by:")
        for name, count in projection["blocking_fields"]:
            mark = "  <- binding constraint" if count == projection["blocking_fields"][0][1] else ""
            lines.append(f"  {name:<24} blocks {count}/{n}{mark}")
    if projection["review_lane"]:
        routed = dict(projection["review_lane_routed"])
        lines.append("review lane (excused by design, not failures): "
                     + ", ".join(f"{f}({routed.get(f, 0)})" for f in projection["review_lane"]))
    if n and a == 0:
        lines.append("YIELD IS ZERO — before spending a corpus budget, decide whether this is the "
                     "gate working or a field that cannot be verified. The ranking above says "
                     "which field to interrogate first.")
    return "\n".join(lines)
