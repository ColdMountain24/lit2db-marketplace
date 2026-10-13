"""Uncertainty beside a measured value: spread type, spread values, sample size (D-203).

What has to hold, and each is a way it could fail quietly:
  * the vocabulary is ClinicalTrials.gov's, with what was left out written down;
  * a group is ratified like any field, and a partial group cannot reach a spec;
  * absent, 'not reported' and 0 are three facts, and the gate tells them apart;
  * a spread whose type and values disagree is held, because it cannot be converted;
  * both enforcement points apply the rule, and the driver cannot run without it.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.contracts import (                                            # noqa: E402
    RatificationLedger, LedgerItem, RatificationStatus, FieldSpec, CorpusQuery, SourceScope,
    SchemaReadySpec, MLTask,
)
from lit2db.gate import gate_reasons, populated_fields                    # noqa: E402
from lit2db.pipeline import assemble, claim_text                          # noqa: E402
from lit2db.spec_context import spec_context                              # noqa: E402
from lit2db.uncertainty import (                                          # noqa: E402
    NOT_REPORTED, ROLES, SPREAD_TYPES, companion_fields, companion_name, uncertainty_reasons,
)

HOOK = ROOT / "hooks" / "pretooluse_write_gate.py"
GROUPS = {"x": {r: companion_name("x", r) for r in ROLES}}


# --- the vocabulary ----------------------------------------------------------------------

# ClinicalTrials.gov API v2 `MeasureDispersionType`, as served by /api/v2/studies/enums on
# 2026-10-03. Pinned here so the claim "follows ClinicalTrials.gov" is checked, not remembered.
CTGOV = {
    "STANDARD_DEVIATION": "standard deviation", "STANDARD_ERROR": "standard error",
    "INTER_QUARTILE_RANGE": "interquartile range", "FULL_RANGE": "full range",
    "CONFIDENCE_80": "confidence interval", "CONFIDENCE_90": "confidence interval",
    "CONFIDENCE_95": "confidence interval", "CONFIDENCE_975": "confidence interval",
    "CONFIDENCE_99": "confidence interval", "CONFIDENCE_OTHER": "confidence interval",
    "NA": None, "GEOMETRIC_COEFFICIENT": None,      # left out on purpose, see uncertainty.py
}


def test_every_clinicaltrials_member_maps_or_is_left_out_on_purpose():
    ours = {v for v in CTGOV.values() if v}
    assert ours | {NOT_REPORTED} == set(SPREAD_TYPES)
    assert [k for k, v in CTGOV.items() if v is None] == ["NA", "GEOMETRIC_COEFFICIENT"]


# --- the spec ----------------------------------------------------------------------------

def _spec(extra_fields=(), status=RatificationStatus.ACCEPTED, measured_type="float"):
    fields = [FieldSpec(name="x", type=measured_type, definition="the measured value",
                        provenance_granularity="per-source", ledger_item_id="F1")]
    fields += [FieldSpec(**f) for f in extra_fields]
    return SchemaReadySpec(
        research_question="q", ml_task=MLTask.regression, unit_of_analysis="(w, s)",
        fields=fields, negative_data_policy="p",
        source_scope=SourceScope(adapters=["literature"],
                                 queries=[CorpusQuery(corpus="europepmc", query="x",
                                                      ledger_item_id="Q1")]),
        ledger=RatificationLedger(items=[
            LedgerItem(item_id="F1", kind="field", summary="ok",
                       status=RatificationStatus.ACCEPTED),
            LedgerItem(item_id="U1", kind="field", summary="uncertainty", status=status),
            LedgerItem(item_id="Q1", kind="source_scope", summary="q",
                       status=RatificationStatus.ACCEPTED)]),
        spec_version="t")


def test_the_seven_companions_build_a_spec_once_ratified():
    s = _spec(companion_fields("x", "U1"))
    assert s.uncertainty_groups() == GROUPS


def test_a_proposed_group_cannot_reach_a_spec():
    """The definitions are the agent's proposal; the researcher ratifies them like any field."""
    with pytest.raises(Exception, match="not ACCEPTED"):
        _spec(companion_fields("x", "U1"), status=RatificationStatus.PROPOSED)


def test_a_partial_group_is_refused():
    with pytest.raises(Exception, match="missing"):
        _spec([f for f in companion_fields("x", "U1") if f["uncertainty_role"] != "n_total"])


