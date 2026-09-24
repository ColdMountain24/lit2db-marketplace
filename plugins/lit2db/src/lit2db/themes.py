"""Theme sets for a review: propose from the corpus, ratify, freeze into the spec (M13, D-201).

## The shape, and why it needs no new pipeline

A theme is substance, so it ratifies; a theme SET proposed from claims the corpus actually
contains is structure, so an agent may propose it. That is the same boundary as the schema, and
following it exactly buys the rest for free:

    wave 1, a SAMPLE   extract claims, with no theme field in the spec
    propose            read those claims back, propose a theme set with definitions and examples
    ratify             the researcher accepts, edits or rejects each theme, once
    freeze             the set becomes an ENUM FIELD in the spec, versioned like any schema change
    wave 2, the CORPUS extract claims again, now with `theme` among the ratified fields

Wave 2 assigns themes through machinery that already exists and is already measured: k
independent readings, agreement per field, abstention into the review queue when they disagree,
the judge, the counter-evidence hunter, the gate. **There is no separate classifier, no second
gate, and no new way for a theme to enter the database unratified.**

The price is stated rather than hidden: the sample papers are read twice. That is what it costs
to build a coding frame out of the corpus instead of out of a guess, and it is what a human
reviewer does with their first ten papers anyway.

## What this module refuses

It never proposes a theme the claims do not support: every theme must cite the claim ids it was
drawn from, and one citing none is dropped before the researcher sees it. It never freezes a
theme the researcher did not accept, because `freeze` writes ACCEPTED ledger items and a field
tracing to anything else fails `SchemaReadySpec` validation the way an unratified field always
has. And it never edits a frozen theme set in place: a revision is a new spec version with the
reason recorded.

Domain-INVARIANT: nothing here knows what a theme is about.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sqlite3
from typing import Callable, Optional

TEMPLATE = pathlib.Path(__file__).resolve().parents[2] / "templates" / "prompts" / "themes.md"
THEME_FIELD = "theme"
LEDGER_PREFIX = "T"
UNASSIGNED = "not assigned"


# The invariant core of a review's spec. Domain-blind: every one of these is a fact about how a
# review reads papers, not about any field. The researcher's question, criteria and query are
# what make it theirs, and they arrive through ratification like everything else.
CLAIM_FIELDS = [
    {"name": "claim_text", "type": "str",
     "definition": "One claim the paper makes about the review's question, in the paper's own "
                   "words. Not a summary, not a conclusion drawn across papers.",
     "provenance_granularity": "one row per claim per paper", "ledger_item_id": "C1"},
    {"name": "direction", "type": "enum",
     "enum": ["supports", "contradicts", "mixed", "not_applicable"],
     "definition": "What this claim does to the review's question, as the paper states it. "
                   "`mixed` where the paper itself reports both; `not_applicable` where the "
                   "claim is context rather than evidence.",
     "provenance_granularity": "one row per claim per paper", "ledger_item_id": "C2"},
    {"name": "study_design", "type": "str",
     "definition": "How the paper describes its own design, quoted rather than classified.",
     "provenance_granularity": "one per paper", "ledger_item_id": "C3"},
    {"name": "population", "type": "str",
     "definition": "Who or what was studied, as the paper describes them.",
     "provenance_granularity": "one per paper", "ledger_item_id": "C4"},
]


def claim_spec(question: str, *, query: str, inclusion: Optional[dict] = None,
               ratified: bool = False, version: str = "v1") -> dict:
    """A review's starting spec: the invariant claim fields plus the researcher's own question.

    Written PROPOSED by default, which is not a formality. `SchemaReadySpec` refuses to build
    from a field tracing to anything but an ACCEPTED item, so a spec written by this function
    cannot run a wave until the researcher has actually ruled on it. `ratified=True` is the act
    of ruling, and the caller is responsible for having asked.
    """
    status = "ACCEPTED" if ratified else "PROPOSED"
    items = [{"item_id": f["ledger_item_id"], "kind": "field",
              "summary": f"{f['name']}: {f['definition']}", "proposed_by": "agent",
              "status": status} for f in CLAIM_FIELDS]
    items.append({"item_id": "Q1", "kind": "source_scope",
                  "summary": f"The corpus is whatever this query returns: {query}",
                  "proposed_by": "agent", "status": status})
    for i, (k, v) in enumerate(sorted((inclusion or {}).items()), start=1):
        items.append({"item_id": f"I{i}", "kind": "inclusion_criterion",
                      "summary": f"{k}: {v}", "proposed_by": "agent", "status": status})
    return {
        "research_question": question,
        "ml_task": "knowledge_graph",
        "unit_of_analysis": "(claim, study): one row per claim a paper makes about the question",
        "fields": [dict(f) for f in CLAIM_FIELDS],
        "inclusion_exclusion": dict(inclusion or {}),
        "negative_data_policy": "A paper that makes no claim about the question is recorded as "
                                "having made none. An empty reading is a result about the paper.",
        "source_scope": {"adapters": ["literature"],
                         "queries": [{"corpus": "europepmc", "query": query,
                                      "ledger_item_id": "Q1"}]},
        "ledger": {"items": items},
        "spec_version": version,
    }


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _rows(db_path) -> list:
    path = pathlib.Path(db_path)
    if not path.exists():
        return []
    con = sqlite3.connect(str(path))
    try:
        return con.execute("SELECT record_id, source_id, payload_json FROM candidates "
                           "ORDER BY source_id, record_id").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def collect_claims(db_path, *, text_field: str = "claim_text", limit: int = 400) -> list:
    """Every claim the sample produced, gated or not.

    The candidate pool, not the accepted table: a coding frame built only from what cleared an
    uncalibrated bar is a frame built from whatever the gate happened to like, and on the first
    BBB wave that was nothing at all.
    """
    out = []
    for rid, sid, payload in _rows(db_path)[:limit]:
        try:
            rec = json.loads(payload or "{}")
        except (TypeError, ValueError):
            continue
        f = next((x for x in (rec.get("fields") or [])
                  if x.get("field_name") == text_field), None)
        if not f:
            continue
        out.append({"record_id": rid, "source_id": sid, "text": f.get("value"),
                    "quote": (f.get("provenance") or {}).get("verbatim_quote"),
                    "theme": next((x.get("value") for x in (rec.get("fields") or [])
                                   if x.get("field_name") == THEME_FIELD), None)})
    return out


def build_prompt(claims: list, question: str, *, n_themes: int = 12) -> str:
    listing = "\n".join(f'- [{c["record_id"]}] {c["text"]}' for c in claims)
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("{QUESTION}", question)
            .replace("{N_THEMES}", str(n_themes))
            .replace("{CLAIMS}", listing))


def parse(reply: str, known_ids: Optional[set] = None) -> list:
    """Themes out of a model reply, each carrying the claims it was drawn from."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        raise ValueError("no JSON object in the reply")
    data = json.loads(m.group(0))
    out = []
    for t in data.get("themes") or []:
        name = str(t.get("name") or "").strip()
        definition = str(t.get("definition") or "").strip()
        examples = [str(e).strip() for e in (t.get("example_claim_ids") or []) if str(e).strip()]
        if known_ids is not None:
            examples = [e for e in examples if e in known_ids]
        if not name or not definition or not examples:
            continue
        out.append({"name": name, "definition": definition, "example_claim_ids": examples})
    return out


