"""The coding frame: proposed from the corpus, ratified by a person, frozen into the spec (M13).

The whole design rests on one boundary being kept. A theme is substance, so it ratifies. A theme
SET proposed from claims the corpus actually made is structure, so an agent may propose it. Keep
that line and a review needs no new pipeline: `theme` becomes an ordinary enum field, and the
agreement, abstention and gate that already exist do the assigning. Cross it and the tool is
writing the review's conclusions and calling them the researcher's.
"""
from __future__ import annotations

import json

import pytest

from lit2db import output, themes

REPLY = json.dumps({"themes": [
    {"name": "efficacy", "definition": "Claims about whether the intervention changed the "
                                       "primary outcome.", "example_claim_ids": ["r1"]},
    {"name": "safety", "definition": "Claims about harms or tolerability.",
     "example_claim_ids": ["r2"]},
    {"name": "invented", "definition": "A theme the sample never suggested.",
     "example_claim_ids": ["r999"]},
    {"name": "nameless", "definition": "", "example_claim_ids": ["r1"]},
]})


def _claim(rid, text, quote, theme=None):
    fields = [{"field_name": "claim_text", "value": text,
               "provenance": {"verbatim_quote": quote}}]
    if theme:
        fields.append({"field_name": "theme", "value": theme,
                       "provenance": {"verbatim_quote": quote}})
    return {"record_id": rid, "entity_type": "claim", "fields": fields}


def _db(tmp_path, claims=(("r1", "vitamin D lowered TSH", "TSH fell in the treated group."),
                          ("r2", "no excess adverse events", "Adverse events were similar."))):
    db = str(tmp_path / "database.db")
    for rid, text, quote, *rest in claims:
        output.record_candidate(_claim(rid, text, quote, rest[0] if rest else None), 0.5,
                                {"decision": "deny", "reasons": ["x"]}, db, "PMC1")
    return db


def _spec(tmp_path, **over):
    spec = {
        "research_question": "Does vitamin D help?", "ml_task": "classification",
        "unit_of_analysis": "(claim, study)",
        "fields": [{"name": "claim_text", "type": "str", "definition": "The claim as stated.",
                    "provenance_granularity": "one per claim", "ledger_item_id": "F1"}],
        "negative_data_policy": "A paper claiming nothing qualifying is recorded as such.",
        "source_scope": {"adapters": ["literature"],
                         "queries": [{"corpus": "europepmc", "query": "x",
                                      "ledger_item_id": "Q1"}]},
        "ledger": {"items": [
            {"item_id": "F1", "kind": "field", "summary": "claim", "status": "ACCEPTED"},
            {"item_id": "Q1", "kind": "source_scope", "summary": "q", "status": "ACCEPTED"}]},
        "spec_version": "v1"}
    spec.update(over)
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec))
    return p


# --- propose ---------------------------------------------------------------------------------

def test_a_theme_citing_no_claim_from_this_sample_never_reaches_the_researcher(tmp_path):
    """A researcher asked to approve a theme the corpus did not suggest is being asked to
    originate one while believing they are approving one. That has happened twice in this
    project's own schemas, and both times the scaffold was correct and the work went around it."""
    out = themes.propose(_db(tmp_path), "Does vitamin D help?", lambda prompt: REPLY)
    names = [t["name"] for t in out["themes"]]
    assert names == ["efficacy", "safety"]
    assert out["status"] == "PROPOSED" and "Nothing here is ratified" in out["note"]


def test_the_prompt_carries_the_claims_and_the_question_and_no_domain(tmp_path):
    seen = {}

    def ask(prompt):
        seen["prompt"] = prompt
        return REPLY

    themes.propose(_db(tmp_path), "Does vitamin D help?", ask)
    assert "[r1] vitamin D lowered TSH" in seen["prompt"]
    assert "Does vitamin D help?" in seen["prompt"]
    body = themes.TEMPLATE.read_text().lower()
    for word in ("vitamin", "patient", "trial", "gene"):
        assert word not in body, f"the shipped template names {word!r}"


def test_proposing_before_any_claims_exist_says_so(tmp_path):
    with pytest.raises(ValueError, match="run the sample wave first"):
        themes.propose(str(tmp_path / "nothing.db"), "q", lambda p: REPLY)


