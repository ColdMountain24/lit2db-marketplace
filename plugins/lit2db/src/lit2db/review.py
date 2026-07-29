"""The review join — a candidate, its paper, and whether its quote is really where it says.

Everything a reviewer needs to rule on a candidate has existed for a while, in two places that
were never connected. `review_queue` returns the queue but SELECTs seven columns and not
`payload_json`, so the quotes and offsets it took a whole pipeline to produce are unreachable
through any tool. `full.txt` holds the text those offsets index into. Nothing read both.

That gap has a cost, and it is the reason this module exists rather than a UI convenience:
`commands/lit2db-review.md` requires that a reviewer be shown the quote before being asked to
rule, and the only way to honour that today is to paste a sentence with nothing around it. A
sentence out of context is exactly the case where a careful reader answers "can't tell" for want
of a paragraph rather than for want of access — which then calibrates the accept bar against the
reviewing interface instead of against the extractor.

## The load-bearing part: an offset is a CLAIM, and it can be wrong

`char_offset` is a character index into `full.txt` and nothing else (see `store.py`). A record
asserting `char_offset=1180` asserts that its quote begins at character 1180 of that exact file.
That assertion can fail — a store rebuilt differently, a quote assembled from a different pass,
a paper that was never stored at all — and the failure is SILENT: slicing at a stale offset
returns text, just not the text the record cites.

So `anchor()` verifies rather than assumes, and reports WHICH of five things happened in a
sentence a researcher can act on. What it must never do is show a reviewer some text, let them
believe it is the evidence, and take a verdict on it — a verdict given against the wrong
paragraph is not a weak label, it is a wrong one, and it is indistinguishable from a good one
once it is in the calibration table.

Domain-blind, like everything else in `src/lit2db/`: nothing here knows what a terpene is.
No HTTP either — this is the join, and `scripts/review_ui.py` is one caller of it.
"""
from __future__ import annotations

import json
import pathlib
from typing import Optional

from .output import connect
from .store import find_spans, section_of, store_dirname

# The pipeline joins several quotes for one value with this separator (`pipeline.assemble`),
# while `char_offset` anchors only the first part that resolved. A reader that treated the
# joined string as one quote would fail to find it and report perfectly good evidence missing.
QUOTE_JOIN = " | "

# What a human can be OFFERED, given whether their evidence could be put in front of them.
# `cant_tell` is always available; the other two have to be earned by showing the quote.
ALL_VERDICTS = ("right", "wrong", "cant_tell")
CANT_TELL_ONLY = ("cant_tell",)

# The five things that can be true of a quote and its recorded offset. Only the first one
# means "the reviewer is looking at the evidence".
ANCHOR_EXACT = "exact"
ANCHOR_MOVED = "moved"
ANCHOR_ABSENT = "absent"
ANCHOR_PAST_END = "past_end"
ANCHOR_NO_STORE = "no_store"


# --- the store, read back ---------------------------------------------------------------
def load_store(sources_root, source_id: str) -> Optional[dict]:
    """Read a written store back into the shape `store.py`'s helpers expect.

    Returns None when there is no `full.txt` for this source — a MISSING paper and an EMPTY
    paper are different facts, and `write_store` refuses to produce the latter, so None here
    unambiguously means "never stored".

    The directory name comes from `store_dirname` rather than from the raw `source_id`, because
    the writer sanitized it: `DOI_10.1002/anie.201506541` was written to `DOI_10.1002_anie...`
    and looking under the unsanitized name would report a stored paper as absent.
    """
    root = pathlib.Path(sources_root)
    d = root / store_dirname(source_id)
    # A source_id arrives from a query string in at least one caller. `store_dirname` already
    # strips `/` and `.` runs cannot climb without one, but the containment check is what makes
    # that a guarantee rather than a consequence of the character class.
    try:
        if not d.resolve().is_relative_to(root.resolve()):
            return None
    except (OSError, ValueError):
        return None
    full = d / "full.txt"
    if not full.is_file():
        return None
    text = full.read_text(encoding="utf-8")
    sections, meta = [], {}
    for name, target in (("sections.json", "sections"), ("meta.json", "meta")):
        p = d / name
        if p.is_file():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if target == "sections" and isinstance(loaded, list):
                sections = loaded
            elif target == "meta" and isinstance(loaded, dict):
                meta = loaded
    return {"source_id": source_id, "full_text": text, "sections": sections, "meta": meta,
            "dir": str(d)}


