"""The interview as proposals to tick: from a researcher's question to a ratified spec (D-211).

## Why this exists

`/lit2db-start` ran the scope interview inside Claude Code: four popup questions, then a second
agent interview, then a spec written by that agent. A researcher who has never opened a terminal
could not get through it, and a researcher who could still ratified a schema they never saw
whole, one popup at a time. The app shows the whole draft on one page instead, and every tick is
the ratification of one item. This module is the part of that which is not a page: the prompt
that drafts the table, the reading of the draft, and the spec built from what was ticked.

## The boundary it keeps

The model PROPOSES; the researcher's ticks RATIFY. Concretely:

- Every column carries where it came from: the researcher's own question (`from_question`, with
  the words quoted) or a suggestion (with its reason). The page shows the two apart.
- A ticked column becomes an ACCEPTED ledger item, or ACCEPTED_WITH_EDIT when the researcher
  changed its wording; an unticked one is REJECTED with that reason, so it is not proposed again;
  a column the researcher added is ACCEPTED with their words kept verbatim in the payload.
- The spec is built through `SchemaReadySpec`, so a field that traces to anything unratified, or
  a literature corpus with no ratified query, is refused here exactly as everywhere else.

Nothing here calls a model. `build_prompt` makes the text and `parse_proposal` reads the reply;
the call itself goes through the wave driver's `call_agent`, so it is billed and fused like every
other call (the app does that).
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from typing import Optional

from .contracts import SchemaReadySpec
from .uncertainty import companion_fields

TEMPLATE = pathlib.Path(__file__).resolve().parents[2] / "templates" / "intake.md"

# What a proposal's `kind` becomes in the spec: the stored type, and whether the value can ever
# auto-accept. `text` is the review lane by design (contracts/spec.py `auto_acceptable`): a
# sentence is phrased differently by every reading, so it is always checked by eye.
KINDS = {
    "name": ("str", True),
    "number": ("float", True),
    "count": ("int", True),
    "category": ("enum", True),
    "list": ("list[str]", True),
    "text": ("str", False),
}
SPREAD = "spread"
ML_TASKS = ("classification", "regression", "knowledge_graph", "generative")
EPMC = "europepmc"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    if not s:
        return "column"
    return s if s[0].isalpha() else f"c_{s}"


def build_prompt(question: str, seed: str = "") -> str:
    if not (question or "").strip():
        raise ValueError("there is no question to draft a table from")
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("{QUESTION}", question.strip())
            .replace("{SEED}", (seed or "").strip() or "(nothing)"))


def _json_object(text: str) -> dict:
    """The one JSON object in a model's reply, fenced or not."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the draft came back without a table in it")
    try:
        return json.loads(t[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"the draft came back unreadable ({exc.msg})") from exc


def parse_proposal(text: str) -> dict:
    """Read a draft into the shape the page shows, repairing what can be repaired and refusing
    what cannot. A repair is structural only (a duplicate name renumbered, an unknown kind read
    as text); nothing about what a column means is changed here."""
    raw = _json_object(text) if isinstance(text, str) else dict(text)
    cols, seen = [], set()
    for c in raw.get("columns") or []:
        if not isinstance(c, dict) or not (c.get("label") or c.get("name")):
            continue
        name = slug(c.get("name") or c.get("label"))
        base, i = name, 2
        while name in seen:
            name, i = f"{base}_{i}", i + 1
        seen.add(name)
        kind = str(c.get("kind") or "text").strip().lower()
        if kind not in KINDS and kind != SPREAD:
            kind = "text"
        values = [str(v) for v in (c.get("values") or []) if str(v).strip()]
        if kind == "category" and len(values) < 2:
            kind = "name"                  # a category with no list is a name as printed
        cols.append({
            "name": name,
            "label": str(c.get("label") or name.replace("_", " ")).strip(),
            "definition": str(c.get("definition") or "").strip(),
            "example": str(c.get("example") or "").strip(),
            "kind": kind,
            "unit": (str(c["unit"]).strip() or None) if c.get("unit") else None,
            "values": values if kind == "category" else [],
            "from_question": bool(c.get("from_question")),
            "question_words": str(c.get("question_words") or "").strip(),
            "why": str(c.get("why") or "").strip(),
            "suggest_on": bool(c.get("suggest_on", True)),
            "suggest_required": bool(c.get("suggest_required")),
            "normalized": bool(c.get("normalized")),
            "qualifies": slug(c["qualifies"]) if c.get("qualifies") else None,
        })
    numeric = {c["name"] for c in cols if c["kind"] in ("number", "count")}
    # A spread with nothing numeric to sit beside cannot be declared (the contract refuses a group
    # on a non-numeric field), so it is dropped rather than shown as an option that would fail.
    cols = [c for c in cols if c["kind"] != SPREAD or c["qualifies"] in numeric]
    if not cols:
        raise ValueError("the draft proposed no columns")

    opts = [o for o in (raw.get("row_options") or []) if isinstance(o, dict) and o.get("label")]
    if not opts:
        raise ValueError("the draft did not say what one row is")
    opts = [{"label": str(o["label"]).strip(),
             "description": str(o.get("description") or "").strip(),
             "unit_of_analysis": str(o.get("unit_of_analysis") or o["label"]).strip(),
             "suggested": bool(o.get("suggested")),
             "why": str(o.get("why") or "").strip()} for o in opts[:4]]
    first = next((i for i, o in enumerate(opts) if o["suggested"]), 0)
    for i, o in enumerate(opts):
        o["suggested"] = i == first

    plain = [c["name"] for c in cols if c["kind"] != SPREAD]
    identity = [slug(x) for x in (raw.get("identity") or []) if slug(x) in plain][:4] \
        or plain[:1]
    purpose = raw.get("purpose") if isinstance(raw.get("purpose"), dict) else {}
    task = str(purpose.get("ml_task") or "").strip().lower()
    if task not in ML_TASKS:
        task = "regression" if numeric else "knowledge_graph"
    query = str(raw.get("query") or "").strip()
    if not query:
        raise ValueError("the draft proposed no search")
    return {
        "entity": slug(raw.get("entity") or "row"),
        "row_options": opts,
        "columns": cols,
        "identity": identity,
        "purpose": {"ml_task": task, "plain": str(purpose.get("plain") or "").strip()},
        "negative_data": str(raw.get("negative_data") or "").strip()
        or "A paper that reports nothing for a row is recorded as reporting nothing.",
        "exclusions": [str(x).strip() for x in (raw.get("exclusions") or []) if str(x).strip()],
        "query": query,
        "query_plain": str(raw.get("query_plain") or "").strip(),
    }


def default_rulings(proposal: dict) -> dict:
    """The page's starting state: the suggestions pre-ticked as the draft suggested them. Nothing
    is ratified by this; ratification is the researcher pressing continue on what they see."""
    return {
        "row": {"choice": next(i for i, o in enumerate(proposal["row_options"]) if o["suggested"]),
                "own": ""},
        "columns": [{**c, "on": c["suggest_on"], "required": c["suggest_required"] and c["suggest_on"],
                     "added": False, "verbatim": ""} for c in proposal["columns"]],
        "identity": list(proposal["identity"]),
        "normalized": [c["name"] for c in proposal["columns"] if c["normalized"]],
        "exclusions": [{"text": x, "on": True, "added": False} for x in proposal["exclusions"]],
    }


_COMPARED = ("label", "definition", "kind", "values", "unit")


def _changed(col: dict, drafted: Optional[dict]) -> list:
    if drafted is None:
        return []
    return [k for k in _COMPARED if (col.get(k) or None) != (drafted.get(k) or None)]


def build_spec(intake: dict, *, query: str, result_counts: Optional[dict] = None,
               abstracts: bool = False, version: str = "v1") -> dict:
    """The ratified spec, from the question, the draft, the ticks and the approved search.

    Raises ValueError with a plain sentence when the ticks cannot make a table (no column
    ticked, say), and lets `SchemaReadySpec` refuse anything the contract refuses.
    """
    question = (intake.get("question") or "").strip()
    proposal, rulings = intake.get("proposal"), intake.get("rulings")
    if not question or not proposal or not rulings:
        raise ValueError("the question and the table have to be settled before the search")
    if not (query or "").strip():
        raise ValueError("there is no search to approve")
    drafted = {c["name"]: c for c in proposal["columns"]}
    items, fields = [], []

    # One row is ...
    r = rulings.get("row") or {}
    own = (r.get("own") or "").strip()
    if own:
        uoa = f"({own}, source)"
        items.append({"item_id": "L-ROW", "kind": "axis_setting", "status": "ACCEPTED_WITH_EDIT",
                      "summary": f"One row is: {own}",
                      "edit_note": f"The researcher wrote their own row instead of the three "
                                   f"offered: {own!r}",
                      "payload": {"researcher_verbatim": own,
                                  "offered": [o["label"] for o in proposal["row_options"]]}})
    else:
        opt = proposal["row_options"][int(r.get("choice") or 0)]
        uoa = opt["unit_of_analysis"]
        items.append({"item_id": "L-ROW", "kind": "axis_setting", "status": "ACCEPTED",
                      "summary": f"One row is: {opt['label']}. {opt['description']}".strip(),
                      "payload": {"unit_of_analysis": uoa, "was_suggested": opt["suggested"],
                                  "offered": [o["label"] for o in proposal["row_options"]]}})

    # The columns. Every one the draft offered is ruled on, ticked or not.
    cols = rulings.get("columns") or []
    on = {c["name"]: c for c in cols if c.get("on")}
    if not [c for c in on.values() if c.get("kind") != SPREAD]:
        raise ValueError("tick at least one column")
    for c in cols:
        iid = f"F-{c['name']}"
        origin = ("added by the researcher" if c.get("added") else
                  "the researcher's question" if c.get("from_question") else "suggested")
        payload = {"origin": origin, "label": c["label"]}
        if c.get("question_words"):
            payload["question_words"] = c["question_words"]
        if c.get("why"):
            payload["why_suggested"] = c["why"]
        if c.get("verbatim"):
            payload["researcher_verbatim"] = c["verbatim"]
        item = {"item_id": iid, "kind": "field", "payload": payload,
                "summary": f"{c['label']}: {c.get('definition') or ''}".strip()}
        if not c.get("on"):
            item.update(status="REJECTED",
                        reject_reason="left unticked by the researcher on the What to collect step")
            items.append(item)
            continue
        changes = _changed(c, drafted.get(c["name"]))
        if changes:
            item.update(status="ACCEPTED_WITH_EDIT",
                        edit_note="The researcher changed the drafted " + ", ".join(changes) + ".")
        else:
            item["status"] = "ACCEPTED"
        items.append(item)
        if c.get("kind") == SPREAD:
            continue
        typ, auto = KINDS[c.get("kind") or "text"]
        definition = (c.get("definition") or c["label"]).rstrip(".") + "."
        if c.get("example"):
            definition += f" For example: {c['example']}."
        fields.append({"name": c["name"], "type": typ, "unit": c.get("unit") or None,
                       "enum": list(c["values"]) if typ == "enum" else None,
                       "definition": definition, "provenance_granularity": uoa,
                       "ledger_item_id": iid, "auto_acceptable": auto,
                       "required": bool(c.get("required"))})
    # A spread sits beside one ticked number; its seven parts are D-203's uncertainty group.
    names = {f["name"]: f for f in fields}
    for c in on.values():
        if c.get("kind") != SPREAD:
            continue
        m = names.get(c.get("qualifies"))
        if m is None or m["type"] not in ("float", "int"):
            raise ValueError(f"'{c['label']}' goes beside a number that is not ticked; tick that "
                             f"number too, or untick '{c['label']}'")
        fields += companion_fields(m["name"], f"F-{c['name']}", unit=m.get("unit"))

    # Decided for you: each one shown on the page with a Change link, and confirmed by continuing.
    ticked = [f["name"] for f in fields if not f.get("uncertainty_of")]
    label_of = {c["name"]: c["label"] for c in cols}
    identity = [n for n in (rulings.get("identity") or []) if n in ticked] or ticked[:1]
    identity_edited = identity != [n for n in proposal["identity"] if n in ticked]
    items.append({"item_id": "L-IDENTITY", "kind": "axis_setting",
                  "status": "ACCEPTED_WITH_EDIT" if identity_edited else "ACCEPTED",
                  "edit_note": "The researcher changed which columns make two rows the same."
                  if identity_edited else None,
                  "summary": "Two rows count as the same when "
                             + ", ".join(label_of[n] for n in identity) + " all match.",
                  "payload": {"identity": identity}})
    normalized = [n for n in (rulings.get("normalized") or []) if n in ticked]
    items.append({"item_id": "L-NORMALIZED", "kind": "axis_setting", "status": "ACCEPTED",
                  "summary": "Written in a standard form rather than copied as printed, so checked "
                             "against their sentence for support: " + (", ".join(normalized)
                                                                       or "none"),
                  "payload": {"evidence_grounded": normalized}})
    task = proposal["purpose"]["ml_task"]
    items.append({"item_id": "L-PURPOSE", "kind": "axis_setting", "status": "ACCEPTED",
                  "summary": proposal["purpose"]["plain"] or f"The table is for {task}.",
                  "payload": {"ml_task": task}})
    items.append({"item_id": "L-NEGATIVE", "kind": "axis_setting", "status": "ACCEPTED",
                  "summary": proposal["negative_data"], "payload": {}})
    inclusion = {}
    for i, x in enumerate(rulings.get("exclusions") or [], start=1):
        iid = f"I{i}"
        if x.get("on"):
            inclusion[f"exclude_{i}"] = x["text"]
        items.append({"item_id": iid, "kind": "inclusion_criterion",
                      "status": "ACCEPTED" if x.get("on") else "REJECTED",
                      "reject_reason": None if x.get("on") else "turned off by the researcher",
                      "summary": f"Leave out: {x['text']}",
                      "payload": {"origin": "added by the researcher" if x.get("added")
                                  else "suggested"}})
    items.append({"item_id": "L-ABSTRACTS", "kind": "source_scope", "status": "ACCEPTED",
                  "summary": ("Abstracts are read when the full paper is not free to read."
                              if abstracts else "Only full papers are read; abstracts are not."),
                  "payload": {"abstracts_admitted": bool(abstracts)}})
    query_edited = query.strip() != proposal["query"].strip()
    items.append({"item_id": "Q1", "kind": "source_scope",
                  "status": "ACCEPTED_WITH_EDIT" if query_edited else "ACCEPTED",
                  "edit_note": "The researcher edited the drafted search." if query_edited else None,
                  "summary": f"The papers are whatever this Europe PMC search returns: {query}",
                  "payload": {"drafted": proposal["query"],
                              "plain": proposal.get("query_plain") or ""}})

    tiers = [identity[:k] for k in range(len(identity), 0, -1)]
    spec = {
        "research_question": question,
        "ml_task": task,
        "unit_of_analysis": uoa,
        "fields": fields,
        "inclusion_exclusion": inclusion,
        "negative_data_policy": proposal["negative_data"],
        "source_scope": {"adapters": ["literature"], "corpora": [EPMC],
                         "queries": [{"corpus": EPMC, "query": query.strip(),
                                      "executed_at": _now(),
                                      "result_counts": {k: int(v) for k, v in
                                                        (result_counts or {}).items()
                                                        if isinstance(v, int)},
                                      "ledger_item_id": "Q1"}]},
        "ledger": {"items": items},
        "spec_version": version,
        "_built_by": "lit2db app: the What to collect and Which papers steps (D-211). Every "
                     "item above was shown to the researcher and ruled by a tick or by continuing.",
        # What `plan` needs and the spec does not otherwise say, recorded beside the ruling it
        # came from so the run cannot drift from what the page showed.
        "_plan": {"entity_type": proposal["entity"], "identity_field": identity[0],
                  "identity_chain": tiers, "ordinal_within": identity[:1],
                  "evidence_grounded": normalized, "abstracts": bool(abstracts)},
    }
    SchemaReadySpec.model_validate(spec)         # the contract's refusal, at the point of writing
    return spec
