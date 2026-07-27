"""The adversarial judge must be auditable, and a missing verdict must be loud.

Measured in the v4 calibration slice (45 records, 3 papers):
  - 7 records got no parseable verdict, and the driver read that as absence rather than
    failure — a record silently skipping the adversarial check that is the point of the
    pipeline.
  - The judge's reasoning was never persisted at all, so those 7 were undiagnosable and no
    denial anywhere could be audited. In a system whose claim is auditability.
  - 31 of 75 catalogued questions were one free-prose review-lane field disagreeing with
    itself, burying the 12 scope questions that genuinely needed a human.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

run_wave = pytest.importorskip("run_wave")
_parse = run_wave._parse_verdicts


# --- the judge's reasoning survives ----------------------------------------------------
def test_single_claim_reply_is_parsed_structurally_not_by_regex():
    """A one-claim reply carries no record_id because it does not need one.

    Regressed once already: without the single-id attribution every unbatched judgement fell
    to the regex path and lost reasoning, weakest_supported_claim and overreach — the fields
    a human needs to audit a denial, and the only reason to persist the judge at all.
    """
    r = _parse(json.dumps({"verdict": "PARTIAL", "reasoning": "GC-MS only",
                           "weakest_supported_claim": "a product was formed",
                           "overreach": ["identity confirmed"]}), ["ts1"])
    assert r["ts1"]["verdict"] == "PARTIAL"
    assert r["ts1"]["reasoning"] == "GC-MS only"
    assert r["ts1"]["overreach"] == ["identity confirmed"]
    assert "by_regex" not in r["ts1"]


def test_fenced_batch_with_ids_binds_each_verdict_to_its_own_record():
    txt = ("```json\n"
           '[{"record_id":"ts1","verdict":"SUPPORTED","reasoning":"NMR"},\n'
           ' {"record_id":"ts2","verdict":"UNSUPPORTED","reasoning":"silent"}]\n```')
    r = _parse(txt, ["ts1", "ts2"])
    assert r["ts1"]["verdict"] == "SUPPORTED" and r["ts2"]["verdict"] == "UNSUPPORTED"
    assert r["ts2"]["reasoning"] == "silent"
    assert not any("by_position" in v for v in r.values())


def test_verdicts_wrapped_in_a_key_are_found():
    txt = json.dumps({"verdicts": [{"record_id": "ts1", "verdict": "SUPPORTED"},
                                   {"record_id": "ts2", "verdict": "PARTIAL"}]})
    assert set(_parse(txt, ["ts1", "ts2"])) == {"ts1", "ts2"}


# --- a guess is labelled as a guess, and a miss is a miss -------------------------------
def test_positional_pairing_is_marked_as_a_guess():
    """A batched reply with no ids can only be paired by order, and order is a GUESS.

    A mis-paired verdict is worse than a missing one: it attributes a judgement to a record
    nobody made it about, and nothing downstream can tell.
    """
    r = _parse('[{"verdict":"SUPPORTED"},{"verdict":"UNSUPPORTED"}]', ["ts1", "ts2"])
    assert r["ts1"]["by_position"] is True and r["ts2"]["by_position"] is True


def test_mismatched_count_refuses_rather_than_mis_pairs():
    assert _parse('[{"verdict":"SUPPORTED"}]', ["ts1", "ts2"]) == {}


def test_unparseable_reply_yields_no_verdict_never_a_default():
    """Absence must not become 'SUPPORTED' or any other value by default."""
    for txt in ("the judge was unsure", "", "verdict: probably fine", "{}"):
        assert _parse(txt, ["ts1"]) == {}


def test_unknown_verdict_word_is_rejected():
    assert _parse('{"record_id":"ts1","verdict":"MAYBE"}', ["ts1"]) == {}


def test_record_id_outside_the_batch_is_not_accepted():
    r = _parse('[{"record_id":"ghost","verdict":"SUPPORTED"}]', ["ts1", "ts2"])
    assert "ghost" not in r


# --- the question queue keeps its signal ------------------------------------------------
def _merged(fields_ambiguous):
    return {"alignment": [], "ensemble": {n: {"ambiguous_modal": True, "groups": []}
                                          for n in fields_ambiguous}}


def test_review_lane_field_does_not_flood_the_question_queue():
    """`function` is prose ratified as never-auto-acceptable: it disagrees on every record by
    construction. 31 of 75 questions were this one field."""
    qs = run_wave.catalogue_questions(
        "PMC1", _merged(["ts1:function", "ts1:product"]), [], [],
        review_lane=("function",))
    kinds = [q["detail"] for q in qs if q["kind"] == "no_consensus_value"]
    assert any("product" in d for d in kinds), "a real field must still be surfaced"
    assert not any("function" in d for d in kinds), "the review-lane field must not"


def test_without_a_review_lane_nothing_is_suppressed():
    qs = run_wave.catalogue_questions("PMC1", _merged(["ts1:function"]), [], [])
    assert len([q for q in qs if q["kind"] == "no_consensus_value"]) == 1


def test_a_record_the_judge_never_answered_becomes_a_question():
    qs = run_wave.catalogue_questions("PMC1", _merged([]), [], [], unjudged=["ts3", "ts7"])
    nv = [q for q in qs if q["kind"] == "no_verdict"]
    assert len(nv) == 2
    assert "ts3" in nv[0]["detail"] and "skipped its check" in nv[0]["detail"]


def test_scope_disagreement_and_weak_identity_still_surface():
    merged = {"alignment": [
        {"identity": "sp|geraniol synthase", "found_by_passes": 1, "identity_tier": "fallback1"},
        {"identity": "sp#ord0", "found_by_passes": 3, "identity_tier": "ordinal"}], "ensemble": {}}
    kinds = {q["kind"] for q in run_wave.catalogue_questions("PMC1", merged, [], [])}
    assert kinds == {"scope_disagreement", "weak_identity"}