# --- the candidate, read back -----------------------------------------------------------
def candidate_row(db_path: str, record_id: str, source_id: str) -> Optional[dict]:
    """One candidate by key — its queue columns AND its payload, in a single query.

    `review_queue` returns the queue columns but not `payload_json`: it selects seven columns and
    that is not one of them, which is why the quotes and offsets were unreachable. Rather than
    widening that return shape for every caller — the conversational loop does not want a large
    payload per row — the reader that needs one asks by key.

    Both halves come back together on purpose. A viewer that fetched the row and the payload
    separately would run two queries per record, and the obvious way to get the row (re-run
    `review_queue` and search its result) re-reads the whole queue plus three COUNT(*)s to find
    one item. `connect` is reused so the table definitions keep one home.
    """
    con = connect(db_path)
    try:
        r = con.execute("SELECT entity_type, composite_confidence, decision, reasons_json, "
                        "judge_verdict, payload_json FROM candidates "
                        "WHERE record_id = ? AND source_id = ?",
                        (str(record_id), str(source_id))).fetchone()
    finally:
        con.close()
    if not r:
        return None
    payload = None
    if r[5]:
        try:
            loaded = json.loads(r[5])
            payload = loaded if isinstance(loaded, dict) else None
        except (TypeError, ValueError):
            payload = None
    reasons = []
    if r[3]:
        try:
            reasons = json.loads(r[3]) or []
        except (TypeError, ValueError):
            reasons = []
    return {"record_id": record_id, "source_id": source_id, "entity_type": r[0],
            "composite_confidence": r[1], "decision": r[2], "reasons": reasons,
            "judge_verdict": r[4], "payload": payload}


def candidate_payload(db_path: str, record_id: str, source_id: str) -> Optional[dict]:
    """Just the stored record. Thin wrapper over `candidate_row` — one query either way."""
    row = candidate_row(db_path, record_id, source_id)
    return (row or {}).get("payload")


# --- the verification ---------------------------------------------------------------------
def _anchor_one(full_text: str, quote: str, expected: Optional[int]) -> dict:
    """One quote against one expected offset. `expected=None` means 'find it, wherever it is'."""
    n = len(full_text)
    hits = [h["start"] for h in find_spans(full_text, quote)] if quote else []

    if expected is not None and expected >= n:
        return {"state": ANCHOR_PAST_END, "start": None, "end": None,
                "recorded_offset": expected, "found_at": hits, "text_length": n,
                "explain": (f"The record points to character {expected:,}. This paper's stored "
                            f"text ends at {n:,}.")}

    if expected is not None and full_text[expected:expected + len(quote)] == quote and quote:
        return {"state": ANCHOR_EXACT, "start": expected, "end": expected + len(quote),
                "recorded_offset": expected, "found_at": hits, "text_length": n, "explain": ""}

    if hits and expected is None:
        # No offset was claimed for this part, so there is no claim to have been broken —
        # locating it by search is the whole of what it promised. This is the case for the
        # second and later parts of a joined quote, and calling them `moved` would report a
        # discrepancy against a position the record never asserted.
        return {"state": ANCHOR_EXACT, "start": hits[0], "end": hits[0] + len(quote),
                "recorded_offset": None, "found_at": hits, "text_length": n, "explain": ""}

    if hits:
        # Nearest occurrence to where the record said it would be — with several matches, the
        # closest is the one most likely to be what the offset drifted from.
        best = min(hits, key=lambda h: abs(h - expected))
        return {"state": ANCHOR_MOVED, "start": best, "end": best + len(quote),
                "recorded_offset": expected, "found_at": hits, "text_length": n,
                "explain": (f"The record places this sentence at character {expected:,}. That "
                            f"position holds different text. The sentence itself appears at "
                            f"character {best:,}.")}

    return {"state": ANCHOR_ABSENT, "start": None, "end": None, "recorded_offset": expected,
            "found_at": [], "text_length": n,
            "explain": "This sentence does not appear anywhere in the stored text of this paper."}


