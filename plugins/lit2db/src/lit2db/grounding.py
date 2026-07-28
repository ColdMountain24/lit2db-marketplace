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


def _measurement_num(value: object):
    """The value's number, but ONLY when the value IS a number rather than merely contains one.

    `_norm_num` searches anywhere in the string, which sent every name containing a digit down
    the numeric path: `hapalindole 7` grounded happily against "isolated after 7 days of
    culture", because some 7 appeared in the quote. That is a false POSITIVE in the check the
    database's writes depend on, and it was live before the series rule existed -- found by a
    test written for the series rule, on a value shaped like the ones this schema expects
    (an extractor is told to keep a name like `compound 3` when that is all the paper gives).

    The rule: a measurement LEADS with its number (`12.4`, `12.4 s-1`, `-0.5 kcal/mol`); a name
    that happens to contain one does not. Anything not leading with a number is grounded as a
    string, where an exact or series match is required.
    """
    s = str(value).strip().replace("−", "-")
    return _norm_num(s) if re.match(r"^[-+]?\d", s) else None


def _ground_scalar(value: object, q: str) -> dict:
    """One value against one quote. The rule itself, so the list path cannot drift from it."""
    v_num = _measurement_num(value)
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
    if sv and sv in sq:
        return {"c_grounded": 1.0, "mode": "string_match"}
    series = _ground_series(sv, sq)
    if series:
        return series
    return {"c_grounded": 0.0, "mode": "string_absent"}


# A trailing designator: a single letter, or a small integer, optionally primed/subscripted
# (`A`, `B'`, `3`, `A1`). Long trailing tokens are not designators -- they are part of the name.
_DESIGNATOR = r"[a-z](?:['′]|\d{1,2})?|\d{1,3}"
_DASH = r"[-‐‑‒–—−]"
_SEP = rf"\s*(?:,|and|&|/|or|{_DASH}|to)\s*"


def _expand(a: str, b: str) -> list[str]:
    """Expand an inclusive range designator: a-c -> [a,b,c]; 1-3 -> [1,2,3]. [] if not a range."""
    if a.isdigit() and b.isdigit() and int(a) <= int(b) <= int(a) + 50:
        return [str(n) for n in range(int(a), int(b) + 1)]
    if len(a) == 1 and len(b) == 1 and a.isalpha() and b.isalpha() and a <= b:
        return [chr(c) for c in range(ord(a), ord(b) + 1)]
    return []


def _ground_series(sv: str, sq: str) -> dict | None:
    """Does the quote name this value as one member of a SERIES it enumerates?

    Ratified 2026-07-28 after the compound pilot: a source that writes "we propose the trivial
    names corvol ethers A and B" reports corvol ether A, and the strict substring rule scored it
    0.0 because the singular never appears. Five of six correct records in that run died this
    way, all on the identity field -- and series naming is how this literature names new things,
    so it is the common case rather than an edge one.

    **Domain-BLIND by construction.** The rule is structural: a value shaped `STEM DESIGNATOR`
    grounds when the quote contains that stem (optionally pluralised) followed by an enumeration
    that includes the designator, whether written as a list (`A and B`), a range (`A-C`), or a
    mixture. Nothing here knows what a compound is; the same rule holds for `mutant 3` in a quote
    reading `mutants 1-5`.

    Deliberately conservative in three ways, because this LOOSENS a check that guards the
    database: the stem must match on a word boundary and be non-trivial, the enumeration is read
    only from the text immediately following the stem, and a range expands only if it is a real
    ascending range. Returns None (not a score) when it does not apply, so the caller's
    `string_absent` remains the default answer.
    """
    m = re.match(rf"^(.*?)[\s ]*({_DESIGNATOR})$", sv)
    if not m:
        return None
    stem, designator = m.group(1).strip(), m.group(2)
    if len(stem) < 3:
        # Too short to be a name -- "cpd a" style stems would match almost anything.
        return None

    # The stem as printed collectively: pluralised on its final word ("corvol ether" -> "ethers").
    stem_pat = re.escape(stem) + r"e?s?"
    for sm in re.finditer(rf"(?<![a-z0-9]){stem_pat}\b", sq):
        tail = sq[sm.end():sm.end() + 60]
        # Read the enumeration that immediately follows: designators joined by separators.
        run = re.match(rf"^\s*\(?\s*({_DESIGNATOR})((?:{_SEP}(?:{_DESIGNATOR}))*)", tail)
        if not run:
            continue
        listed = re.findall(rf"{_DESIGNATOR}", run.group(0))
        if designator in listed:
            return {"c_grounded": 1.0, "mode": "series_match",
                    "series": run.group(0).strip(), "member": designator}
        # A range: every member between the endpoints is named by it.
        for i in range(len(listed) - 1):
            if re.search(rf"{re.escape(listed[i])}\s*{_DASH}\s*{re.escape(listed[i + 1])}",
                         run.group(0)) and designator in _expand(listed[i], listed[i + 1]):
                return {"c_grounded": 1.0, "mode": "series_range_match",
                        "series": run.group(0).strip(), "member": designator}
    return None


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
