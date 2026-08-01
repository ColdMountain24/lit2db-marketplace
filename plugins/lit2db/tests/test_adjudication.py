"""The adjudication loop — a researcher's check becomes this project's calibration set.

The gold set sat on the ladder for weeks as a blocker because it was framed as work to
commission. A researcher confirming candidates is work they were going to do anyway; these tests
pin the two properties that make capturing it safe:

  1. **A human verdict cannot reach the ML-ready table.** Saying "this record is right" is a
     statement about whether the GATE was right. Letting it place the row would consume the
     measurement it exists to produce, and would make the gate advisory by a side door — the
     failure that put 18 rejected records into a shipped BBB database.
  2. **`cant_tell` is a verdict, not a skip.** 60% of this corpus's target compounds are named
     only in text nobody can obtain. Recording those as `wrong` would calibrate the accept bar
     against the reach of a library subscription.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "mcp"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db import calibration  # noqa: E402
from lit2db.gate import WRITE_TOOLS, is_write_tool  # noqa: E402
from lit2db.output import (ADJUDICATION_VERDICTS, adjudications,  # noqa: E402
                           record_adjudication, record_candidate, review_queue)


def _candidate(rid, source, comp, decision="deny", n_fields=5, verdict="supported"):
    rec = {"record_id": rid, "entity_type": "t", "judge_verdict": verdict,
           "fields": [{"field_name": f"f{i}", "value": f"v{i}",
                       "provenance": {"source_id": source, "source_status": "active"}}
                      for i in range(n_fields)]}
    return rec, {"decision": decision, "reasons": ["thin agreement"]}


def _seed(db, n=3):
    for i in range(n):
        rec, gate = _candidate(f"r{i}", "PMC1", 0.9)
        record_candidate(rec, 0.9, gate, db, source_id="PMC1")


# --- 1. it cannot reach the ML-ready table ---------------------------------------------
def test_recording_a_verdict_is_not_a_write_tool():
    """The PreToolUse hook must leave it alone — and it must not be able to reach `records`."""
    assert "record_adjudication" not in WRITE_TOOLS
    assert not is_write_tool("mcp__plugin_lit2db_lit2db__record_adjudication")


def test_confirming_a_record_does_not_put_it_in_the_ml_ready_table(tmp_path):
    db = str(tmp_path / "o.db")
    _seed(db)
    record_adjudication("r0", "PMC1", "right", db, note="I checked the paper myself")
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM adjudications").fetchone()[0] == 1
    finally:
        con.close()


# --- 2. the vocabulary ------------------------------------------------------------------
def test_the_three_verdicts_and_no_others(tmp_path):
    db = str(tmp_path / "o.db")
    _seed(db)
    assert ADJUDICATION_VERDICTS == ("right", "wrong", "cant_tell")
    for v in ADJUDICATION_VERDICTS:
        assert record_adjudication("r0", "PMC1", v, db)["recorded"] is True


def test_an_unrecognized_verdict_is_refused_not_stored(tmp_path):
    """Free text would make the calibration table silently incomplete — the same reason
    `failure_reason` is an enum rather than a string."""
    db = str(tmp_path / "o.db")
    _seed(db)
    out = record_adjudication("r0", "PMC1", "probably?", db)
    assert out["recorded"] is False and "not one of" in out["reason"]
    assert adjudications(db)["n"] == 0


def test_cant_tell_is_held_OUT_of_precision_and_counted_beside_it():
    """The whole reason for a third verdict. Folding these into `wrong` would drag every
    calibrated bar down by the fraction of the literature that is paywalled."""
    rows = ([{"verdict": "right"}] * 6 + [{"verdict": "wrong"}] * 2
            + [{"verdict": "cant_tell"}] * 12)
    p = calibration.precision(rows)
    assert p["n_verifiable"] == 8 and p["precision"] == 0.75
    assert p["n_unverifiable"] == 12          # visible, so 8-of-20 cannot read as 8-of-8


# --- 3. a second sitting does not re-ask ------------------------------------------------
def test_unadjudicated_only_hides_what_has_been_ruled_on(tmp_path):
    db = str(tmp_path / "o.db")
    _seed(db, n=3)
    assert review_queue(db)["n"] == 3
    record_adjudication("r0", "PMC1", "right", db)
    record_adjudication("r1", "PMC1", "cant_tell", db)
    assert review_queue(db)["n"] == 3                              # default unchanged
    left = review_queue(db, unadjudicated_only=True)
    assert left["n"] == 1 and left["queue"][0]["record_id"] == "r2"
    assert left["adjudicated_total"] == 2


def test_re_adjudicating_replaces_rather_than_duplicates(tmp_path):
    """A reviewer may change their mind. Two rows for one record with different answers and
    nothing saying which is current would be a calibration set that cannot be read."""
    db = str(tmp_path / "o.db")
    _seed(db)
    record_adjudication("r0", "PMC1", "wrong", db)
    record_adjudication("r0", "PMC1", "right", db, note="misread it the first time")
    got = adjudications(db)
    assert got["n"] == 1 and got["adjudications"][0]["verdict"] == "right"


# --- 4. the join that IS the calibration set --------------------------------------------
def test_adjudications_carry_the_signals_the_gate_saw(tmp_path):
    """A verdict with no signals attached cannot calibrate anything — the join is the point."""
    db = str(tmp_path / "o.db")
    rec, gate = _candidate("r9", "PMC2", 0.85, n_fields=4)
    record_candidate(rec, 0.85, gate, db, source_id="PMC2")
    record_adjudication("r9", "PMC2", "right", db)
    row = adjudications(db)["adjudications"][0]
    assert row["composite_confidence"] == 0.85
    assert row["judge_verdict"] == "supported"
    assert row["n_populated_fields"] == 4
    assert row["gate_decision"] == "deny"


def test_completeness_is_counted_by_the_SAME_code_the_gate_uses(tmp_path):
    """A second definition of "populated" would let the calibration table and the write
    predicate disagree about the exact signal being calibrated."""
    db = str(tmp_path / "o.db")
    rec, gate = _candidate("r1", "PMC3", 1.0, n_fields=3)
    rec["fields"].append({"field_name": "empty", "value": "   ",
                          "provenance": {"source_id": "PMC3", "source_status": "active"}})
    record_candidate(rec, 1.0, gate, db, source_id="PMC3")
    record_adjudication("r1", "PMC3", "right", db)
    assert adjudications(db)["adjudications"][0]["n_populated_fields"] == 3


# --- 5. the arithmetic ------------------------------------------------------------------
def test_small_n_is_never_hidden():
    """3-of-3 reading 100% is the shape of a finding this project has retracted three times."""
    t = calibration.table([{"verdict": "right", "k": 5}] * 3, key=lambda r: r["k"])
    assert t[0]["precision"] == 1.0 and t[0]["n_verifiable"] == 3
    lo, hi = t[0]["ci95"]
    assert lo < 0.5              # three observations buy almost nothing, and it says so
    assert "3" in calibration.render(t)


def test_no_observations_is_not_zero_precision():
    p = calibration.precision([{"verdict": "cant_tell"}] * 4)
    assert p["precision"] is None and p["ci95"] == [0.0, 1.0]


def test_the_frontier_enumerates_observed_values_not_a_grid():
    """The composite is quantized. A continuous grid invents settings that cannot differ, which
    is how a threshold gets discussed for months as a dial with ~26 positions."""
    rows = [{"verdict": "right", "c": 1.0}, {"verdict": "wrong", "c": 0.5},
            {"verdict": "right", "c": 1.0}]
    f = calibration.frontier(rows, lambda r: r["c"])
    assert [round(x["bar"], 2) for x in f] == [0.5, 1.0]
    assert f[-1]["precision"] == 1.0 and f[-1]["n_accepted"] == 2


def test_the_verdict_vocabulary_is_stated_once_per_module_and_they_agree():
    """`calibration` cannot import `output` without a cycle, so the words exist in two places.
    This is the test that stops them drifting."""
    assert (calibration.CORRECT, calibration.INCORRECT,
            calibration.UNVERIFIABLE) == ADJUDICATION_VERDICTS


# --- 6. the MCP surface -----------------------------------------------------------------
def test_the_mcp_tools_are_wired_and_report_honestly(tmp_path):
    from lit2db_mcp import server as S

    def fn(t):
        return getattr(t, "fn", t)

    db = str(tmp_path / "o.db")
    _seed(db, n=4)
    for rid, v in (("r0", "right"), ("r1", "right"), ("r2", "wrong"), ("r3", "cant_tell")):
        assert fn(S.record_adjudication)(rid, "PMC1", v, db_path=db)["recorded"] is True
    rep = fn(S.calibration_report)(db_path=db, bucket_by="completeness")
    assert rep["ok"] and rep["n_adjudicated"] == 4
    assert rep["overall"]["n_verifiable"] == 3          # cant_tell held out
    assert rep["overall"]["n_unverifiable"] == 1        # and counted
    assert json.dumps(rep)                              # MCP has to serialize it
    assert fn(S.calibration_report)(db_path=db, bucket_by="nonsense")["ok"] is False


# --- 4. the queue ORDER must not hide the class the reviewer is there to rule on ---------
def _grounding_zero_candidate(rid, source, field="compound_name"):
    """A record whose value is not in its quote: grounding 0.0, so at k=1 the composite is 0.0
    and the gate emits one failure wearing several hats."""
    rec = {"record_id": rid, "entity_type": "t", "judge_verdict": "not_run",
           "fields": [{"field_name": field, "value": "v", "route": "human_review",
                       "confidence_components": {"c_grounded": 0.0},
                       "provenance": {"source_id": source, "source_status": "active"}}]}
    gate = {"decision": "deny",
            "reasons": [f"composite 0.000 < auto-accept 0.95",
                        f"field '{field}' routed human_review",
                        "never challenged by the adversarial judge (not_run)"]}
    return rec, gate


def _clean_composite_candidate(rid, source):
    """A record denied on an INDEPENDENT finding, whose composite is a perfect 1.000 — the
    combination `best_first` sorts to the very front."""
    rec = {"record_id": rid, "entity_type": "t", "judge_verdict": "supported",
           "fields": [{"field_name": "f0", "value": "v", "route": "auto_accept",
                       "confidence_components": {"c_grounded": 1.0},
                       "provenance": {"source_id": source, "source_status": "active"}}]}
    return rec, {"decision": "deny", "reasons": ["record populates 1 field(s) (f0) < the "
                                                 "calibrated minimum 5"]}


def test_best_first_HIDES_the_grounding_class_when_the_composite_is_binary(tmp_path):
    """The measured pathology, in miniature (landed terpenoid wave, 2026-07-29).

    At k=1 the composite IS the grounding score, so it takes two values and nothing between.
    `best_first` then ranks by root cause by accident and in the wrong direction: every
    perfect-composite record sorts ahead of every grounding-0.0 one, so a limit that does not
    reach the whole pool shows the reviewer NONE of the largest, most diagnostic class.
    """
    db = str(tmp_path / "o.db")
    for i in range(6):
        rec, gate = _clean_composite_candidate(f"good{i}", "PMC1")
        record_candidate(rec, 1.0, gate, db, source_id="PMC1")
    for i in range(6):
        rec, gate = _grounding_zero_candidate(f"zero{i}", "PMC1")
        record_candidate(rec, 0.0, gate, db, source_id="PMC1")

    hidden = review_queue(db, limit=6)                       # the shipped default order
    assert {c["composite_confidence"] for c in hidden["queue"]} == {1.0}
    assert not any(c["record_id"].startswith("zero") for c in hidden["queue"]), \
        "best_first with a truncating limit must be shown to hide the grounding class"

    surfaced = review_queue(db, limit=6, order="by_root_cause")
    assert all(c["record_id"].startswith("zero") for c in surfaced["queue"])
    assert all(c["root_cause_class"] == "the value is not in the quote it cited"
               for c in surfaced["queue"])


def test_by_root_cause_collapses_one_failure_wearing_three_hats(tmp_path):
    """Three reasons, one question. The reviewer is asked whether the value is in the quote —
    not to re-derive which of `composite`, `routed`, and `not_run` is the real finding."""
    db = str(tmp_path / "o.db")
    rec, gate = _grounding_zero_candidate("z", "PMC1")
    record_candidate(rec, 0.0, gate, db, source_id="PMC1")
    row = review_queue(db, order="by_root_cause")["queue"][0]
    assert len(row["reasons"]) == 3
    assert row["reasons_subsumed_by_root"] == 3, "all three follow from the grounding miss"
    assert "does not appear in the quote" in row["root_cause"]


def test_the_limit_is_applied_AFTER_the_root_cause_sort(tmp_path):
    """The bug this order exists to fix, one layer down: truncating in SQL before the Python
    sort would reproduce it exactly while appearing to be ordered correctly."""
    db = str(tmp_path / "o.db")
    for i in range(8):
        rec, gate = _clean_composite_candidate(f"good{i}", "PMC1")
        record_candidate(rec, 1.0, gate, db, source_id="PMC1")
    rec, gate = _grounding_zero_candidate("zero0", "PMC1")
    record_candidate(rec, 0.0, gate, db, source_id="PMC1")
    q = review_queue(db, limit=1, order="by_root_cause")
    assert q["n"] == 1 and q["queue"][0]["record_id"] == "zero0"


def test_root_cause_is_absent_rather_than_wrong_on_the_default_path(tmp_path):
    """`root_cause` needs the payload, which the default order deliberately does not read.
    Reporting it anyway would make the SAME record describe itself differently depending on how
    the queue was sorted."""
    db = str(tmp_path / "o.db")
    rec, gate = _grounding_zero_candidate("z", "PMC1")
    record_candidate(rec, 0.0, gate, db, source_id="PMC1")
    assert "root_cause" not in review_queue(db)["queue"][0]
    assert "root_cause" in review_queue(db, order="by_root_cause")["queue"][0]
