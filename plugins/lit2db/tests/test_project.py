"""A project on disk (D-199): the hand steps between a ratified spec and a spreadsheet.

Offline. Europe PMC is replaced by a fake getter, because what is under test is what this module
does with each kind of hit, not whether the service is up. The live path was exercised by hand on
2026-09-16: 4,789 hits for a vitamin D trial query, 14 of the first 50 readable in full.
"""
import csv
import json
import pathlib
import subprocess
import sys

import pytest

from lit2db import output, project
from lit2db.contracts import (
    CorpusQuery, FieldSpec, LedgerItem, MLTask, RatificationLedger, RatificationStatus,
    SchemaReadySpec, SourceScope,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent

JATS = b"""<article><front><article-meta>
<article-title>Widgets in the wild</article-title>
<abstract><p>We report widget A.</p></abstract>
</article-meta></front>
<body><sec><title>Results</title><p>Widget A was isolated from site 3.</p></sec></body>
</article>"""


def _hit(**kw):
    base = {"pmid": None, "pmcid": None, "doi": None, "title": "t", "pubYear": "2022",
            "isOpenAccess": "N", "inEPMC": "N", "license": None,
            "journalInfo": {"journal": {"title": "J"}}, "pubTypeList": {"pubType": []}}
    base.update(kw)
    return base


HITS = [
    _hit(pmcid="PMC111", pmid="1", title="open", isOpenAccess="Y", inEPMC="Y", license="cc by"),
    _hit(pmid="2", title="closed with abstract", abstractText="Widget B was inferred."),
    _hit(pmid="3", title="closed, nothing"),
    _hit(pmcid="PMC555", pmid="5", title="meeting abstract", isOpenAccess="Y", inEPMC="Y",
         pubTypeList={"pubType": ["Abstract"]}),
    _hit(pmcid="PMC444", pmid="4", title="retracted", isOpenAccess="Y", inEPMC="Y",
         pubTypeList={"pubType": ["Journal Article", "Retracted Publication"]}),
]


class FakeEPMC:
    def __init__(self, hits=HITS):
        self.hits, self.calls = hits, []

    def __call__(self, url):
        self.calls.append(url)
        if "/search?" in url:
            return json.dumps({"hitCount": 57, "nextCursorMark": "*",
                               "resultList": {"result": self.hits}}).encode()
        if url.endswith("/fullTextXML"):
            return JATS
        raise AssertionError(f"unexpected request {url}")


def _spec(field_ledger="F1"):
    ledger = RatificationLedger(items=[
        LedgerItem(item_id="F1", kind="field", summary="ok", status=RatificationStatus.ACCEPTED),
        LedgerItem(item_id="F2", kind="field", summary="no", status=RatificationStatus.PROPOSED),
        LedgerItem(item_id="Q1", kind="source_scope", summary="q",
                   status=RatificationStatus.ACCEPTED)])
    return SchemaReadySpec(
        research_question="Which widgets are reported?", ml_task=MLTask.regression,
        unit_of_analysis="(widget, source)",
        fields=[FieldSpec(name="widget_name", type="str", definition="Name as printed.",
                          provenance_granularity="per-source", ledger_item_id="F1"),
                FieldSpec(name="basis", type="enum", enum=["isolated", "inferred"],
                          definition="How it is evidenced.", provenance_granularity="per-source",
                          ledger_item_id="F1")],
        negative_data_policy="Nothing qualifying is a recorded negative.",
        source_scope=SourceScope(adapters=["literature"], queries=[
            CorpusQuery(corpus="europepmc", query='"widget" AND OPEN_ACCESS:y',
                        ledger_item_id="Q1")]),
        ledger=ledger, spec_version="test-v1")


def _write_spec(tmp_path, spec=None):
    p = project.paths(tmp_path)
    p["spec"].parent.mkdir(parents=True, exist_ok=True)
    p["spec"].write_text((spec or _spec()).model_dump_json(), encoding="utf-8")


def _search(tmp_path):
    return json.loads(project.paths(tmp_path)["search"].read_text())


# --- fetch --------------------------------------------------------------------------------

def test_fetch_runs_the_RATIFIED_query_verbatim(tmp_path):
    """The corpus boundary is researcher substance. Rewriting the query here would move it."""
    _write_spec(tmp_path)
    fake = FakeEPMC()
    project.fetch(tmp_path, limit=10, allow_abstracts=True, get=fake, pause=0)
    assert _search(tmp_path)["query"] == '"widget" AND OPEN_ACCESS:y'
    assert "%22widget%22+AND+OPEN_ACCESS%3Ay" in fake.calls[0]


def test_fetch_refuses_a_spec_that_is_not_ratified(tmp_path):
    bad = _spec().model_dump(mode="json")
    bad["fields"].append({"name": "sneaky", "type": "str", "definition": "x",
                          "provenance_granularity": "x", "ledger_item_id": "F2"})
    p = project.paths(tmp_path)
    p["spec"].parent.mkdir(parents=True, exist_ok=True)
    p["spec"].write_text(json.dumps(bad))
    with pytest.raises(Exception):
        project.fetch(tmp_path, limit=10, allow_abstracts=True, get=FakeEPMC(), pause=0)


def test_every_hit_gets_an_outcome_and_none_is_dropped(tmp_path):
    _write_spec(tmp_path)
    out = project.fetch(tmp_path, limit=10, allow_abstracts=True, get=FakeEPMC(), pause=0)
    by_title = {e["title"]: e for e in _search(tmp_path)["papers"]}

    assert by_title["open"]["outcome"] == "full_text"
    assert by_title["closed with abstract"]["outcome"] == "abstract_only"
    assert by_title["closed, nothing"]["outcome"] == "unreachable"
    assert by_title["retracted"]["outcome"] == "retracted"
    assert by_title["meeting abstract"]["why"] == "published as a conference abstract"
    assert out["counts"] == {"hits": 57, "examined": 5, "full_text": 2, "abstract_only": 1,
                             "unreachable": 1, "retracted": 1}


def test_a_retracted_paper_is_never_stored(tmp_path):
    """The wave driver stamps every source it is handed `active`. So the refusal has to happen
    before a store exists, or a retracted paper enters the run looking like any other."""
    _write_spec(tmp_path)
    fake = FakeEPMC()
    project.fetch(tmp_path, limit=10, allow_abstracts=True, get=fake, pause=0)
    assert not (project.paths(tmp_path)["sources"] / "PMC444").exists()
    assert not any("PMC444" in c for c in fake.calls)


def test_abstracts_are_only_stored_when_the_caller_admits_them(tmp_path):
    _write_spec(tmp_path)
    project.fetch(tmp_path, limit=10, allow_abstracts=False, get=FakeEPMC(), pause=0)
    by_title = {e["title"]: e for e in _search(tmp_path)["papers"]}
    assert by_title["closed with abstract"]["outcome"] == "unreachable"
    assert "abstracts not admitted" in by_title["closed with abstract"]["why"]


def test_stores_carry_their_acquisition_route_and_licence(tmp_path):
    """D-141: which permitted route a document came from is a legal artefact, not hygiene."""
    _write_spec(tmp_path)
    project.fetch(tmp_path, limit=10, allow_abstracts=True, get=FakeEPMC(), pause=0)
    src = project.paths(tmp_path)["sources"]
    full = json.loads((src / "PMC111" / "meta.json").read_text())
    abst = json.loads((src / "PMID_2" / "meta.json").read_text())
    assert full["acquisition_route"] == "europepmc_open_access_full_text"
    assert full["license"] == "cc by" and full["source_text_scope"] == "full_text"
    assert abst["acquisition_route"] == "europepmc_abstract"
    assert abst["source_text_scope"] != "full_text"


def test_fetch_is_resumable_and_does_not_download_twice(tmp_path):
    _write_spec(tmp_path)
    project.fetch(tmp_path, limit=10, allow_abstracts=True, get=FakeEPMC(), pause=0)
    again = FakeEPMC()
    project.fetch(tmp_path, limit=10, allow_abstracts=True, get=again, pause=0)
    assert not any(c.endswith("/fullTextXML") for c in again.calls)


def test_nothing_is_written_under_the_plugin(tmp_path):
    """The plugin folder is replaced on update. A tester's corpus written there is lost."""
    _write_spec(tmp_path)
    project.fetch(tmp_path, limit=10, allow_abstracts=True, get=FakeEPMC(), pause=0)
    assert project.paths(tmp_path)["base"] == tmp_path.resolve() / "lit2db"
    assert not str(project.paths(tmp_path)["base"]).startswith(str(ROOT))


# --- plan ---------------------------------------------------------------------------------

def _fetched(tmp_path):
    _write_spec(tmp_path)
    project.fetch(tmp_path, limit=10, allow_abstracts=True, get=FakeEPMC(), pause=0)


def _plan(tmp_path, **over):
    kw = dict(entity_type="widget", identity_field="widget_name", models=["sonnet"] * 3,
              judge_model="opus", hunter_model="sonnet", judge_audit_fraction=0.2)
    kw.update(over)
    return project.plan(tmp_path, **kw)


def test_plan_writes_a_config_the_runner_accepts(tmp_path):
    _fetched(tmp_path)
    out = _plan(tmp_path)
    assert out["papers"] == 3
    dry = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_wave.py"), "--config",
                          out["config"], "--dry-run"], capture_output=True, text=True)
    assert dry.returncode == 0, dry.stdout + dry.stderr