def test_the_spread_type_must_carry_exactly_the_vocabulary():
    fs = companion_fields("x", "U1")
    fs[0]["enum"] = [t for t in SPREAD_TYPES if t != NOT_REPORTED]
    with pytest.raises(Exception, match="exactly the spread types"):
        _spec(fs)


def test_a_companion_of_a_non_numeric_or_missing_field_is_refused():
    with pytest.raises(Exception, match="not a plain numeric"):
        _spec(companion_fields("x", "U1"), measured_type="str")
    with pytest.raises(Exception, match="not a field of this spec"):
        _spec(companion_fields("y", "U1"))


def test_two_companions_in_one_role_are_refused():
    fs = companion_fields("x", "U1")
    fs.append(dict(fs[1], name="x_spread_again"))
    with pytest.raises(Exception, match="two 'spread' companions"):
        _spec(fs)


def test_a_held_companion_is_refused():
    """A review-lane field is stripped from the written row, so the row would land without it."""
    fs = companion_fields("x", "U1")
    fs[2]["auto_acceptable"] = False
    with pytest.raises(Exception, match="auto_acceptable"):
        _spec(fs)


def test_role_and_measured_field_travel_together():
    with pytest.raises(Exception, match="travel together"):
        FieldSpec(name="a", type="float", definition="d", provenance_granularity="p",
                  ledger_item_id="U1", uncertainty_role="spread")


def test_a_spec_without_a_group_renders_and_plans_as_before():
    s = _spec()
    assert s.uncertainty_groups() == {}
    assert "Uncertainty" not in spec_context(s)


def test_the_extractor_is_told_not_reported_is_an_answer():
    out = spec_context(_spec(companion_fields("x", "U1")))
    assert "# Uncertainty beside a measured value" in out
    assert "Write exactly `not reported`" in out and "Never write 0" in out


# --- the gate ----------------------------------------------------------------------------

def _f(name, value):
    return {"field_name": name, "value": value, "route": "auto_accept",
            "contradiction_search": "clean",
            "provenance": {"kind": "literature", "source_id": "PMC1", "source_status": "active",
                           "retrieval_timestamp": "2026-01-01T00:00:00Z",
                           "producing_process": "t", "verbatim_quote": "q", "char_offset": 0}}


def _rec(**values):
    return {"record_id": "r1", "entity_type": "t", "judge_verdict": "supported",
            "fields": [_f(k, v) for k, v in values.items()]}


def _u(**companions):
    """A record carrying x and the named companions (role=value)."""
    return _rec(x=1.2, **{companion_name("x", r): v for r, v in companions.items()})


def _gate(rec, **kw):
    return gate_reasons(rec, 1.0, 0.95, require_contradiction_search=True,
                        uncertainty_groups=GROUPS, **kw)


NR3 = dict(spread_type=NOT_REPORTED, n_group=NOT_REPORTED, n_total=NOT_REPORTED)


def test_no_declared_group_means_no_condition():
    assert gate_reasons(_rec(x=1.2), 1.0, 0.95, require_contradiction_search=True) == []
    assert uncertainty_reasons(_rec(x=1.2), {}) == []


def test_absent_is_not_not_reported():
    reasons = _gate(_rec(x=1.2))
    assert len(reasons) == 3 and all("is absent" in r for r in reasons)
    assert all("a reading that did not answer" in r for r in reasons)


def test_not_reported_is_an_answer_and_passes():
    assert _gate(_u(**NR3)) == []


def test_zero_is_not_unknown():
    reasons = _gate(_u(**{**NR3, "n_group": 0}))
    assert len(reasons) == 1 and "never 0" in reasons[0]


@pytest.mark.parametrize("junk", ["", "NR", "n/a", "unknown", "-"])
def test_a_blank_or_abbreviation_is_unreadable_not_unknown(junk):
    assert _gate(_u(**{**NR3, "n_total": junk}))
    sd = _gate(_u(spread_type="standard deviation", spread=junk, n_group=6, n_total=24))
    assert len(sd) == 1 and "neither a number nor exactly 'not reported'" in sd[0]