def propose(db_path, question: str, ask: Callable[[str], str], *,
            text_field: str = "claim_text", n_themes: int = 12, limit: int = 400) -> dict:
    claims = collect_claims(db_path, text_field=text_field, limit=limit)
    if not claims:
        raise ValueError(f"no claims in {db_path}; run the sample wave first")
    themes = parse(ask(build_prompt(claims, question, n_themes=n_themes)),
                   known_ids={c["record_id"] for c in claims})
    if not themes:
        raise ValueError("the reply proposed no theme citing a claim from this sample")
    return {"question": question, "proposed_at": _now(), "n_claims_read": len(claims),
            "status": "PROPOSED", "themes": themes,
            "note": "Nothing here is ratified. Accept, edit or reject each theme; only then does "
                    "freeze write them into the spec."}


def _next_version(v: str) -> str:
    m = re.match(r"^(.*?)(\d+)$", str(v))
    return f"{m.group(1)}{int(m.group(2)) + 1}" if m else f"{v}-themed"


def freeze(spec_path, themes: list, *, ratified_by: str = "researcher",
           granularity: str = "one theme per claim") -> dict:
    """Write the ratified theme set into the spec as an enum field, versioned.

    A schema is never mutated in place: the version is bumped and what forced the change is
    recorded, the rule a field revision has always had. Ledger items are written ACCEPTED because
    this is only reached after the researcher ruled, and a theme they edited carries their
    wording verbatim as its definition.
    """
    path = pathlib.Path(spec_path)
    spec = json.loads(path.read_text(encoding="utf-8"))
    names = [t["name"] for t in themes]
    if not names:
        raise ValueError("no themes to freeze")
    if len(set(names)) != len(names):
        raise ValueError(f"two themes share a name: {sorted(names)}")
    if any(f.get("name") == THEME_FIELD for f in spec.get("fields") or []):
        raise ValueError(f"this spec already carries a '{THEME_FIELD}' field; a theme set is "
                         f"revised by freezing a NEW spec version, never edited in place")

    existing = {i.get("item_id") for i in (spec.get("ledger") or {}).get("items") or []}
    n = 1
    while f"{LEDGER_PREFIX}{n}" in existing:
        n += 1
    item_id = f"{LEDGER_PREFIX}{n}"

    spec.setdefault("ledger", {}).setdefault("items", []).append({
        "item_id": item_id, "kind": "vocab_binding",
        "summary": f"Theme set for this review, {len(themes)} themes, ratified by {ratified_by}: "
                   + "; ".join(f"{t['name']} = {t['definition']}" for t in themes),
        "proposed_by": "agent", "status": "ACCEPTED",
        "payload": {"themes": themes, "ratified_at": _now(), "ratified_by": ratified_by}})
    spec.setdefault("fields", []).append({
        "name": THEME_FIELD, "type": "enum", "enum": names,
        "definition": "Which ratified theme this claim belongs to. Exactly one, and only where "
                      "the claim's own words support it.",
        "provenance_granularity": granularity, "ledger_item_id": item_id})

    before = str(spec.get("spec_version") or "v0")
    spec["spec_version"] = _next_version(before)
    path.write_text(json.dumps(spec, indent=1), encoding="utf-8")

    log = path.parent / "spec-evolution.md"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"## {_now()} · {before} -> {spec['spec_version']}\n\n"
                 f"Theme set frozen: {len(themes)} themes, ratified by {ratified_by} as ledger "
                 f"item `{item_id}`. What forced the change: the sample wave's own claims, read "
                 f"back and grouped. Every later claim carries `theme` as an ordinary ratified "
                 f"field, so the assignment goes through the same agreement and abstention as "
                 f"every other value.\n\n"
                 + "\n".join(f"- **{t['name']}** {t['definition']}" for t in themes) + "\n\n")
    return {"spec_version": spec["spec_version"], "was": before, "ledger_item": item_id,
            "themes": names, "spec": str(path), "evolution_log": str(log)}