def test_preflight_refuses_a_config_missing_a_key_the_pipeline_indexes(tmp_path):
    """The first clean-room run passed preflight, then lost two papers to KeyError
    'identity_primary' after their passes were paid for. Preflight exists to stop that."""
    _fetched(tmp_path)
    cfg_path = pathlib.Path(_plan(tmp_path)["config"])
    cfg = json.loads(cfg_path.read_text())
    assert cfg["identity_primary"] == "widget_name" and cfg["run_timestamp"]
    del cfg["identity_primary"]
    cfg_path.write_text(json.dumps(cfg))
    dry = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_wave.py"), "--config",
                          str(cfg_path), "--dry-run"], capture_output=True, text=True)
    assert dry.returncode != 0 and "identity_primary" in dry.stdout + dry.stderr


def test_the_prompts_carry_no_domain_and_no_unfilled_project_marker(tmp_path):
    """The templates ship in a domain-blind plugin. Project specifics arrive only as the rendered
    ratified spec at run time, plus two structural names stamped at plan time."""
    _fetched(tmp_path)
    _plan(tmp_path)
    prompts = project.paths(tmp_path)["prompts"]
    extract = (prompts / "extract.md").read_text()
    assert "{SPEC_CONTEXT}" in extract and "<<" not in extract
    assert "`widget`" in extract and "`widget_name`" in extract
    for name in ("extract", "judge", "hunter"):
        body = (project.TEMPLATES / f"{name}.md").read_text().lower()
        for word in ("terpen", "compound", "organism", "enzyme", "nmr"):
            assert word not in body, f"template {name}.md carries domain word {word!r}"