def test_a_standard_deviation_with_its_n_passes():
    assert _gate(_u(spread_type="standard deviation", spread=0.12, n_group=6, n_total=24)) == []
    assert _gate(_u(spread_type="standard error", spread="0.05", n_group=3,
                    n_total=NOT_REPORTED)) == []


def test_type_and_values_must_agree():
    sd_with_pair = _gate(_u(spread_type="standard deviation", spread=0.1, spread_lower=1,
                            spread_upper=2, n_group=6, n_total=6))
    assert len(sd_with_pair) == 2 and all("has no such value" in r for r in sd_with_pair)
    ci_no_level = _gate(_u(spread_type="confidence interval", spread_lower=1, spread_upper=2,
                           n_group=6, n_total=6))
    assert len(ci_no_level) == 1 and "needs 'x_spread_level'" in ci_no_level[0]
    iqr_as_sd = _gate(_u(spread_type="interquartile range", spread=0.4, n_group=6, n_total=6))
    assert any("has no such value" in r for r in iqr_as_sd)
    assert any("needs 'x_spread_lower'" in r for r in iqr_as_sd)


def test_a_confidence_interval_with_its_level_passes_and_a_bad_level_does_not():
    ok = dict(spread_type="confidence interval", spread_lower=0.8, spread_upper=1.6,
              spread_level=95, n_group=10, n_total=20)
    assert _gate(_u(**ok)) == []
    assert _gate(_u(**{**ok, "spread_level": NOT_REPORTED})) == []
    for bad in (0, 100, 120):
        assert any("not a percentage" in r for r in _gate(_u(**{**ok, "spread_level": bad})))
    assert any("above upper limit" in r
               for r in _gate(_u(**{**ok, "spread_lower": 2, "spread_upper": 1})))


def test_an_unlabelled_plus_minus_is_kept_under_not_reported():
    assert _gate(_u(**NR3, spread=3)) == []
    assert _gate(_u(**NR3, spread_lower=1, spread_upper=2)) == []
    one_end = _gate(_u(**NR3, spread_lower=1))
    assert len(one_end) == 1 and "both" in one_end[0]
    assert any("has no such value" in r for r in _gate(_u(**NR3, spread_level=95)))


def test_sample_size_ranges_and_bounds():
    base = dict(spread_type=NOT_REPORTED, n_total=NOT_REPORTED)
    assert _gate(_u(**base, n_group=[3, 5])) == []
    assert _gate(_u(**base, n_group=[5, 3]))
    assert _gate(_u(**base, n_group=2.5))
    assert any("larger than n_total" in r for r in
               _gate(_u(spread_type=NOT_REPORTED, n_group=12, n_total=6)))


def test_a_row_with_nothing_measured_is_not_held():
    assert _gate(_rec(other="a")) == []


def test_case_and_spacing_are_typography():
    assert _gate(_u(spread_type="Standard Deviation", spread=1, n_group="Not  Reported",
                    n_total=" NOT REPORTED ")) == []


def test_an_unreadable_declaration_fails_closed():
    assert uncertainty_reasons(_u(**NR3), ["x"])
    assert uncertainty_reasons(_u(**NR3), {"x": {"spread_type": "x_spread_type"}})


def test_a_review_lane_companion_reads_as_absent():
    reasons = _gate(_u(**NR3), review_lane=["x_n_total"])
    assert len(reasons) == 1 and "'x_n_total' is absent" in reasons[0]


def test_not_reported_does_not_count_toward_completeness():
    rec = _u(**NR3)
    assert populated_fields(rec) == sorted(["x", "x_n_group", "x_n_total", "x_spread_type"])
    assert populated_fields(rec, companions=["x_n_group", "x_n_total", "x_spread_type"]) == ["x"]
    reasons = _gate(rec, min_populated_fields=2)
    assert len(reasons) == 1 and "populates 1 field(s)" in reasons[0]


# --- both enforcement points, and the write ------------------------------------------------

def _hook(record, groups):
    tool_input = {"record": record, "composite_confidence": 1.0, "db_path": "",
                  "autoaccept": -1.0, "uncertainty_groups": groups}
    payload = {"session_id": "t", "hook_event_name": "PreToolUse",
               "tool_name": "mcp__lit2db__gate_upsert", "tool_input": tool_input}
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                         capture_output=True, text=True, env=dict(os.environ))
    return json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"]


