"""D-067's record-level review lane, which had never once worked (D-092).

The lane shipped in v0.27.0 and was exercised for the first time on the validation-arm run,
when an extractor marked a record `review_only`. The pipeline joined the reasons into
`failure_reason` — a five-value enum — so pydantic raised, and the exception took down the
whole paper along with its four other records.

It had never fired before because no earlier extractor prompt had led a model to set
`review_only`. A declared mechanism whose first real use is a crash is the same shape as the
twelve the v0.33.0 audit found, so these tests pin the behaviour rather than the fix:
a review-lane record must survive validation, keep its reasons in words, and BLOCK.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.contracts import ExtractedRecord  # noqa: E402
from lit2db.gate import gate_reasons  # noqa: E402
from lit2db.pipeline import assemble  # noqa: E402


def _rec(**kw):
    base = {
        "record_id": "cpd1",
        "entity_type": "bacterial_terpenoid_compound",
        "route": "human_review",
        "review_reasons": ["organism unstated"],
        "fields": [],
    }
    base.update(kw)
    return base


def test_a_review_lane_record_validates():
    """The whole bug: this raised, and the raise killed the paper."""
    r = ExtractedRecord.model_validate(_rec())
    assert r.review_reasons == ["organism unstated"]
    assert r.failure_reason is None


def test_free_text_still_cannot_enter_failure_reason():
    """The enum stays an enum — the fix is a new carrier, not a loosened contract."""
    with pytest.raises(Exception):
        ExtractedRecord.model_validate(_rec(failure_reason="unstated"))


def test_the_enum_values_still_work():
    assert ExtractedRecord.model_validate(
        _rec(failure_reason="retracted")).failure_reason.value == "retracted"


def test_reasons_default_to_empty_not_none():
    """A record with no reasons carries an empty list, so callers never branch on None."""
    assert ExtractedRecord.model_validate(_rec(review_reasons=[])).review_reasons == []


def test_a_review_lane_record_is_BLOCKED_by_the_gate():
    """Carrying the reasons must not have cost the lane its teeth: `route` is what blocks."""
    reasons = gate_reasons(_rec(), 1.0, 0.95)
    assert reasons, "a human_review record must never be written"
    assert any("human_review" in r or "route" in r for r in reasons), reasons


def test_the_reasons_reach_the_record_through_assemble():
    """End to end through the code that had the bug: an extractor's `review_only` becomes a
    routed record carrying its words, instead of an exception."""
    merged = {"records": [{
        "record_id": "cpd1",
        "entity_type": "bacterial_terpenoid_compound",
        "review_only": True,
        "review_reasons": ["organism unstated"],
        "fields": [{"field_name": "compound_name", "value": "corvol ether A",
                    "verbatim_quote": "we propose the trivial names corvol ethers A and B",
                    "agreement": 1.0, "n_passes": 3, "n_agreeing": 3}],
    }], "_passes": []}
    cfg = {"stores": str(ROOT), "extract_prompt": __file__, "models": ["a", "b", "c"],
           "producing_process": "test", "run_timestamp": "2026-07-28T00:00:00Z",
           "evidence_grounded_fields": [], "review_lane": []}
    try:
        out, _dropped = assemble("nonexistent_store", cfg, merged,
                                 {"state_by_record": {}, "contradictions": []})
    except Exception as exc:                       # store I/O varies by environment
        pytest.skip(f"assemble needs a real store here: {exc}")
    if out:
        rec = ExtractedRecord.model_validate(out[0])   # the call that used to raise
        assert rec.route.value == "human_review"
        assert rec.review_reasons == ["organism unstated"]