def ratified_set(spec_path) -> dict:
    """{name: theme} for the set this spec froze, or {} if it froze none."""
    spec = json.loads(pathlib.Path(spec_path).read_text(encoding="utf-8"))
    return {t["name"]: t for item in (spec.get("ledger") or {}).get("items") or []
            for t in (item.get("payload") or {}).get("themes") or []}


def markdown(db_path, spec_path, *, text_field: str = "claim_text") -> str:
    """The claims under each ratified theme, grouped by study. A reading order, not an argument.

    Claims nobody could place are printed under "not assigned" rather than dropped: an abstention
    is a result, and a themed table that quietly omits what it could not place would overstate
    how well the frame fits the corpus.
    """
    spec = json.loads(pathlib.Path(spec_path).read_text(encoding="utf-8"))
    field = next((f for f in spec.get("fields") or [] if f.get("name") == THEME_FIELD), None)
    if field is None:
        raise ValueError("this spec has no ratified theme set; freeze one first")
    defs = ratified_set(spec_path)

    claims = collect_claims(db_path, text_field=text_field, limit=100_000)
    grouped: dict = {}
    for c in claims:
        grouped.setdefault(c.get("theme") or UNASSIGNED, []).append(c)

    lines = [f"# Themed evidence, spec {spec.get('spec_version')}", "",
             f"{len(claims)} claim(s) from {len({c['source_id'] for c in claims})} source(s). "
             "Every claim is quoted from its own paper and carries the record it is stored "
             "under. The grouping is the ratified theme set; nothing here is a synthesis.", ""]
    for name in list(field.get("enum") or []) + [UNASSIGNED]:
        rows = grouped.get(name) or []
        if not rows and name == UNASSIGNED:
            continue
        lines.append(f"## {name}  ({len(rows)} claim(s))")
        if defs.get(name, {}).get("definition"):
            lines += ["", f"*{defs[name]['definition']}*"]
        elif name == UNASSIGNED:
            lines += ["", "*The readings did not agree on a theme, so these wait for a person. "
                      "An abstention is a result, not a gap to fill.*"]
        lines.append("")
        for src in sorted({r["source_id"] for r in rows}):
            lines.append(f"**{src}**")
            for r in [x for x in rows if x["source_id"] == src]:
                lines.append(f"- {r['text']}")
                lines.append(f"  > {r['quote'] or '(no quote recorded)'}")
                lines.append(f"  `{r['record_id']}`")
            lines.append("")
    return "\n".join(lines)