def test_the_hook_and_the_tool_apply_one_rule():
    whole, bare = _u(**NR3), _rec(x=1.2)
    assert _hook(whole, GROUPS) == "allow" and gate_reasons(whole, 1.0, 0.95,
                                                            uncertainty_groups=GROUPS) == []
    assert _hook(bare, GROUPS) == "deny" and gate_reasons(bare, 1.0, 0.95,
                                                          uncertainty_groups=GROUPS)
    assert _hook(bare, {}) == "allow", "no declaration, no condition, at the hook too"


PROV = {"kind": "literature", "source_id": "PMC1", "retrieval_timestamp": "2026-07-27T00:00:00Z",
        "producing_process": "test", "process_fingerprint": "a" * 64,
        "verbatim_quote": "the value was 1.2 (n = 6)", "char_offset": 0, "section": "Results",
        "source_status": "active", "source_chars_total": 100, "source_chars_read": 100}


def _full(**values):
    return {"record_id": "r1", "entity_type": "t", "judge_verdict": "supported",
            "fields": [{"field_name": k, "value": v, "provenance": dict(PROV),
                        "route": "auto_accept", "contradiction_search": "clean"}
                       for k, v in values.items()]}


def test_the_write_holds_a_value_without_its_uncertainty(tmp_path):
    from lit2db.output import upsert
    db = str(tmp_path / "t.db")
    g = upsert(_full(x=1.2), 1.0, db, uncertainty_groups=GROUPS)
    assert not g["written"] and any("is absent" in r for r in g["reasons"])
    whole = _full(x=1.2, **{companion_name("x", r): v for r, v in NR3.items()})
    assert upsert(whole, 1.0, db, uncertainty_groups=GROUPS)["written"]


