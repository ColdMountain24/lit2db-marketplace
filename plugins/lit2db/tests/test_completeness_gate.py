"""The completeness condition — a sparse record is held out of the ML-ready table.

Measured across two arms against a collaborator's own database (237 records, one arm a
pre-registered held-out slice): precision rises with the number of populated fields, and keeps
rising INSIDE the adversarial judge's supported set — 86% at five fields against 62% at four.
It out-predicts both signals that are in the confidence formula.

Two things these tests exist to pin, in order of importance:

  1. **The default changes nothing.** `min_populated_fields=0` must leave every existing
     project's written set byte-identical. A mechanism that arrives switched on rewrites
     history for every database already built with this plugin.
  2. **Both enforcement points apply it.** `gate_upsert` and the PreToolUse hook must reach the
     same number from the same rule. A hook laxer than the tool is how a gate becomes advisory.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "mcp"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

import pytest  # noqa: E402

from lit2db.gate import (DEFAULT_MIN_POPULATED_FIELDS, gate_reasons,  # noqa: E402
                         populated_fields, resolve_min_populated, selection_reasons)

HOOK = ROOT / "hooks" / "pretooluse_write_gate.py"


def _field(name, value):
    return {"field_name": name, "value": value,
            "provenance": {"source_id": "PMC1", "retrieval_timestamp": "2026-01-01T00:00:00Z",
                           "producing_process": "test", "process_fingerprint": "f" * 64,
                           "source_status": "active", "kind": "literature",
                           "verbatim_quote": "q", "char_offset": 0},
            "route": "auto_accept"}


def _record(**values):
    return {"record_id": "r1", "entity_type": "t", "judge_verdict": "supported",
            "fields": [_field(k, v) for k, v in values.items()]}


def _hook(record, min_populated=None, env=None, review_lane=None):
    tool_input = {"record": record, "composite_confidence": 1.0, "db_path": "",
                  "autoaccept": -1.0}
    if min_populated is not None:
        tool_input["min_populated_fields"] = min_populated
    if review_lane is not None:
        tool_input["review_lane"] = review_lane
    payload = {"session_id": "t", "hook_event_name": "PreToolUse",
               "tool_name": "mcp__lit2db__gate_upsert", "tool_input": tool_input}
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                         capture_output=True, text=True, env={**os.environ, **(env or {})})
    return json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"]


# --- 1. the default must be inert ------------------------------------------------------
def test_the_default_is_off():
    assert DEFAULT_MIN_POPULATED_FIELDS == 0


@pytest.mark.parametrize("n_fields", [1, 2, 3, 4, 5])
def test_at_the_default_no_record_is_denied_for_completeness(n_fields):
    """The condition must be invisible until a project calibrates it. Any denial here would
    mean shipping the mechanism silently re-gated every database already built."""
    rec = _record(**{f"f{i}": f"v{i}" for i in range(n_fields)})
    assert gate_reasons(rec, 1.0, 0.5) == []


# --- 2. presence is not population -----------------------------------------------------
def test_an_empty_value_does_not_count_as_populated():
    """An extractor told to fill a schema emits `""` — counting that would inflate exactly the
    signal the condition rests on."""
    rec = _record(a="real", b="", c="   ", d=None, e=[], f={})
    assert populated_fields(rec) == ["a"]


def test_a_zero_or_false_value_DOES_count():
    """`0` and `False` are answers. Only emptiness is absence — a numeric field legitimately
    carries 0, and dropping it would penalise the most precise records there are."""
    rec = _record(a=0, b=False, c=0.0)
    assert populated_fields(rec) == ["a", "b", "c"]


def test_a_review_lane_field_does_not_count_toward_completeness():
    """Review-lane fields are HELD, not written (`output.upsert` strips them). Counting one
    would let a field that is not part of the row satisfy a condition about the row."""
    rec = _record(compound="x", function="free prose")
    assert populated_fields(rec, review_lane=["function"]) == ["compound"]
    assert selection_reasons(rec, 1.0, 0.5, review_lane=["function"],
                             min_populated_fields=2)
    assert selection_reasons(rec, 1.0, 0.5, review_lane=["function"],
                             min_populated_fields=1) == []


# --- 3. the condition itself -----------------------------------------------------------
def test_a_sparse_record_is_denied_with_a_reason_a_human_can_act_on():
    rec = _record(a="x", b="y")
    reasons = gate_reasons(rec, 1.0, 0.5, min_populated_fields=5)
    assert len(reasons) == 1
    assert "populates 2 field(s)" in reasons[0] and "a, b" in reasons[0]
    assert "candidate pool" in reasons[0]


def test_a_complete_record_passes():
    rec = _record(a="1", b="2", c="3", d="4", e="5")
    assert gate_reasons(rec, 1.0, 0.5, min_populated_fields=5) == []


def test_the_condition_is_ADDITIVE_and_never_rescues_a_record():
    """Completeness is a necessary condition, not a substitute for one. A fully-populated
    record the judge struck out must still be denied — otherwise a signal ADDED to raise
    precision would have loosened the gate."""
    rec = _record(a="1", b="2", c="3", d="4", e="5")
    rec["judge_verdict"] = "unsupported"
    reasons = gate_reasons(rec, 1.0, 0.5, min_populated_fields=5)
    assert any("adversarial judge" in r for r in reasons)


# --- 4. both enforcement points, one rule ----------------------------------------------
def test_the_hook_applies_the_condition_from_the_call():
    sparse = _record(a="x", b="y")
    assert _hook(sparse) == "allow"                     # default off
    assert _hook(sparse, min_populated=5) == "deny"
    assert _hook(_record(a="1", b="2", c="3", d="4", e="5"), min_populated=5) == "allow"


def test_the_hook_applies_the_condition_from_the_environment():
    """The env var is how a calibrated project switches it on for every caller at once,
    including callers that predate the parameter."""
    assert _hook(_record(a="x", b="y"), env={"LIT2DB_MIN_POPULATED_FIELDS": "5"}) == "deny"


def test_hook_and_tool_reach_the_same_number():
    """The precedence rule is shared, so the two enforcement points cannot be configured apart:
    call arg > env > default, resolved by ONE function."""
    assert resolve_min_populated({"min_populated_fields": 3},
                                 {"LIT2DB_MIN_POPULATED_FIELDS": "5"}) == 3
    assert resolve_min_populated({}, {"LIT2DB_MIN_POPULATED_FIELDS": "5"}) == 5
    assert resolve_min_populated({}, {}) == 0
    assert resolve_min_populated({"min_populated_fields": -1}, {}) == 0   # -1 == unset


def test_the_hook_honours_the_review_lane_like_the_tool_does():
    """The hook used to pass NEITHER `review_lane` nor `required_fields` to the predicate it
    shares with `gate_upsert`. So a record with a ratified review-lane field was DENIED at the
    hook and ALLOWED by the tool — safe in direction, but a genuine disagreement between two
    points whose whole design is that they cannot disagree, and a false denial of exactly the
    class D-067 measured driving a pilot's auto-accept rate to zero by construction.

    Found while threading the completeness bound through, and it had to be fixed in the same
    change: with the lane unknown to the hook, the hook would COUNT a held field toward
    completeness while the tool did not — a disagreement in the permissive direction."""
    rec = _record(compound="x", function="free prose")
    rec["fields"][1]["route"] = "human_review"

    assert _hook(rec) == "deny"                                    # lane unknown -> blocks
    assert _hook(rec, review_lane=["function"]) == "allow"         # lane known -> held out
    assert gate_reasons(rec, 1.0, 0.5, review_lane=["function"]) == []   # the tool, unchanged

    # And the reason it could not be left alone: the held field must not pay for the row.
    assert _hook(rec, review_lane=["function"], min_populated=2) == "deny"


def test_the_hook_applies_ALL_the_predicate_conditions_not_a_subset():
    """D-101. Every condition `gate_upsert` passes, the hook passes — four of six was the bug.

    `required_fields` was the other omission. It denies in the SAFE direction, which is why it
    survived unnoticed alongside `review_lane`, which denied in the unsafe one. A predicate
    reached through two doors that pass different arguments is two predicates."""
    rec = _record(a="1", b="2")
    assert _hook(rec) == "allow"
    # locked field absent -> the hook must refuse it, as the tool always did
    tool_input = {"record": rec, "composite_confidence": 1.0, "db_path": "", "autoaccept": -1.0,
                  "required_fields": ["c"]}
    payload = {"session_id": "t", "hook_event_name": "PreToolUse",
               "tool_name": "mcp__lit2db__gate_upsert", "tool_input": tool_input}
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                         capture_output=True, text=True, env=dict(os.environ))
    decision = json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"]
    assert decision == "deny"
    assert gate_reasons(rec, 1.0, 0.5, required_fields=["c"])   # and the tool agrees


# --- 5. single-pass runs: completeness stands in for agreement (D-100 / V-001) ----------
def test_a_single_pass_run_without_a_completeness_bar_is_REFUSED():
    """The coupling, enforced rather than documented. Dropping to one reading removes one of the
    two signals the composite is built from; that is only safe because completeness takes its
    place. Configured without it, k=1 has NEITHER — and it would arrive looking like a cost
    saving rather than an ungated run."""
    from lit2db.gate import single_pass_problems
    problems = single_pass_problems(ensemble_k=0, min_populated_fields=0)
    assert len(problems) == 1
    assert "nothing is configured to take its place" in problems[0]


def test_a_single_pass_run_WITH_a_completeness_bar_is_allowed():
    from lit2db.gate import single_pass_problems
    assert single_pass_problems(ensemble_k=0, min_populated_fields=5) == []


def test_a_multi_pass_run_never_needs_the_completeness_bar():
    """k>=2 has agreement, so the substitution does not apply and must not be demanded."""
    from lit2db.gate import single_pass_problems
    assert single_pass_problems(ensemble_k=3, min_populated_fields=0) == []


def test_single_pass_routing_no_longer_sends_every_field_to_human_review():
    """Measured before this change: k=1 wrote ZERO records across 21 gate settings, because
    routing demanded an agreement signal that a single pass cannot produce. That is what made
    'drop to k=1' impossible rather than merely cheaper."""
    from lit2db.contracts import FieldValue, RouteDecision
    from lit2db.contracts.routing import default_route
    fv = FieldValue.model_validate(_field("a", "x") | {
        "confidence_components": {"c_grounded": 1.0, "c_ensemble": None}})
    assert default_route(fv, 1.0, require_ensemble=True) == RouteDecision.human_review
    assert default_route(fv, 1.0, require_ensemble=False) == RouteDecision.auto_accept


def test_single_pass_routing_still_requires_GROUNDING():
    """The substitution replaces agreement, not evidence. A value that is not in the text must
    still not auto-accept, or k=1 would be a free pass rather than a different configuration."""
    from lit2db.contracts import FieldValue, RouteDecision
    from lit2db.contracts.routing import default_route
    ungrounded = FieldValue.model_validate(_field("a", "x") | {
        "confidence_components": {"c_grounded": 0.0, "c_ensemble": None}})
    assert default_route(ungrounded, 1.0, require_ensemble=False) == RouteDecision.human_review


# --- 6. root cause vs cascade (2026-07-29) ---------------------------------------------
def test_one_grounding_failure_is_reported_as_ONE_cause_not_three():
    """Measured on 87 confirmed-correct compounds the gate threw away: 72% failed for a single
    reason wearing three hats. At k=1 the composite IS the grounding score, so one lexical miss
    fails the bar, routes the field to human_review, AND stops the record being selected for
    judging — which then vetoes it as `never challenged`."""
    from lit2db.gate import diagnose
    rec = _record(a="x", b="y")
    rec["fields"][0]["confidence_components"] = {"c_grounded": 0.0, "c_ensemble": None}
    rec["fields"][0]["route"] = "human_review"
    rec["judge_verdict"] = "not_run"
    reasons = gate_reasons(rec, 0.0, 0.95)
    d = diagnose(reasons, rec)
    assert len(reasons) >= 3
    assert "grounding returned 0.0 on a" in d["root"]
    assert d["derived"], "the score/route/judge reasons should be marked as consequences"
    assert not d["independent"]


def test_a_genuine_judge_veto_is_NOT_filed_as_a_consequence():
    """A record that grounded fine and was struck out by the judge has an independent finding,
    and it must never be presented as fallout from something else."""
    from lit2db.gate import diagnose
    rec = _record(a="x", b="y")
    for fv in rec["fields"]:
        fv["confidence_components"] = {"c_grounded": 1.0, "c_ensemble": 1.0}
    rec["judge_verdict"] = "unsupported"
    d = diagnose(gate_reasons(rec, 1.0, 0.5), rec)
    assert d["independent"] and "adversarial judge" in d["independent"][0]
    assert not d["derived"]


def test_diagnosis_changes_nothing_about_what_is_written():
    """The load-bearing assertion. `diagnose` is a READER over the same list — a gate that
    explained itself better by denying less would be the regression this module prevents."""
    from lit2db.gate import diagnose
    rec = _record(a="x", b="y")
    rec["fields"][0]["confidence_components"] = {"c_grounded": 0.0}
    before = gate_reasons(rec, 0.0, 0.95)
    diagnose(before, rec)
    assert gate_reasons(rec, 0.0, 0.95) == before
    assert before, "still denied"