# Worst-first: a field is only as trustworthy as its least anchored part.
_SEVERITY = {ANCHOR_NO_STORE: 4, ANCHOR_ABSENT: 3, ANCHOR_PAST_END: 2, ANCHOR_MOVED: 1,
             ANCHOR_EXACT: 0}


def anchor(full_text: Optional[str], quote: str, char_offset: Optional[int]) -> dict:
    """Is this quote where the record says it is? Five answers, each with its own sentence.

    - `exact`     — it is. The reviewer can be shown the evidence and asked to rule.
    - `moved`     — the sentence is in the paper, but not at the recorded position. The offset
                    is what disambiguates two occurrences of the same entity, so a moved quote
                    means nobody can say WHICH occurrence was extracted.
    - `absent`    — the sentence is not in this paper at all.
    - `past_end`  — the offset lies beyond the end of the stored text.
    - `no_store`  — this paper was never built into a store.

    NO FUZZY MATCHING, deliberately. A whitespace-tolerant or near-miss comparison would turn
    the three failing states into `exact` for exactly the records most worth being suspicious
    of, and the point of this function is to be believed when it says `exact`.

    Multi-part quotes (joined with `QUOTE_JOIN`) anchor part-by-part: part 0 at `char_offset`,
    the rest by search — the record asserts a position for the first part only, so a later part
    found anywhere satisfies the only claim made about it. The field reports its WORST part.
    """
    if full_text is None:
        return {"state": ANCHOR_NO_STORE, "spans": [], "recorded_offset": char_offset,
                "explain": ""}   # the caller names the source and the root; this cannot

    parts = [p for p in str(quote or "").split(QUOTE_JOIN) if p]
    if not parts:
        return {"state": ANCHOR_ABSENT, "spans": [], "recorded_offset": char_offset,
                "explain": "This record carries no quote to check."}

    spans = [_anchor_one(full_text, parts[0], char_offset)]
    spans += [_anchor_one(full_text, p, None) for p in parts[1:]]
    worst = max(spans, key=lambda s: _SEVERITY[s["state"]])
    return {"state": worst["state"], "spans": spans, "recorded_offset": char_offset,
            "explain": worst["explain"]}