def test_the_mcp_tool_takes_the_groups(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("srv_u", ROOT / "mcp" / "lit2db_mcp" /
                                                  "server.py")
    srv = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(srv)
    except ImportError:
        pytest.skip("mcp package not installed in this interpreter")
    g = srv.gate_upsert(record=_full(x=1.2), composite_confidence=1.0,
                        db_path=str(tmp_path / "t.db"), uncertainty_groups=GROUPS)
    assert not g["written"] and any("uncertainty of 'x'" in r for r in g["reasons"])


# --- extraction, grounding and the judge ---------------------------------------------------

QUOTE = "Brain uptake was 1.2 in treated animals."


@pytest.fixture()
def store(tmp_path):
    d = tmp_path / "PMC_u"
    d.mkdir()
    (d / "full.txt").write_text(QUOTE, encoding="utf-8")
    (d / "sections.json").write_text(json.dumps(
        [{"title": "Results", "depth": 1, "start": 0, "end": len(QUOTE)}]), encoding="utf-8")
    return tmp_path


def _assemble(store_root, fields, grounder=None, groups=GROUPS):
    cfg = {"stores": str(store_root), "extract_prompt": __file__, "models": ["a", "b", "c"],
           "producing_process": "t", "run_timestamp": "2026-10-03T00:00:00Z",
           "identity_primary": "name", "evidence_grounded_fields": [], "review_lane": [],
           "uncertainty_groups": groups}
    rec = {"record_id": "r1", "entity_type": "t",
           "fields": [{"field_name": n, "value": v, "verbatim_quote": QUOTE}
                      for n, v in [("name", "treated")] + list(fields)]}
    out, _ = assemble("PMC_u", cfg, {"records": [rec], "_passes": [[rec]] * 3},
                      {"state_by_record": {}, "contradictions": []}, grounder=grounder)
    return {f["field_name"]: f["confidence_components"] for f in out[0]["fields"]}


def test_not_reported_is_anchored_on_its_evidence_only_for_a_declared_companion(store):
    cc = _assemble(store, [("x", 1.2), ("x_n_group", NOT_REPORTED), ("other", NOT_REPORTED)])
    assert cc["x"]["c_grounded"] == 1.0
    assert cc["x_n_group"]["c_grounded"] == 1.0
    assert cc["x_n_group"]["_grounding_mode"] == "not_reported_anchored"
    assert cc["other"]["c_grounded"] == 0.0, "the sentinel is not a free pass anywhere else"


def test_the_model_grounder_is_never_asked_to_find_an_absence(store):
    asked = []

    def grounder(rid, name, value, quote):
        asked.append(name)
        return [True, True]

    _assemble(store, [("x", 1.2), ("x_spread_type", NOT_REPORTED)], grounder=grounder)
    assert asked == ["name", "x"]


def test_the_judge_is_asked_to_confirm_an_absence_as_one():
    rec = {"fields": [{"field_name": "x", "value": 1.2},
                      {"field_name": "x_n_group", "value": NOT_REPORTED},
                      {"field_name": "y", "value": None}]}
    assert claim_text(rec) == "x = 1.2; x_n_group: the source does not report this"


# --- the driver cannot run with the rule switched off --------------------------------------

def test_plan_writes_the_groups_and_preflight_refuses_a_config_without_them(tmp_path):
    from lit2db import project
    run_wave = pytest.importorskip("run_wave")
    p = project.paths(tmp_path)
    p["spec"].parent.mkdir(parents=True, exist_ok=True)
    p["spec"].write_text(json.dumps(_spec(companion_fields("x", "U1")).model_dump(mode="json")),
                         encoding="utf-8")
    (p["sources"] / "PMC1").mkdir(parents=True)
    (p["sources"] / "PMC1" / "full.txt").write_text(QUOTE, encoding="utf-8")
    (p["sources"] / "PMC1" / "sections.json").write_text("[]", encoding="utf-8")
    p["search"].parent.mkdir(parents=True, exist_ok=True)
    p["search"].write_text(json.dumps({"papers": [{"outcome": "full_text",
                                                   "source_id": "PMC1"}]}), encoding="utf-8")
    out = project.plan(tmp_path, entity_type="t", identity_field="x", models=["a"] * 3,
                       judge_model="j", hunter_model="h", judge_audit_fraction=0.2)
    cfg = json.loads(pathlib.Path(out["config"]).read_text())
    assert cfg["uncertainty_groups"] == GROUPS
    run_wave._spec_context_for.cache_clear()
    assert not any("uncertainty" in x for x in run_wave.preflight(cfg, cfg["papers"]))
    cfg.pop("uncertainty_groups")
    assert any("declares uncertainty groups" in x for x in run_wave.preflight(cfg, cfg["papers"]))


def test_plan_writes_the_locked_fields_and_the_gate_holds_a_row_without_one(tmp_path):
    """`FieldSpec.required` reached no run until 2026-10-13: locked, reported, never enforced."""
    from lit2db import project
    run_wave = pytest.importorskip("run_wave")
    fields = companion_fields("x", "U1")
    spec = _spec(fields).model_dump(mode="json")
    for f in spec["fields"]:
        if f["name"] == "x":
            f["required"] = True
    p = project.paths(tmp_path)
    p["spec"].parent.mkdir(parents=True, exist_ok=True)
    p["spec"].write_text(json.dumps(spec), encoding="utf-8")
    (p["sources"] / "PMC1").mkdir(parents=True)
    (p["sources"] / "PMC1" / "full.txt").write_text(QUOTE, encoding="utf-8")
    (p["sources"] / "PMC1" / "sections.json").write_text("[]", encoding="utf-8")
    p["search"].parent.mkdir(parents=True, exist_ok=True)
    p["search"].write_text(json.dumps({"papers": [{"outcome": "full_text",
                                                   "source_id": "PMC1"}]}), encoding="utf-8")
    out = project.plan(tmp_path, entity_type="t", identity_field="x", models=["a"] * 3,
                       judge_model="j", hunter_model="h", judge_audit_fraction=0.2)
    cfg = json.loads(pathlib.Path(out["config"]).read_text())
    assert cfg["required_fields"] == ["x"]
    run_wave._spec_context_for.cache_clear()
    assert not any("locks" in x for x in run_wave.preflight(cfg, cfg["papers"]))
    cfg.pop("required_fields")
    assert any("locks ['x']" in x for x in run_wave.preflight(cfg, cfg["papers"]))

    db = str(tmp_path / "g.db")
    rec = _full(x=1.2, name="treated")
    rec["fields"] = [f for f in rec["fields"] if f["field_name"] != "x"]
    from lit2db.output import upsert
    g = upsert(rec, 1.0, db, required_fields=["x"])
    assert not g["written"] and any("locked field 'x' is absent" in r for r in g["reasons"])
