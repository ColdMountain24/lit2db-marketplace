"""Tests for counter-evidence as a BLOCKING condition (Stage 4c').

The property under test is the one that motivated the feature: a contradiction must not be
survivable by piling up confidence. Every confidence signal scores the span the extractor
chose; a contradiction says that choice was unrepresentative. If a weighted mean can bury
it, the check is decorative.
"""
import json, os, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "mcp"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.contracts import ExtractedRecord, RouteDecision, default_route
from lit2db.contracts.routing import ContradictionEvidence, ContradictionKind, ContradictionSearch
from lit2db.gate import gate_reasons

PROV = {"kind": "literature", "source_id": "S1", "retrieval_timestamp": "2026-07-19T00:00:00Z",
        "producing_process": "p@1", "source_status": "active",
        "verbatim_quote": "Km was 4.2 uM.", "char_offset": 10}

CONTRA = {"verbatim_quote": "All assays were performed at pH 5.0 unless otherwise stated.",
          "char_offset": 880, "kind": "scope_mismatch",
          "explanation": "schema requires pH 7.4; this value was measured at pH 5.0"}


def _field(**over):
    fv = {"field_name": "km_value", "value": 4.2, "provenance": PROV, "route": "auto_accept",
          "confidence": 0.99,
          "confidence_components": {"c_grounded": 1.0, "c_judge": 1.0, "c_ensemble": 1.0,
                                    "c_verbal": 1.0, "c_consistency": 1.0}}
    fv.update(over)
    return fv


def _record(**over):
    return {"record_id": "r1", "entity_type": "e", "fields": [_field(**over)]}


# --- the load-bearing property -----------------------------------------------------
def test_contradiction_blocks_at_maximum_confidence():
    """A perfect composite must NOT redeem a contradicted value."""
    reasons = gate_reasons(_record(contradictions=[CONTRA]), 1.0)
    assert any("contradicted by its own source" in r for r in reasons)


def test_contradiction_reason_names_the_kind():
    reasons = gate_reasons(_record(contradictions=[CONTRA]), 1.0)
    assert any("scope_mismatch" in r for r in reasons)


def test_clean_search_does_not_block():
    assert gate_reasons(_record(contradiction_search="clean", contradictions=[]), 1.0) == []


def test_multiple_contradictions_are_counted():
    reasons = gate_reasons(_record(contradictions=[CONTRA, {**CONTRA, "kind": "superseded"}]), 1.0)
    assert any("2x" in r for r in reasons)


# --- "not searched" is not "clean" -------------------------------------------------
def test_unsearched_passes_by_default():
    """Off by default so existing pipelines keep working."""
    assert gate_reasons(_record(), 1.0) == []


def test_unsearched_blocks_when_search_is_required():
    reasons = gate_reasons(_record(), 1.0, require_contradiction_search=True)
    assert any("not searched is not clean" in r for r in reasons)


def test_required_search_accepts_a_clean_result():
    rec = _record(contradiction_search="clean")
    assert gate_reasons(rec, 1.0, require_contradiction_search=True) == []


def test_required_search_still_blocks_a_finding():
    """Turning the requirement on must not turn a finding into a pass."""
    rec = _record(contradiction_search="found", contradictions=[CONTRA])
    reasons = gate_reasons(rec, 1.0, require_contradiction_search=True)
    assert any("contradicted by its own source" in r for r in reasons)


# --- routing ------------------------------------------------------------------------
def test_contradiction_routes_to_human_review_over_a_perfect_score():
    fv = ExtractedRecord.model_validate(_record(contradictions=[CONTRA])).fields[0]
    assert default_route(fv) == RouteDecision.human_review


def test_without_contradiction_the_same_field_auto_accepts():
    fv = ExtractedRecord.model_validate(_record()).fields[0]
    assert default_route(fv) == RouteDecision.auto_accept


# --- contract shape -----------------------------------------------------------------
def test_contradiction_requires_an_anchored_span():
    """No verbatim quote + offset means a human cannot re-check it — reject the shape."""
    with pytest.raises(Exception):
        ContradictionEvidence(kind="other", explanation="feels wrong")


def test_search_state_defaults_to_not_run():
    fv = ExtractedRecord.model_validate(_record()).fields[0]
    assert fv.contradiction_search is ContradictionSearch.not_run
    assert fv.contradictions == []


def test_every_kind_is_a_real_enum_member():
    for k in ("conflicting_value", "scope_mismatch", "superseded", "negated",
              "unmet_condition", "other"):
        ContradictionKind(k)


def test_contradiction_survives_a_model_roundtrip():
    """It must ride through score_and_route's serialization to reach the gate."""
    rec = ExtractedRecord.model_validate(_record(contradictions=[CONTRA]))
    assert gate_reasons(json.loads(rec.model_dump_json()), 1.0)
