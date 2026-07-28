"""A ratified review-lane field is HELD, never written — and it must not veto the row.

Measured 2026-07-27 on the wave-1 calibration slice: a record scored 0.385 because the record
composite is the minimum over its fields and `function` — free prose, ratified into the human
review lane — scored 0.385. Its other nine fields scored 0.923-1.000 with three independent
readings unanimous on eight of them. A field the researcher had already ruled could never
auto-accept was vetoing every row it appeared in, so the pilot's auto-accept rate would have
been zero **by construction rather than by evidence**, and the run would have read as a broken
schema.

The two halves below must be tested together, because either alone is a defect:

  * exempting the field from the gate WITHOUT stripping it writes unverified prose into the
    database under a passing gate — strictly worse than the veto it replaces;
  * stripping it without the exemption changes nothing, because the field still blocks.
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("srv", ROOT / "mcp" / "lit2db_mcp" / "server.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

PROV = {
    "kind": "literature", "source_id": "PMC1", "retrieval_timestamp": "2026-07-27T00:00:00Z",
    "producing_process": "test", "process_fingerprint": "a" * 64,
    "verbatim_quote": "the enzyme converts FPP into pentalenene", "char_offset": 0,
    "section": "Results", "source_status": "active",
    "source_chars_total": 100, "source_chars_read": 100,
}


def field(name, value, grounded, ensemble):
    return {"field_name": name, "value": value, "provenance": dict(PROV),
            "confidence_components": {"c_grounded": grounded, "c_ensemble": ensemble},
            "contradiction_search": "clean"}


def record():
    """The shape actually measured: strong verifiable fields, one unverifiable prose field.

    `judge_verdict` is supported so these tests isolate the REVIEW LANE. The judge is a veto
    since D-079, so without it every write below would deny for an unrelated reason.
    """
    return {"record_id": "ts1", "entity_type": "terpene_synthase",
            "judge_verdict": "supported", "fields": [
        field("enzyme_name", "pentalenene synthase", 1.0, 1.0),
        field("accession", "WP_1", 1.0, 1.0),
        field("substrate", "FPP", 1.0, 1.0),
        field("function", "Catalyzes the ionization and cyclization of FPP", 0.0, 0.333),
    ]}


def score(rec, lane=()):
    return S.score_and_route(record=rec, weights_key="numeric", ensemble_k=3,
                             ensemble_min_agreeing=0, review_lane=list(lane))


def test_the_measured_veto_without_a_review_lane():
    """Baseline: the prose field IS the record's score."""
    out = score(record())
    assert out["_composite_confidence"] < 0.5


def test_the_review_lane_removes_the_veto():
    out = score(record(), lane=["function"])
    assert out["_composite_confidence"] == 1.0
    assert out["_review_lane"] == ["function"]


def test_the_held_field_is_not_written(tmp_path):
    """The half that makes the exemption safe. Unverified prose must not reach the database."""
    db = str(tmp_path / "t.db")
    out = score(record(), lane=["function"])
    g = S.gate_upsert(record=out, composite_confidence=out["_composite_confidence"],
                      db_path=db, autoaccept=0.95, review_lane=["function"])
    assert g["written"] is True
    assert g["held_for_review"] == ["function"]

    # Read the stored payload itself — `db_query` returns the summary view, and a test that
    # only checks the summary would pass while the prose sat in the row.
    import sqlite3
    with sqlite3.connect(db) as con:
        stored = con.execute("SELECT * FROM records").fetchone()
    blob = json.dumps(stored)
    assert "pentalenene synthase" in blob, "the verified facts must be recorded"
    assert "ionization and cyclization" not in blob, "held prose must NOT be in the database"
    assert "function" not in blob, "the held field must not appear at all"


def test_a_record_that_is_entirely_review_lane_is_denied(tmp_path):
    """Stripping everything would otherwise write an empty row that looks like a finding."""
    rec = {"record_id": "ts2", "entity_type": "terpene_synthase",
           "judge_verdict": "supported",
           "fields": [field("function", "does something", 0.0, 0.333)]}
    out = score(rec, lane=["function"])
    g = S.gate_upsert(record=out, composite_confidence=1.0, db_path=str(tmp_path / "t.db"),
                      autoaccept=0.95, review_lane=["function"])
    assert g["written"] is False
    assert any("review lane" in r for r in g["reasons"])


def test_the_lane_does_not_excuse_any_other_blocking_condition(tmp_path):
    """The exemption is scoped to the named fields ONLY. A retracted source, a contradiction,
    or an unsearched field elsewhere must still deny the write."""
    rec = record()
    rec["fields"][1]["provenance"]["source_status"] = "retracted"
    out = score(rec, lane=["function"])
    g = S.gate_upsert(record=out, composite_confidence=out["_composite_confidence"],
                      db_path=str(tmp_path / "t.db"), autoaccept=0.95,
                      review_lane=["function"])
    assert g["written"] is False
    assert any("retracted" in r for r in g["reasons"])


def test_a_contradiction_on_a_held_field_does_not_silently_vanish(tmp_path):
    """A held field is not written, so it cannot be blocked on — but the contradiction must
    still be visible on the record handed to the reviewer, not dropped by the strip."""
    rec = record()
    rec["fields"][3]["contradictions"] = [
        {"verbatim_quote": "no activity was detected", "char_offset": 5,
         "kind": "negated", "explanation": "the source denies the described activity"}]
    out = score(rec, lane=["function"])
    held = [f for f in out["fields"] if f["field_name"] == "function"]
    assert held and held[0]["contradictions"], "the reviewer must still see the counter-evidence"


@pytest.mark.parametrize("lane", [(), None, []])
def test_no_review_lane_is_the_default_and_changes_nothing(lane):
    """Every existing caller passes no lane; behaviour must be identical to before."""
    out = S.score_and_route(record=record(), weights_key="numeric", ensemble_k=3,
                            ensemble_min_agreeing=0, review_lane=list(lane or []))
    assert out["_composite_confidence"] < 0.5