# --- why it stopped short, in the researcher's language ------------------------------------
def why_it_stopped_short(row: dict, payload: Optional[dict], autoaccept: Optional[float] = None
                         ) -> list:
    """The denial, translated — *"two of the three readings found this; the third did not"*.

    Built from the STRUCTURED SIGNALS in the payload, never by parsing the `reasons` strings.
    A string parser would keep working, wrongly, the day a reason is reworded — and a reviewer
    reading a confident mistranslation is worse off than one reading the raw string.

    Which is why the raw `reasons` are never replaced, only accompanied: the caller shows this
    list and keeps `row["reasons"]` verbatim beside it.
    """
    out = []
    payload = payload or {}
    fields = payload.get("fields") or []

    verdict = str(payload.get("judge_verdict") or row.get("judge_verdict") or "not_run")
    if verdict == "partial":
        out.append("The checker thought the paper supports part of this, but not all of it.")
    elif verdict == "unsupported":
        out.append("The checker could not find support for this in the paper.")
    elif verdict == "unparseable":
        out.append("The checker ran but its answer could not be read, so nothing confirmed this.")
    elif verdict == "not_run":
        out.append("Nothing challenged this record. It was never checked against the paper.")
    if payload.get("judge_note"):
        out.append(f"The checker's note: {payload['judge_note']}")

    k = None
    summary = payload.get("_routing_summary")
    if isinstance(summary, dict):
        for key in ("k", "ensemble_k", "n_passes"):
            if isinstance(summary.get(key), int):
                k = summary[key]
                break

    for fv in fields:
        if not isinstance(fv, dict):
            continue
        name = fv.get("field_name", "this value")
        comp = fv.get("confidence_components") or {}
        ens = comp.get("c_ensemble")
        if isinstance(ens, (int, float)) and ens < 1.0:
            if k:
                # Only when k was actually recorded. Recovering "2 of 3" from 0.667 would be a
                # guess about how the run was configured, printed as a fact.
                out.append(f"Of the {k} independent readings, {round(ens * k)} found "
                           f"'{name}' and the rest did not.")
            else:
                out.append(f"The independent readings did not all find '{name}'.")
        gnd = comp.get("c_grounded")
        if isinstance(gnd, (int, float)) and gnd < 1.0:
            out.append(f"Only part of '{name}' could be found in the sentence quoted for it.")
        for c in fv.get("contradictions") or []:
            if isinstance(c, dict) and c.get("explanation"):
                out.append(f"The paper itself argues against '{name}': {c['explanation']}")
        prov = fv.get("provenance") or {}
        status = prov.get("source_status")
        if status and status != "active":
            out.append(f"The paper this came from is marked {status}.")

    comp_conf = row.get("composite_confidence")
    if isinstance(comp_conf, (int, float)):
        if autoaccept is not None:
            out.append(f"Overall it scored {comp_conf:.2f}, under this project's accept bar "
                       f"of {autoaccept:.2f}.")
        else:
            out.append(f"Overall it scored {comp_conf:.2f}, under this project's accept bar.")

    # Order-preserving dedupe: the same sentence can be produced by two fields.
    seen, unique = set(), []
    for line in out:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return unique


# --- the assembled view --------------------------------------------------------------------
def _plain_value(v) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return "" if v is None else str(v)


def source_links(meta: dict, source_id: str = "") -> list:
    """Where the reviewer can go and read the real paper.

    The store is a normalized text extract with the reference list removed — good for anchoring
    an offset, and NOT the paper. A reviewer deciding whether a value is right often needs the
    figure, the SI table, or the part `build_store` did not keep, and the honest response is to
    hand them the actual article rather than to imply the extract is all there was.

    Built from the identifiers the store already recorded, so nothing is fetched and nothing is
    guessed: an id that was not captured yields no link rather than a constructed one.
    """
    meta = meta or {}
    out = []
    doi = str(meta.get("doi") or "").strip()
    if not doi and str(source_id).upper().startswith("DOI:"):
        doi = str(source_id)[4:].strip()
    if doi:
        out.append({"label": f"doi:{doi}", "url": "https://doi.org/" + doi})
    pmcid = str(meta.get("pmcid") or "").strip()
    if not pmcid and str(source_id).upper().startswith("PMC"):
        pmcid = str(source_id).strip()
    if pmcid:
        out.append({"label": pmcid,
                    "url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"})
    pmid = str(meta.get("pmid") or "").strip()
    if pmid:
        out.append({"label": f"PMID {pmid}", "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"})
    return out


