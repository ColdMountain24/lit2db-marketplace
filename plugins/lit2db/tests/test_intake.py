"""The interview as proposals to tick (D-211): the draft is read, the ticks become the ledger.

Offline: the model's reply is a fixed string. What is under test is that every tick, untick, edit
and addition lands in the ledger as the status it should, and that the contract still refuses
what it always refused.
"""
import copy
import json

import pytest

from lit2db import intake, project
from lit2db.contracts import SchemaReadySpec

REPLY = """Here is the draft.
```json
{
 "entity": "uptake measurement",
 "row_options": [
  {"label": "One measurement of one antibody", "description": "Each reported uptake value.",
   "unit_of_analysis": "(antibody, measurement, conditions, source)", "suggested": true,
   "why": "Papers measure one antibody several ways."},
  {"label": "One antibody", "description": "All results merged.", "unit_of_analysis": "(antibody)"},
  {"label": "One paper", "description": "One row per study.", "unit_of_analysis": "(paper)"}
 ],
 "columns": [
  {"name": "antibody", "label": "Antibody", "definition": "Named as the paper names it.",
   "example": "anti-TfR1 bispecific", "kind": "name", "from_question": true,
   "question_words": "large antibodies", "suggest_on": true, "suggest_required": true},
  {"name": "brain_uptake", "label": "Brain uptake", "definition": "How much reached the brain.",
   "example": "2.3", "kind": "number", "from_question": true, "question_words": "get into the brain",
   "suggest_on": true, "suggest_required": true},
  {"name": "species", "label": "Species", "definition": "Where it was measured.",
   "example": "mouse", "kind": "name", "why": "Uptake differs by species.", "suggest_on": true,
   "normalized": true},
  {"name": "strategy", "label": "What helps it cross", "definition": "The strategy used.",
   "kind": "category", "values": ["receptor shuttle", "focused ultrasound", "none", "other"],
   "from_question": true, "suggest_on": true},
  {"name": "dose", "label": "Dose", "definition": "Amount given.", "kind": "number",
   "why": "Uptake rises with dose.", "suggest_on": false},
  {"name": "notes", "label": "Notes", "definition": "Anything else said.", "kind": "text",
   "why": "Context.", "suggest_on": false},
  {"name": "uptake_spread", "label": "Spread and sample size", "definition": "SD and n.",
   "kind": "spread", "qualifies": "brain_uptake", "why": "To pool later.", "suggest_on": false},
  {"name": "orphan_spread", "label": "Spread of nothing", "kind": "spread", "qualifies": "colour"}
 ],
 "identity": ["antibody", "species", "dose", "not_a_column"],
 "purpose": {"ml_task": "regression", "plain": "Compare uptake across papers."},
 "negative_data": "A paper with no uptake value gives no row.",
 "exclusions": ["Reviews", "Conference abstracts"],
 "query": "(\\"blood-brain barrier\\" AND antibody) NOT PUB_TYPE:\\"review\\"",
 "query_plain": "Papers on antibodies and the blood-brain barrier."
}
```"""


@pytest.fixture
def drafted():
    proposal = intake.parse_proposal(REPLY)
    return {"question": "How well do large antibodies get into the brain?",
            "proposal": proposal, "rulings": intake.default_rulings(proposal)}


def _status(spec, iid):
    return next(i for i in spec["ledger"]["items"] if i["item_id"] == iid)


def test_the_draft_is_read_and_repaired_only_structurally():
    p = intake.parse_proposal(REPLY)
    names = [c["name"] for c in p["columns"]]
    assert "orphan_spread" not in names          # a spread beside no number cannot be declared
    assert p["entity"] == "uptake_measurement"
    assert p["identity"] == ["antibody", "species", "dose"]   # the unknown name is dropped
    assert [o["suggested"] for o in p["row_options"]] == [True, False, False]
    assert p["columns"][0]["question_words"] == "large antibodies"


def test_an_unreadable_draft_is_refused_in_plain_words():
    with pytest.raises(ValueError, match="without a table"):
        intake.parse_proposal("I could not do that.")
    with pytest.raises(ValueError, match="no columns"):
        intake.parse_proposal('{"columns": [], "row_options": [{"label": "x"}], "query": "q"}')