def test_enum_fields_are_grounded_on_evidence_not_by_substring(tmp_path):
    """D-061, found missing on the first clean-room run: every record scored 0.3 because a label
    like `improved` never appears verbatim in its quote."""
    _fetched(tmp_path)
    cfg = json.loads(pathlib.Path(_plan(tmp_path)["config"]).read_text())
    assert cfg["evidence_grounded_fields"] == ["basis"]


def test_a_measurement_grain_project_can_state_its_identity_chain(tmp_path):
    """From the first BBB wave (2026-09-18). A bare identity means "one row per named thing",
    so a project whose row is a measurement collapsed every measurement of one molecule into
    one row: 3 records where 6 belonged, from the same three passes."""
    _fetched(tmp_path)
    out = _plan(tmp_path, identity_chain=[["widget_name", "basis"], ["widget_name"]],
                ordinal_within=["widget_name"])
    assert out["identity"] == {"chain": [["widget_name", "basis"], "widget_name"],
                               "ordinal_within": ["widget_name"]}
    cfg = json.loads(pathlib.Path(out["config"]).read_text())
    assert cfg["identity_primary"] == "widget_name", "the chain does not change what names a row"


def test_an_identity_tier_naming_an_unratified_field_is_refused(tmp_path):
    _fetched(tmp_path)
    with pytest.raises(ValueError, match="nobody ratified"):
        _plan(tmp_path, identity_chain=[["widget_name", "dose"]])