def record_view(db_path: str, sources_root, record_id: str, source_id: str,
                row: Optional[dict] = None, autoaccept: Optional[float] = None) -> dict:
    """One candidate, its paper, and whether it can honestly be ruled on.

    `verdicts_allowed` is the load-bearing key. A record is confirmable or deniable ONLY when
    every one of its fields anchors exactly — that is, only when the reviewer is looking at the
    evidence. Anything else offers `cant_tell` alone.

    That rule is computed HERE rather than in a browser so the server can enforce it on the way
    in as well as on the way out. A disabled button is a suggestion to the person at the
    keyboard; a refusal is a property of the system.
    """
    stored = candidate_row(db_path, record_id, source_id) or {}
    payload = stored.get("payload")
    # One query, not two: `row` is only passed when a caller already holds the queue entry.
    row = row if row is not None else {k: stored.get(k) for k in
                                       ("entity_type", "composite_confidence", "decision",
                                        "reasons", "judge_verdict")}
    store = load_store(sources_root, source_id)
    full = store["full_text"] if store else None

    fields, worst = [], ANCHOR_EXACT
    for fv in (payload or {}).get("fields") or []:
        if not isinstance(fv, dict):
            continue
        prov = fv.get("provenance") or {}
        quote = str(prov.get("verbatim_quote") or "")
        off = prov.get("char_offset")
        off = off if isinstance(off, int) else None
        a = anchor(full, quote, off)
        if a["state"] == ANCHOR_NO_STORE:
            a = {**a, "explain": (f"There is no stored text for {source_id} under "
                                  f"{sources_root}. The paper this record came from was never "
                                  f"built into a store.")}
        if _SEVERITY[a["state"]] > _SEVERITY[worst]:
            worst = a["state"]
        first = next((s for s in a["spans"] if s.get("start") is not None), None)
        fields.append({
            "field_name": fv.get("field_name", ""),
            "value": _plain_value(fv.get("value")),
            "quote": quote,
            "char_offset": off,
            "anchor": a,
            "section": (section_of(store, first["start"])
                        if store and first and store.get("sections") else prov.get("section")),
            "is_inferential": bool(fv.get("is_inferential")),
            "confidence_components": fv.get("confidence_components") or {},
            "contradictions": fv.get("contradictions") or [],
        })

    ok = bool(fields) and worst == ANCHOR_EXACT
    blocked = None
    if not fields:
        blocked = "This record carries no fields, so there is nothing to show you."
    elif not ok:
        bad = next(f for f in fields if f["anchor"]["state"] != ANCHOR_EXACT)
        blocked = f"{bad['field_name']}: {bad['anchor']['explain']}"

    meta = (store or {}).get("meta") or {}
    return {
        "record_id": record_id,
        "source_id": source_id,
        "entity_type": (payload or {}).get("entity_type") or row.get("entity_type") or "",
        "composite_confidence": row.get("composite_confidence"),
        "decision": row.get("decision"),
        "reasons": row.get("reasons") or [],
        "judge_verdict": (payload or {}).get("judge_verdict") or row.get("judge_verdict"),
        "fields": fields,
        "stopped_short_because": why_it_stopped_short(row, payload, autoaccept),
        "verdicts_allowed": list(ALL_VERDICTS if ok else CANT_TELL_ONLY),
        "blocked_because": blocked,
        "evidence_state": worst if fields else ANCHOR_ABSENT,
        "source_text": (store or {}).get("full_text"),
        "source_text_scope": meta.get("source_text_scope"),
        "source_links": source_links(meta, source_id),
        "source_meta": meta,
        "found": payload is not None,
    }


def may_record(view: dict, verdict: str) -> Optional[str]:
    """None if this verdict may be recorded for this record; the refusal sentence otherwise.

    The single place the rule is applied, so the page and the endpoint cannot drift apart.
    """
    v = str(verdict or "").strip().lower()
    if v not in ALL_VERDICTS:
        return f"{verdict!r} is not one of {list(ALL_VERDICTS)}."
    if v in view.get("verdicts_allowed", []):
        return None
    return ("This record cannot be confirmed or denied, because its quoted sentence could not "
            "be put in front of you: " + (view.get("blocked_because")
                                          or "the evidence is missing.") +
            " Recording anything but \"can't tell from this\" would put a verdict in the "
            "calibration set that was never given against the evidence.")
