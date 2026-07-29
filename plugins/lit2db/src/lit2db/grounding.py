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
    interposed = _ground_interposed(sv, sq)
    if interposed:
        return interposed
    return {"c_grounded": 0.0, "mode": "string_absent"}


# How far apart the first and last token may sit and still count as one phrase. Two tokens the
# paper separates with ", strain " are the same phrase; two tokens forty words apart are two
# facts the reader joined. 80 characters is a clause, not a paragraph.
_INTERPOSED_WINDOW = 80


def _ground_interposed(sv: str, sq: str) -> dict | None:
    """Does the quote contain every token of the value, IN ORDER, within one clause?

    Measured 2026-07-29 on 48 denied records whose `species` grounded 0.0: **42 had every token
    present in the quote**, and 36 of those in the paper's own order. The source writes
    `Sandaracinus amylolyticus, strain NOSO-4T` and the record stores `amylolyticus NOSO-4T` —
    the same words in the same order, with the paper's own connective between them. Requiring
    contiguity there is a TYPOGRAPHIC demand, and D-035 already ruled that normalization exists
    so dissent implies substance rather than punctuation.

    **This LOOSENS a write-path check, so it is narrow in four ways**, and each guard is doing
    real work against the same 48 records:

      * IN ORDER. The other 6 records had the tokens reordered — `sp. Cra33g` assembled from
        "strain (Cra33g) belonging to Amycolatopsis". The paper never wrote that phrase; the
        extractor composed it. Those must keep failing, and they do.
      * WITHIN ONE CLAUSE (80 chars), so tokens scattered across a long quote cannot be sewn
        together into a phrase nobody wrote.
      * WORD BOUNDARIES on every token, so `atra` cannot match inside `atratus`.
      * TOKENS OF 2+ CHARACTERS and at least two of them; a single short token is the plain
        substring case, already handled above, and admitting one here would ground almost
        anything.

    Scored 1.0 rather than partial, and that is forced rather than chosen: at k=1 the composite
    IS the grounding score, so anything below the 0.95 accept bar is identical to 0.0. There is
    no partial credit available at this arity — the honest options are "grounded" or "not", and
    the mode name records which rule said so.
    """
    toks = [t for t in re.split(r"[\s,]+", sv) if t]
    if len(toks) < 2 or any(len(t) < 2 for t in toks):
        return None
    pos, cursor = [], 0
    for t in toks:
        m = re.compile(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])").search(sq, cursor)
        if not m:
            return None
        pos.append(m.start())
        cursor = m.end()          # forward-only: this is what enforces the paper's own order
    if pos[-1] - pos[0] > _INTERPOSED_WINDOW:
        return None
    return {"c_grounded": 1.0, "mode": "interposed_match",
            "span": sq[pos[0]:cursor], "tokens": len(toks)}


# A trailing designator: a letter, optionally a small number, optionally a sub-letter after that
# (`A`, `B'`, `3`, `A1`, `B7a`); or a bare small integer. Long trailing tokens are not designators
# -- they are part of the name.
#
# `B7a` was NOT covered until 2026-07-29 and the omission was expensive twice over. Measured on
# `Napyradiomycins A3 (1), B7a (2), B7b (3), and D1 (5)`: `A3` grounded 1.0 and every later member
# grounded 0.0 -- because an unparseable designator does not merely fail for itself, it TERMINATES
# the enumeration scan, so `D1` failed too even though `D1` is a shape the old pattern accepted.
# One unrecognised member silently invalidates the rest of the list.
#
# The trailing sub-letter is admitted only AFTER digits (`b7a`), never on its own (`ab`), so a
# two-letter tail still cannot pose as a designator. This rule LOOSENS a write-path check, and
# that asymmetry is the whole reason to keep it narrow.
_DESIGNATOR = r"[a-z](?:\d{1,2}[a-z]?|['′])?|\d{1,3}"
_DASH = r"[-‐‑‒–—−]"
# `, and ` is ONE separator, not a comma followed by the designator `a` of the word "and".
# Without the optional conjunction after the comma, the scan captured `a` from "and" and then
# stopped, which is why the last member of every Oxford-comma list failed.
_SEP = rf"\s*(?:,\s*(?:and|or|&)?|and|&|/|or|{_DASH}|to)\s*"
# A citation marker sitting INSIDE the enumeration: `A3 (1), B7a (2), ...`. Papers interleave the
# scheme number with the name constantly, and `(` is not a separator, so the scan used to stop at
# the first one. Skipped rather than parsed -- it is punctuation between members, not a member.
_CITE = r"(?:\s*\((?:\d{1,3}[a-z]?|[ivx]{1,4})\))?"


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
        run = re.match(
            rf"^\s*\(?\s*({_DESIGNATOR}){_CITE}((?:{_SEP}(?:{_DESIGNATOR}){_CITE})*)", tail)
        if not run:
            continue
        # Read members from the SEPARATED positions only. `re.findall` over the whole run would
        # also collect the digits inside skipped citation markers -- `(2)` would enter the list
        # as member `2` -- and a member nobody wrote is exactly the fabricated consensus this
        # module exists to prevent.
        # `^[\s(]*`, not a bare `^`: the run begins with the whitespace/paren that followed the
        # stem, so `^` never reached the FIRST member and every list silently lost its opening
        # element — which is how `A3` regressed to 0.0 while `B7a` and `B7b` passed.
        listed = re.findall(rf"(?:^[\s(]*|{_SEP})({_DESIGNATOR}){_CITE}", run.group(0))
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
