"""Tests for the deterministic verify/route/gate spine (the MCP tools).

These encode the project thesis as assertions:
  - naive grounding passes on a condition-multiplexed value, but a judge-ambiguous verdict
    routes it to human_review and the gate DENIES it;
  - a clean single-condition value is written;
  - a retracted source is denied at the gate even when everything else passes.
"""
import json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "mcp"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))
from lit2db_mcp import server as S

def _fn(t): return getattr(t, "fn", t)
validate, ground, mapv = _fn(S.validate_record), _fn(S.ground_literature), _fn(S.validate_mapping)
score_route, gate, query = _fn(S.score_and_route), _fn(S.gate_upsert), _fn(S.db_query)
RECS = json.load(open(ROOT / "examples" / "demo_records.json"))


def _prep(rec):
    for fv in rec["fields"]:
        g = ground(fv["value"], fv["provenance"]["verbatim_quote"])
        fv["confidence_components"]["c_grounded"] = g["c_grounded"]
    return rec


def test_naive_grounding_passes_on_multiplexed_value():
    # B's number DOES appear in its quote -> naive grounding is fooled
    b = json.loads(json.dumps(RECS["B_condition_multiplexed"]))
    g = ground(b["fields"][0]["value"], b["fields"][0]["provenance"]["verbatim_quote"])
    assert g["c_grounded"] == 1.0


def test_clean_value_is_written():
    a = _prep(json.loads(json.dumps(RECS["A_clean_regression_value"])))
    scored = score_route(a); comp = scored["_composite_confidence"]
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    res = gate(scored, comp, db_path=db)
    assert res["written"] is True
    assert query(db_path=db)["n"] == 1


def test_judge_ambiguous_denied_to_human_review():
    b = _prep(json.loads(json.dumps(RECS["B_condition_multiplexed"])))
    scored = score_route(b); comp = scored["_composite_confidence"]
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    res = gate(scored, comp, db_path=db)
    assert res["written"] is False
    assert query(db_path=db)["n"] == 0


def test_retracted_source_denied_at_gate():
    c = _prep(json.loads(json.dumps(RECS["C_retracted_source"])))
    scored = score_route(c); comp = scored["_composite_confidence"]
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    res = gate(scored, comp, db_path=db)
    assert res["written"] is False
    assert any("source_status" in r for r in res["reasons"])


def test_mapping_validation_flags_out_of_range_but_reports():
    r = mapv(0.003, {"type": "float", "valid_range": [0.01, 1e7]})
    assert r["ok"] is False and r["c_grounded"] == 0.0 and r["flags"]