def test_ticks_become_the_ledger(drafted):
    spec = intake.build_spec(drafted, query=drafted["proposal"]["query"],
                             result_counts={"hits": 412})
    SchemaReadySpec.model_validate(spec)
    assert _status(spec, "F-antibody")["status"] == "ACCEPTED"
    assert _status(spec, "F-dose")["status"] == "REJECTED"          # unticked suggestion
    assert "unticked" in _status(spec, "F-dose")["reject_reason"]
    assert _status(spec, "Q1")["status"] == "ACCEPTED"
    fields = {f["name"]: f for f in spec["fields"]}
    assert set(fields) == {"antibody", "brain_uptake", "species", "strategy"}
    assert fields["antibody"]["required"] and not fields["species"]["required"]
    assert fields["strategy"]["enum"] == ["receptor shuttle", "focused ultrasound", "none", "other"]
    assert spec["_plan"]["identity_chain"] == [["antibody", "species"], ["antibody"]]
    assert spec["_plan"]["evidence_grounded"] == ["species"]
    assert spec["source_scope"]["queries"][0]["result_counts"] == {"hits": 412}


def test_an_edit_is_recorded_as_one(drafted):
    col = next(c for c in drafted["rulings"]["columns"] if c["name"] == "species")
    col["definition"] = "The animal or human the value was measured in."
    spec = intake.build_spec(drafted, query="edited query")
    item = _status(spec, "F-species")
    assert item["status"] == "ACCEPTED_WITH_EDIT" and "definition" in item["edit_note"]
    assert _status(spec, "Q1")["status"] == "ACCEPTED_WITH_EDIT"


def test_a_column_the_researcher_adds_keeps_their_words(drafted):
    drafted["rulings"]["columns"].append({
        "name": "humanized", "label": "Humanized", "definition": "whether it was humanized",
        "kind": "name", "values": [], "unit": None, "on": True, "required": False,
        "added": True, "verbatim": "whether the antibody was humanized"})
    spec = intake.build_spec(drafted, query="q")
    item = _status(spec, "F-humanized")
    assert item["status"] == "ACCEPTED"
    assert item["payload"]["origin"] == "added by the researcher"
    assert item["payload"]["researcher_verbatim"] == "whether the antibody was humanized"


def test_text_is_checked_by_eye_and_spread_is_the_uncertainty_group(drafted):
    for c in drafted["rulings"]["columns"]:
        if c["name"] in ("notes", "uptake_spread"):
            c["on"] = True
    spec = intake.build_spec(drafted, query="q")
    s = SchemaReadySpec.model_validate(spec)
    fields = {f.name: f for f in s.fields}
    assert fields["notes"].auto_acceptable is False
    assert set(s.uncertainty_groups()["brain_uptake"]) == {
        "spread_type", "spread", "spread_lower", "spread_upper", "spread_level",
        "n_group", "n_total"}


def test_a_spread_without_its_number_is_refused(drafted):
    for c in drafted["rulings"]["columns"]:
        if c["name"] == "uptake_spread":
            c["on"] = True
        if c["name"] == "brain_uptake":
            c["on"] = False
    with pytest.raises(ValueError, match="beside a number that is not ticked"):
        intake.build_spec(drafted, query="q")


def test_nothing_ticked_is_refused(drafted):
    for c in drafted["rulings"]["columns"]:
        c["on"] = False
    with pytest.raises(ValueError, match="tick at least one column"):
        intake.build_spec(drafted, query="q")


def test_the_contract_still_refuses_an_unratified_field(drafted):
    spec = intake.build_spec(drafted, query="q")
    bad = copy.deepcopy(spec)
    _status(bad, "F-antibody")["status"] = "PROPOSED"
    with pytest.raises(ValueError, match="not ACCEPTED"):
        SchemaReadySpec.model_validate(bad)


def test_plan_runs_on_what_the_page_built(drafted, tmp_path):
    spec = intake.build_spec(drafted, query="q")
    p = project.paths(tmp_path)
    project.init(tmp_path)
    p["spec"].write_text(json.dumps(spec), encoding="utf-8")
    store = p["sources"] / "PMC1"
    store.mkdir(parents=True)
    (store / "full.txt").write_text("text", encoding="utf-8")
    p["search"].write_text(json.dumps({"papers": [
        {"source_id": "PMC1", "outcome": "full_text", "doi": "10.1/x"}]}), encoding="utf-8")
    plan = spec["_plan"]
    out = project.plan(tmp_path, entity_type=plan["entity_type"],
                       identity_field=plan["identity_field"], models=["sonnet"] * 3,
                       judge_model="opus", hunter_model="sonnet", judge_audit_fraction=0.2,
                       identity_chain=plan["identity_chain"],
                       ordinal_within=plan["ordinal_within"],
                       evidence_grounded=plan["evidence_grounded"])
    cfg = json.loads(p["config"].read_text())
    assert out["papers"] == 1
    assert "species" in cfg["evidence_grounded_fields"]
    assert "strategy" in cfg["evidence_grounded_fields"]      # every category, as before