def test_the_frame_is_built_from_the_candidate_pool_not_the_accepted_table(tmp_path):
    """On the first BBB wave the accepted table was empty. A coding frame drawn from it would
    have been a frame drawn from whatever an uncalibrated gate happened to like."""
    db = _db(tmp_path)
    assert len(themes.collect_claims(db)) == 2


# --- freeze ----------------------------------------------------------------------------------

def _ruled():
    return [{"name": "efficacy", "definition": "Did it change the primary outcome.",
             "example_claim_ids": ["r1"]},
            {"name": "safety", "definition": "Harms and tolerability.",
             "example_claim_ids": ["r2"]}]


def test_freezing_writes_a_ratified_enum_field_and_bumps_the_version(tmp_path):
    spec_path = _spec(tmp_path)
    out = themes.freeze(spec_path, _ruled(), ratified_by="AP")
    spec = json.loads(spec_path.read_text())
    field = next(f for f in spec["fields"] if f["name"] == "theme")

    assert out["was"] == "v1" and out["spec_version"] == "v2"
    assert field["enum"] == ["efficacy", "safety"] and field["type"] == "enum"
    item = next(i for i in spec["ledger"]["items"] if i["item_id"] == field["ledger_item_id"])
    assert item["status"] == "ACCEPTED" and "AP" in item["summary"]


def test_a_frozen_spec_still_validates_so_the_wave_can_run_on_it(tmp_path):
    """The point of freezing into the spec rather than into a config: everything downstream,
    including the refusal to build from anything unratified, applies unchanged."""
    from lit2db.spec_context import load_spec, spec_context
    spec_path = _spec(tmp_path)
    themes.freeze(spec_path, _ruled())
    spec = load_spec(spec_path)
    assert "theme" in {f.name for f in spec.fields}
    assert "efficacy | safety" in spec_context(spec)


def test_a_theme_set_is_never_edited_in_place(tmp_path):
    spec_path = _spec(tmp_path)
    themes.freeze(spec_path, _ruled())
    with pytest.raises(ValueError, match="never edited in place"):
        themes.freeze(spec_path, _ruled())


def test_freezing_records_what_forced_the_change(tmp_path):
    spec_path = _spec(tmp_path)
    themes.freeze(spec_path, _ruled(), ratified_by="AP")
    log = (spec_path.parent / "spec-evolution.md").read_text()
    assert "v1 -> v2" in log and "ratified by AP" in log and "efficacy" in log


def test_two_themes_with_one_name_are_refused(tmp_path):
    dup = _ruled() + [{"name": "safety", "definition": "again", "example_claim_ids": ["r2"]}]
    with pytest.raises(ValueError, match="share a name"):
        themes.freeze(_spec(tmp_path), dup)


# --- the table -------------------------------------------------------------------------------

def test_the_themed_table_groups_by_theme_and_keeps_every_quote(tmp_path):
    spec_path = _spec(tmp_path)
    themes.freeze(spec_path, _ruled())
    db = _db(tmp_path, claims=(("r1", "vitamin D lowered TSH", "TSH fell.", "efficacy"),
                               ("r2", "no excess AEs", "Adverse events were similar.", "safety")))
    body = themes.markdown(db, spec_path)
    assert "## efficacy  (1 claim(s))" in body and "## safety  (1 claim(s))" in body
    assert "> TSH fell." in body and "`r1`" in body
    assert "Did it change the primary outcome." in body, "the ratified definition travels"


def test_a_claim_nobody_could_place_is_shown_not_dropped(tmp_path):
    spec_path = _spec(tmp_path)
    themes.freeze(spec_path, _ruled())
    db = _db(tmp_path, claims=(("r1", "a claim", "A sentence.", None),))
    body = themes.markdown(db, spec_path)
    assert "not assigned" in body and "`r1`" in body
    assert "abstention is a result" in body


def test_the_table_refuses_before_a_set_is_frozen(tmp_path):
    with pytest.raises(ValueError, match="freeze one first"):
        themes.markdown(_db(tmp_path), _spec(tmp_path))