def test_replanning_never_silently_restores_what_someone_fixed_by_hand(tmp_path):
    """The identity chain had to be hand-written into run.json on the first BBB wave, and
    re-running plan would have put the collapsing version back with nothing said."""
    _fetched(tmp_path)
    cfg_path = pathlib.Path(_plan(tmp_path)["config"])
    _plan(tmp_path)                                   # untouched: re-plans without ceremony

    cfg = json.loads(cfg_path.read_text())
    cfg["identity_fields"]["widget"] = {"chain": [["widget_name", "basis"]],
                                        "ordinal_within": ["widget_name"]}
    cfg_path.write_text(json.dumps(cfg))

    with pytest.raises(ValueError, match="edited since plan wrote it"):
        _plan(tmp_path)
    assert json.loads(cfg_path.read_text())["identity_fields"]["widget"] != "widget_name"

    _plan(tmp_path, force=True)
    assert json.loads(cfg_path.read_text())["identity_fields"]["widget"] == {
        "chain": ["widget_name"], "ordinal_within": []}
    kept = json.loads((cfg_path.parent / "run.json.bak").read_text())
    assert kept["identity_fields"]["widget"]["chain"] == [["widget_name", "basis"]]


def test_a_normalized_field_can_be_grounded_on_its_evidence(tmp_path):
    """From the first BBB wave: species "mouse" against a quote reading "mice", unit "%/g"
    against "2%ID/g", a molecule key that is a slug. All correct, all scored 0 by a substring
    test, and the weakest link in 42 of 50 records."""
    _fetched(tmp_path)
    cfg = json.loads(pathlib.Path(
        _plan(tmp_path, evidence_grounded=["widget_name"])["config"]).read_text())
    assert cfg["evidence_grounded_fields"] == ["basis", "widget_name"], "enums stay automatic"
    with pytest.raises(ValueError, match="nobody ratified"):
        _plan(tmp_path, evidence_grounded=["not_a_field"], force=True)


def test_a_field_ratified_as_never_auto_acceptable_is_kept_out_of_the_record_score(tmp_path):
    """`auto_acceptable=False` means human-review output by design. Scoring a record as its
    weakest field while including one of those holds down every record that carries it."""
    spec = _spec()
    spec.fields[1].auto_acceptable = False
    _write_spec(tmp_path, spec)
    project.fetch(tmp_path, limit=10, allow_abstracts=True, get=FakeEPMC(), pause=0)
    cfg = json.loads(pathlib.Path(_plan(tmp_path)["config"])
                     .read_text())
    assert cfg["review_lane"] == ["basis"]


def test_grounding_mode_is_lexical_unless_asked_and_only_takes_the_two_it_supports(tmp_path):
    _fetched(tmp_path)
    assert json.loads(pathlib.Path(_plan(tmp_path)["config"]).read_text())[
        "grounding_mode"] == "lexical"
    cfg = json.loads(pathlib.Path(
        _plan(tmp_path, grounding_mode="llm", force=True)["config"]).read_text())
    assert cfg["grounding_mode"] == "llm" and cfg["grounding_model"]
    with pytest.raises(ValueError, match="lexical"):
        _plan(tmp_path, grounding_mode="fuzzy", force=True)


def test_plan_refuses_an_identity_field_nobody_ratified(tmp_path):
    _fetched(tmp_path)
    with pytest.raises(ValueError, match="not a ratified field"):
        _plan(tmp_path, identity_field="compound_name")


def test_plan_does_not_switch_on_the_chemistry_lookups_for_other_domains(tmp_path):
    _fetched(tmp_path)
    cfg = json.loads(pathlib.Path(_plan(tmp_path)["config"]).read_text())
    assert cfg["resolve_structures"] is False


def test_plan_refuses_before_anything_is_fetched(tmp_path):
    _write_spec(tmp_path)
    with pytest.raises(ValueError, match="run fetch first"):
        _plan(tmp_path)


# --- export -------------------------------------------------------------------------------

def _field(name, value, quote):
    return {"field_name": name, "value": value,
            "provenance": {"source_id": "PMC111", "verbatim_quote": quote}}


