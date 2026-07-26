"""Tests for the HARD write-gate — both enforcement points, on the REAL payload shapes.

The MCP server exposes `gate_upsert`, which reaches a hook namespaced as
`mcp__<server>__gate_upsert`. These tests drive the hook with that payload rather than a
synthetic stand-in, so a hook that silently allows everything fails here instead of passing.

The load-bearing assertion is `test_hook_and_tool_agree`: two enforcement points, one
predicate, never a disagreement.
"""
import json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "mcp"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.gate import gate_reasons, is_write_tool, resolve_composite, resolve_threshold
from lit2db_mcp import server as S

HOOK = ROOT / "hooks" / "pretooluse_write_gate.py"
RECS = json.load(open(ROOT / "examples" / "demo_records.json"))

def _fn(t): return getattr(t, "fn", t)
ground, score_route, gate = _fn(S.ground_literature), _fn(S.score_and_route), _fn(S.gate_upsert)


def _scored(key):
    """Run a demo record through ground -> score/route, exactly as the pipeline does."""
    rec = json.loads(json.dumps(RECS[key]))
    for fv in rec["fields"]:
        fv["confidence_components"]["c_grounded"] = ground(
            fv["value"], fv["provenance"]["verbatim_quote"])["c_grounded"]
    scored = score_route(rec)
    return scored, scored["_composite_confidence"]


def _payload(record, composite, tool="mcp__lit2db__gate_upsert", **overrides):
    """The real Claude Code PreToolUse event for a `gate_upsert` call."""
    tool_input = {"record": record, "composite_confidence": composite,
                  "db_path": "", "autoaccept": -1.0}
    tool_input.update(overrides)
    return {"session_id": "test", "hook_event_name": "PreToolUse",
            "tool_name": tool, "tool_input": tool_input}


def _decide(payload, env=None, raw=None):
    """Run the hook as Claude Code does. Returns the decision, or None if it stayed silent
    (silence = not our tool = leave the normal permission flow alone)."""
    stdin = raw if raw is not None else json.dumps(payload)
    out = subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True,
                         text=True, env={**os.environ, **(env or {})})
    if not out.stdout.strip():
        return None
    return json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"]


# --- the bug that made the hook dead: it never matched the tool the server exposes -----
def test_hook_fires_on_the_namespaced_mcp_tool_name():
    assert is_write_tool("mcp__lit2db__gate_upsert")
    assert is_write_tool("gate_upsert") and is_write_tool("db_upsert")
    assert not is_write_tool("Read") and not is_write_tool("db_query")


def test_hook_denies_judge_ambiguous_record():
    rec, comp = _scored("B_condition_multiplexed")
    assert _decide(_payload(rec, comp)) == "deny"


def test_hook_allows_clean_record():
    rec, comp = _scored("A_clean_regression_value")
    assert _decide(_payload(rec, comp)) == "allow"


def test_hook_denies_retracted_source():
    rec, comp = _scored("C_retracted_source")
    assert _decide(_payload(rec, comp)) == "deny"


def test_hook_and_tool_agree():
    """Defense in depth only works if both points decide identically."""
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    for key in RECS:
        rec, comp = _scored(key)
        hook_says = _decide(_payload(rec, comp))
        tool_says = gate(rec, comp, db_path=db)["decision"]
        assert hook_says == tool_says, f"{key}: hook={hook_says} tool={tool_says}"


# --- shapes the old hook mis-read ------------------------------------------------------
def test_hook_gates_the_db_upsert_stub_shape():
    """`db_upsert` takes only a record — the composite comes off `_composite_confidence`."""
    clean, _ = _scored("A_clean_regression_value")
    ambiguous, _ = _scored("B_condition_multiplexed")
    ev = {"tool_name": "db_upsert", "tool_input": {"record": clean}}
    assert _decide(ev) == "allow"
    ev["tool_input"]["record"] = ambiguous
    assert _decide(ev) == "deny"


def test_hook_fails_closed_when_no_composite_is_resolvable():
    """`ExtractedRecord` has no record-level `confidence`; reading one is how the old hook
    let every write through. An unresolvable composite must deny."""
    rec, _ = _scored("A_clean_regression_value")
    rec.pop("_composite_confidence")
    for fv in rec["fields"]:
        fv["confidence"] = None
    assert resolve_composite({"record": rec}) is None
    assert _decide({"tool_name": "gate_upsert", "tool_input": {"record": rec}}) == "deny"


def test_hook_ignores_unrelated_tools():
    rec, comp = _scored("A_clean_regression_value")
    assert _decide(_payload(rec, comp, tool="Read")) is None
    assert _decide(_payload(rec, comp, tool="mcp__lit2db__db_query")) is None


def test_hook_fails_closed_on_unparseable_payload():
    assert _decide(None, raw="not json at all") == "deny"
    assert _decide({"tool_name": "gate_upsert", "tool_input": "oops"}) == "deny"


# --- threshold precedence: call arg > env > default ------------------------------------
def test_threshold_precedence():
    rec, comp = _scored("A_clean_regression_value")          # composite ~0.974
    assert _decide(_payload(rec, comp), env={"LIT2DB_AUTOACCEPT": "0.99"}) == "deny"
    assert _decide(_payload(rec, comp, autoaccept=0.99)) == "deny"
    # the call's own argument wins over the environment
    assert _decide(_payload(rec, comp, autoaccept=0.5),
                   env={"LIT2DB_AUTOACCEPT": "0.99"}) == "allow"
    assert resolve_threshold({}, {}) == 0.95                 # conservative placeholder


# --- the predicate itself ---------------------------------------------------------------
def test_gate_denies_a_record_with_no_fields():
    assert gate_reasons({"record_id": "x", "fields": []}, 1.0) == ["record carries no fields"]


def test_gate_denies_a_field_without_provenance():
    rec = {"record_id": "x", "fields": [{"field_name": "y", "value": 1, "route": "auto_accept"}]}
    assert any("provenance" in r for r in gate_reasons(rec, 1.0))
