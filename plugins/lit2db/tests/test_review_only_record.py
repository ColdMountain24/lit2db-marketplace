"""A record the ratified criteria say can never auto-accept is ROUTED, not silently denied.

D-067. The gate mechanism already existed — `record["route"]` blocks in `gate_reasons` — so
what was missing was carrying the flag through the ensemble and declaring the rule. Recorded
here because the decision originally claimed a new mechanism was needed, and it was not.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lit2db.ensemble import merge_passes      # noqa: E402
from lit2db.gate import gate_reasons          # noqa: E402

ID = {"e": {"chain": ["accession"], "ordinal_within": []}}


def _rec(rid, flagged, reason=None):
    r = {"record_id": rid, "entity_type": "e",
         "fields": [{"field_name": "accession", "value": "X1"},
                    {"field_name": "name", "value": "pinene synthase"}]}
    if flagged:
        r["review_only"] = True
        if reason:
            r["review_reason"] = reason
    return r


def test_one_pass_flagging_is_enough():
    """ANY vote carries, deliberately. Field VALUES need agreement because the ensemble decides
    what is true; this flag decides only whether a human looks, and the two errors are not
    symmetric. Unanimity would let a record through because two passes missed what the third
    caught — the failure the ensemble exists to prevent. A false flag costs one glance."""
    m = merge_passes([[_rec("a", True, "plant gene in cyanobacterial host")],
                      [_rec("b", False)], [_rec("c", False)]], identity_fields=ID)
    r = m["records"][0]
    assert r["review_only"] is True
    assert r["review_flagged_by_passes"] == 1
    assert r["review_reasons"] == ["plant gene in cyanobacterial host"]


def test_unflagged_records_carry_no_review_keys():
    m = merge_passes([[_rec("a", False)]] * 3, identity_fields=ID)
    r = m["records"][0]
    assert "review_only" not in r and "review_reasons" not in r


def test_every_flagging_pass_contributes_its_reason():
    m = merge_passes([[_rec("a", True, "plant gene")],
                      [_rec("b", True, "origin unstated")],
                      [_rec("c", False)]], identity_fields=ID)
    r = m["records"][0]
    assert r["review_flagged_by_passes"] == 2
    assert r["review_reasons"] == ["origin unstated", "plant gene"]


def test_a_flagged_reason_is_never_invented():
    m = merge_passes([[_rec("a", True)]] * 1, identity_fields=ID)
    assert m["records"][0]["review_reasons"] == ["unstated"]


# --- the gate half ----------------------------------------------------------------------
def _clean_field(name):
    return {"field_name": name, "value": "v", "provenance": {"source_status": "active"},
            "contradiction_search": "clean"}


def _gateable(**over):
    """A record whose ONLY interesting property is the one under test.

    `judge_verdict` is supported throughout: since D-079 the adversarial judge is a veto, so a
    fixture without a verdict denies for a reason none of these tests is about — and every
    assertion below would still pass while measuring the wrong condition.
    """
    rec = {"judge_verdict": "supported", "fields": [_clean_field("accession")]}
    rec.update(over)
    return rec


def test_a_routed_record_is_denied_even_at_a_perfect_score():
    rec = _gateable(route="human_review")
    reasons = gate_reasons(rec, 1.0, autoaccept=0.95, require_contradiction_search=True)
    assert reasons == ["record routed human_review"]


def test_the_reason_travels_so_a_reviewer_knows_why():
    rec = _gateable(route="human_review", failure_reason="plant gene in cyanobacterial host")
    reasons = gate_reasons(rec, 1.0, autoaccept=0.95)
    assert "plant gene in cyanobacterial host" in reasons[0]


def test_an_identical_unrouted_record_still_writes():
    """The routing must be what denies it — not some incidental property of the record."""
    assert gate_reasons(_gateable(), 1.0, autoaccept=0.95,
                        require_contradiction_search=True) == []


def test_review_routing_does_not_excuse_anything_else():
    """A retracted source still denies a flagged record, and stays visible as its own reason."""
    f = _clean_field("accession")
    f["provenance"] = {"source_status": "retracted"}
    reasons = gate_reasons(_gateable(route="human_review", fields=[f]), 1.0, autoaccept=0.95)
    assert any("retracted" in r for r in reasons)
    assert len(reasons) == 2