def test_calibrate_reports_and_refuses_to_pick_a_bar(tmp_path):
    """Confirming candidates is the calibration set (calibration.py). This is the path from a
    project folder to that table, and like the module it reports without choosing."""
    _fetched(tmp_path)
    db = str(project.paths(tmp_path)["db"])
    for rid, comp, verdict, judge in (("r1", 0.9, "right", "supported"),
                                      ("r2", 0.5, "wrong", "partial"),
                                      ("r3", 0.9, "cant_tell", "supported")):
        rec = {"record_id": rid, "entity_type": "widget",
               "fields": [_field("widget_name", rid, "q")]}
        output.record_candidate(rec, comp, {"decision": "deny", "reasons": ["x"],
                                            "judge_verdict": judge}, db, "PMC111")
        output.record_adjudication(rid, "PMC111", verdict, db)

    out = project.calibrate(tmp_path)
    assert out["reviewed"] == 3
    assert out["verdicts"] == {"right": 1, "wrong": 1, "cant_tell": 1}
    bars = {b["bar"]: b for b in out["frontier"]}
    assert bars[0.9]["n"] == 1 and bars[0.9]["precision"] == 1.0, (
        "cant_tell is held out of the denominator, never counted as wrong")
    assert bars[0.5]["n"] == 2 and bars[0.5]["precision"] == 0.5
    assert "yours to ratify" in out["note"]


def test_calibrate_says_what_to_do_when_nothing_has_been_reviewed(tmp_path):
    _fetched(tmp_path)
    project.paths(tmp_path)["db"].parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    sqlite3.connect(str(project.paths(tmp_path)["db"])).close()
    out = project.calibrate(tmp_path)
    assert out["reviewed"] == 0 and "judge-supported" in out["next"]


def test_export_writes_all_three_tiers_with_quotes(tmp_path):
    _fetched(tmp_path)
    db = str(project.paths(tmp_path)["db"])
    rec = {"record_id": "r1", "entity_type": "widget",
           "fields": [_field("widget_name", "Widget A", "Widget A was isolated from site 3.")]}
    con = output.connect(db)
    con.execute("insert into records values (?,?,?,?,?,?,?)",
                ("PMC111:r1", "widget", 1.0, "auto_accept", "active", json.dumps(rec), "now"))
    con.commit()
    con.close()
    output.record_candidate(rec, 1.0, {"decision": "allow", "reasons": []}, db, "PMC111")
    held = {"record_id": "r1", "entity_type": "widget",
            "fields": [_field("widget_name", "Widget B", "Widget B was inferred.")]}
    output.record_candidate(held, 0.5, {"decision": "deny",
                                        "reasons": ["composite 0.500 < auto-accept 0.95"]},
                            db, "PMID_2")
    output.record_adjudication("r1", "PMID_2", "right", db, note="yes, B")

    out = project.export(tmp_path)
    results = project.paths(tmp_path)["results"]
    assert (out["papers"], out["candidates"], out["accepted"]) == (5, 1, 1)

    accepted = list(csv.DictReader((results / "accepted.csv").open()))
    assert accepted[0]["widget_name"] == "Widget A"
    assert accepted[0]["widget_name (quote)"] == "Widget A was isolated from site 3."

    cands = list(csv.DictReader((results / "candidates.csv").open()))
    assert cands[0]["widget_name"] == "Widget B"
    assert "auto-accept" in cands[0]["why it was not accepted"]
    assert cands[0]["your verdict"] == "right" and cands[0]["your note"] == "yes, B"

    papers = {r["title"]: r for r in csv.DictReader((results / "papers.csv").open())}
    assert papers["retracted"]["what we could read"] == "retracted"
    assert papers["closed, nothing"]["read by the pipeline"] == "no"


def test_export_before_any_run_still_gives_the_reading_list(tmp_path):
    """The floor /lit2db-start promises: even if extraction disappoints, the researcher has the
    screened list of papers. An export that needed a database first would withhold it."""
    _fetched(tmp_path)
    out = project.export(tmp_path)
    assert out["papers"] == 5 and out["accepted"] == 0 and out["candidates"] == 0
