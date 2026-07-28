"""Stage 4b — deterministic span grounding. The naive lexical/numeric baseline.

Library code, not server code: `assemble` in the pipeline needs it on every extracted value, and
before this module existed the headless driver reached it by loading the MCP server file as a
module to borrow the function out of it. Grounding is a rule about text; it does not belong to
whichever interface happens to expose it.

Deliberately the SURFACE check. It is the baseline the project measured passing ~100% while true
factual precision was far lower — which is the entire reason the adversarial judge exists.
Semantic support is the judge's call; this answers only "does the value appear in its quote".
"""
from __future__ import annotations

import re


def _norm_num(s: str):
    """Pull the first numeric token out of a string, tolerant of unicode minus / commas."""
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(s).replace("\u2212", "-"))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def ground_literature(value: object, quote: str) -> dict:
    """Deterministic span-grounding for the literature adapter (Stage 4b, naive baseline).

    Does the extracted value actually appear in its verbatim quote — numerically (with a
    small relative tolerance) or as a normalized substring? Returns a c_grounded score in
    [0,1]. This is intentionally the surface check; semantic support is the judge's call.

    **Multi-valued fields ground PER ELEMENT.** A list used to be stringified whole, so
    `["(+)-δ-cadinol"]` was compared as the literal `"['(+)-δ-cadinol']"` and scored 0.0 while
    the identical scalar scored 1.0 — which meant every `list[str]` field in a frozen schema
    was unable to auto-accept, silently, and it read as extractor failure rather than as a
    missing code path. The score is the FRACTION of elements grounded, so a five-product list
    with one unsupported entry lands at 0.8 and routes to repair instead of passing whole or
    failing whole. This mirrors D-052, which already gave the ensemble per-element unanimity;
    grounding simply never got the same treatment.
    """
    q = (quote or "").strip()
    if not q:
        return {"c_grounded": 0.0, "mode": "no_quote"}
    if isinstance(value, (list, tuple)):
        if not value:
            # An empty list is not a grounded value; it is the absence of one.
            return {"c_grounded": 0.0, "mode": "empty_list"}
        per = [_ground_scalar(v, q) for v in value]
        score = sum(p["c_grounded"] for p in per) / len(per)
        mode = ("list_match" if score == 1.0
                else "list_absent" if score == 0.0 else "list_partial")
        return {"c_grounded": score, "mode": mode,
                "n_elements": len(per),
                "ungrounded": [v for v, p in zip(value, per) if p["c_grounded"] < 1.0]}
    return _ground_scalar(value, q)


def _ground_scalar(value: object, q: str) -> dict:
    """One value against one quote. The rule itself, so the list path cannot drift from it."""
    v_num = _norm_num(value)
    if v_num is not None:
        # numeric grounding: any number in the quote within 1% relative tolerance
        nums = [float(x.replace(",", "")) for x in
                re.findall(r"[-+]?\d[\d,]*\.?\d*", q.replace("\u2212", "-"))]
        for n in nums:
            denom = max(abs(v_num), 1e-9)
            if abs(n - v_num) / denom <= 0.01:
                return {"c_grounded": 1.0, "mode": "numeric_match", "matched": n}
        return {"c_grounded": 0.0, "mode": "numeric_absent", "quote_numbers": nums}
    # string grounding: normalized substring
    sv = re.sub(r"\s+", " ", str(value).strip().lower())
    sq = re.sub(r"\s+", " ", q.lower())
    return {"c_grounded": 1.0 if sv and sv in sq else 0.0,
            "mode": "string_match" if sv and sv in sq else "string_absent"}


def validate_mapping(value: object, field_spec: dict) -> dict:
    """Mapping validation for the structured adapter (Stage 4b): type/range/enum conformance.

    field_spec is a FieldSpec-shaped dict (type, valid_range, enum). Passing = c_grounded 1.0.
    A value outside the ratified valid_range is NOT dropped — it is flagged so the researcher
    can recalibrate the bound (the segregate-don't-drop discipline)."""
    ftype = field_spec.get("type")
    reasons = []
    ok = True
    if ftype in ("float", "int"):
        v = _norm_num(value)
        if v is None:
            ok, _ = False, reasons.append("not numeric")
        else:
            vr = field_spec.get("valid_range")
            if vr and not (vr[0] <= v <= vr[1]):
                ok = False
                reasons.append(f"value {v} outside valid_range {tuple(vr)}")
    enum = field_spec.get("enum")
    if enum and str(value) not in enum:
        ok = False
        reasons.append(f"value {value!r} not in enum {enum}")
    return {"c_grounded": 1.0 if ok else 0.0, "ok": ok, "flags": reasons}


# ------------------------------------------------------------------------------------
# Stage 1 — the offset-anchored store (the coordinate system every offset refers to)
# ------------------------------------------------------------------------------------
