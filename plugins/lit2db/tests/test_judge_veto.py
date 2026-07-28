"""The adversarial judge is a VETO, not a term in the confidence mean (D-079).

WHY THIS FILE EXISTS. `c_judge` sat at weight 0.15 inside a weighted mean beside `c_grounded`
at 0.35, which described the judge as one contributing signal among six. Measured, it never
behaved like one:

    grounding   agreement   judge          composite (old)
    1.0         3/3         SUPPORTED      1.0000
    1.0         3/3         (unjudged)     1.0000     <- the judge changed nothing
    1.0         3/3         PARTIAL        0.8846
    1.0         3/3         UNSUPPORTED    0.7692
    1.0         2/3         SUPPORTED      0.9231     <- below any bar that 1.0000 clears

Against a 0.95 bar only a unanimous, fully-grounded record can ever be written, and for such a
record the verdict could only LOWER the number. That is a veto. 139 of 165 judge calls in the
measured runs could not have changed any outcome, and every one of them was paid for before
anything knew which records mattered.

So the mechanism was renamed to what it was and moved to where the other disqualifying facts
live. The claim that had to be proved is that this changes what a run COSTS and what it SAYS,
never what lands in the database — `test_the_written_set_is_unchanged` is that proof, computed
against a frozen replica of the old arithmetic rather than against a remembered number.
"""
from __future__ import annotations

import itertools
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.contracts.provenance import ConfidenceComponents            # noqa: E402
from lit2db.contracts.routing import (DEFAULT_WEIGHTS, ExtractedRecord,  # noqa: E402
                                      JudgeVerdict, RouteDecision,
                                      achievable_composites, default_route)
from lit2db.gate import (gate_reasons, judge_veto_reasons,               # noqa: E402
                         selection_reasons)

run_wave = pytest.importorskip("run_wave")

BAR = 0.95
PROV = {"kind": "literature", "source_id": "S1", "retrieval_timestamp": "2026-07-19T00:00:00Z",
        "producing_process": "p@1", "source_status": "active",
        "verbatim_quote": "Km was 4.2 uM.", "char_offset": 10}


def _record(grounded, ensemble, verdict="supported", **over):
    rec = {"record_id": "r1", "entity_type": "e", "judge_verdict": verdict,
           "fields": [{"field_name": "km_value", "value": 4.2, "provenance": dict(PROV),
                       "route": "auto_accept", "contradiction_search": "clean",
                       "confidence_components": {"c_grounded": grounded,
                                                 "c_ensemble": ensemble}}]}
    rec.update(over)
    return rec


# =========================================================================================
# THE PROOF: identical outcomes, computed both ways
# =========================================================================================
# The pre-D-079 arithmetic, frozen here rather than referenced, so this test keeps meaning
# after the real implementation has moved on. Weights are the shipped `numeric` profile as it
# stood at v0.31.0; routing is `default_route` as it stood, including its `judge_pass` term.
_OLD_WEIGHTS = {"c_grounded": 0.35, "c_verbal": 0.20, "c_ensemble": 0.15,
                "c_judge": 0.15, "c_consistency": 0.10, "c_logprob": 0.05}
_OLD_VERDICT_TO_C = {"supported": 1.0, "partial": 0.5, "unsupported": 0.0}


def _old_outcome(grounded, ensemble, verdict):
    """Would v0.31.0 have written this record? Composite (weighted mean including c_judge),
    then route (auto_accept required judge_pass), then the gate's blocking routes."""
    present = {"c_grounded": grounded, "c_ensemble": ensemble}
    if verdict in _OLD_VERDICT_TO_C:                       # absent verdict == no c_judge signal
        present["c_judge"] = _OLD_VERDICT_TO_C[verdict]
    num = sum(_OLD_WEIGHTS[k] * v for k, v in present.items())
    den = sum(_OLD_WEIGHTS[k] for k in present)
    composite = num / den
    judge_pass = present.get("c_judge", 0.0) >= 0.5
    if judge_pass and ensemble >= 1.0 - 1e-9 and grounded >= 0.9:
        route = "auto_accept"
    elif (0.6 <= grounded < 0.9) or (0.0 < ensemble < 1.0 - 1e-9):
        route = "cheap_repair"
    else:
        route = "human_review"
    return composite >= BAR and route not in ("human_review", "quarantine")


def _new_outcome(grounded, ensemble, verdict):
    """Does the shipped spine write it? Real composite, real routing, real gate."""
    cc = ConfidenceComponents(c_grounded=grounded, c_ensemble=ensemble)
    composite = cc.composite(DEFAULT_WEIGHTS["numeric"])
    rec = _record(grounded, ensemble, verdict)
    rec["fields"][0]["route"] = default_route(
        ExtractedRecord.model_validate(rec).fields[0]).value
    return not gate_reasons(rec, composite, BAR, require_contradiction_search=True)


GRID = list(itertools.product(
    (0.0, 0.6, 0.9, 1.0),                                  # grounding rungs
    (0.0, 1 / 3, 2 / 3, 1.0),                              # agreement at k=3
    ("supported", "partial", "unsupported", "not_run", "unparseable")))


@pytest.mark.parametrize("grounded,ensemble,verdict", GRID)
def test_the_written_set_is_unchanged(grounded, ensemble, verdict):
    """80 combinations of the full signal space; every one must decide the same way it did.

    This is the whole justification for the change. Taking a term out of a mean is a licence to
    loosen a gate by accident, and the two places it could have happened are both live here:
    an UNSUPPORTED record used to be stopped by routing (no judge_pass) and is now stopped by
    the veto; a PARTIAL record used to be stopped by scoring 0.885 against a 0.95 bar and is
    now stopped by the veto. Tolerating either at the veto would have quietly written records
    the old spine denied, while the change was being described as behaviour-preserving.
    """
    assert _new_outcome(grounded, ensemble, verdict) == _old_outcome(grounded, ensemble, verdict)


def test_the_grid_actually_contains_writes_and_denials():
    """A guard on the guard: an equivalence that compares 'nothing' to 'nothing' proves nothing."""
    outcomes = [_new_outcome(*c) for c in GRID]
    assert any(outcomes) and not all(outcomes)
    assert sum(outcomes) == 1, "exactly one combination writes: perfect grounding, unanimous, supported"


# =========================================================================================
# c_judge is out of the mean, and cannot be put back by accident
# =========================================================================================
def test_no_shipped_profile_weights_the_judge():
    for key, weights in DEFAULT_WEIGHTS.items():
        assert "c_judge" not in weights, f"{key} still scores the judge"


def test_weighting_the_judge_is_refused_not_ignored():
    """A project overrides `confidence_weights` from its instantiation. Silently dropping the
    key would let a project believe it had re-enabled a signal that does nothing; scoring it
    would restore the defect. Refusing is the only honest third option."""
    cc = ConfidenceComponents(c_grounded=1.0, c_ensemble=1.0, c_judge=0.0)
    with pytest.raises(ValueError, match="not a scored signal"):
        cc.composite({"c_grounded": 0.35, "c_judge": 0.15})


def test_a_recorded_c_judge_does_not_move_the_score():
    """Artifacts written before v0.32.0 carry `c_judge`. They must still validate, and the
    value must be inert — visible to an auditor, invisible to the arithmetic."""
    weights = DEFAULT_WEIGHTS["numeric"]
    without = ConfidenceComponents(c_grounded=1.0, c_ensemble=1.0).composite(weights)
    for stale in (0.0, 0.5, 1.0):
        with_stale = ConfidenceComponents(c_grounded=1.0, c_ensemble=1.0,
                                          c_judge=stale).composite(weights)
        assert with_stale == without == 1.0


def test_routing_no_longer_reads_the_verdict():
    """Selection is the two mechanical signals. A record with no verdict at all must route
    `auto_accept` — and then be stopped by the gate, which is where the stopping now lives."""
    fv = ExtractedRecord.model_validate(_record(1.0, 1.0, verdict="not_run")).fields[0]
    assert default_route(fv) == RouteDecision.auto_accept
    assert judge_veto_reasons(_record(1.0, 1.0, verdict="not_run"))


# =========================================================================================
# The veto itself
# =========================================================================================
@pytest.mark.parametrize("verdict", ["partial", "unsupported", "not_run", "unparseable"])
def test_only_supported_clears(verdict):
    assert judge_veto_reasons(_record(1.0, 1.0, verdict))
    assert not judge_veto_reasons(_record(1.0, 1.0, "supported"))


def test_struck_out_and_never_challenged_are_different_sentences():
    """v0.31.0's line, applied to the judge: a verdict that stands is auditable, a stage that
    did not happen is a run to retry. A reviewer must be able to tell which they are holding."""
    struck = judge_veto_reasons(_record(1.0, 1.0, "unsupported"))[0]
    absent = judge_veto_reasons(_record(1.0, 1.0, "not_run"))[0]
    assert "struck out" in struck and "not judged is not supported" in absent
    assert struck != absent


def test_the_judges_note_travels_to_whoever_holds_the_denial():
    rec = _record(1.0, 1.0, "partial")
    rec["judge_note"] = "GC-MS only; identity is not confirmed"
    assert "GC-MS only" in judge_veto_reasons(rec)[0]


def test_an_unreadable_verdict_is_never_read_as_approval():
    """Fails closed on anything the gate does not recognize — a typo, a wire-format word that
    leaked through, a future verdict this version predates."""
    for bogus in ("SUPPORTED?", "yes", "approved", "", None, 1.0, {"verdict": "supported"}):
        assert judge_veto_reasons(_record(1.0, 1.0, bogus))


def test_the_verdict_survives_a_pydantic_roundtrip():
    """It has to reach the gate through `score_and_route`'s serialization to do anything."""
    rec = ExtractedRecord.model_validate(_record(1.0, 1.0, "unsupported"))
    assert rec.judge_verdict is JudgeVerdict.unsupported
    assert judge_veto_reasons(json.loads(rec.model_dump_json()))


def test_the_default_is_not_run_so_silence_blocks():
    rec = ExtractedRecord.model_validate({"record_id": "r", "entity_type": "e", "fields": []})
    assert rec.judge_verdict is JudgeVerdict.not_run


# --- the split: selection is separable, the write path is not ----------------------------
def test_selection_ignores_the_veto_and_the_gate_applies_it():
    rec, comp = _record(1.0, 1.0, "unsupported"), 1.0
    assert selection_reasons(rec, comp, BAR, require_contradiction_search=True) == []
    assert gate_reasons(rec, comp, BAR, require_contradiction_search=True)


def test_gate_reasons_is_exactly_selection_plus_veto():
    """One predicate, composed — never two predicates that could drift. The failure this
    project keeps finding in its own work is a check routed around, not a check computed wrong.
    """
    for verdict in ("supported", "partial", "not_run"):
        for grounded, ensemble in ((1.0, 1.0), (0.6, 1 / 3), (0.0, 0.0)):
            rec = _record(grounded, ensemble, verdict)
            comp = ConfidenceComponents(c_grounded=grounded,
                                        c_ensemble=ensemble).composite(DEFAULT_WEIGHTS["numeric"])
            assert (gate_reasons(rec, comp, BAR) ==
                    selection_reasons(rec, comp, BAR) + judge_veto_reasons(rec))


def test_the_hook_and_the_tool_both_veto():
    """Defense in depth is only defense if BOTH points enforce the new condition. The hook is
    a separate process reading the raw payload, so it can only inherit this by really calling
    the same predicate."""
    import subprocess
    hook = ROOT / "hooks" / "pretooluse_write_gate.py"
    for verdict, expected in (("supported", "allow"), ("unsupported", "deny"),
                              ("not_run", "deny")):
        rec = _record(1.0, 1.0, verdict)
        payload = {"tool_name": "mcp__lit2db__gate_upsert",
                   "tool_input": {"record": rec, "composite_confidence": 1.0,
                                  "db_path": "", "autoaccept": -1.0}}
        out = subprocess.run([sys.executable, str(hook)], input=json.dumps(payload),
                             capture_output=True, text=True)
        got = json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert got == expected, f"hook said {got} for judge_verdict={verdict}"


# =========================================================================================
# The lattice this coarsens — a known consequence, pinned
# =========================================================================================
def test_the_lattice_is_tenths_and_only_one_rung_clears_the_bar():
    """Removing a signal made the score COARSER: steps of 1/13 became steps of 1/10. Accepted,
    but it must not drift further without somebody noticing — a profile whose top rung stopped
    clearing 0.95 would auto-accept nothing at all, by construction rather than by evidence."""
    rungs = achievable_composites(DEFAULT_WEIGHTS["numeric"], k=3)
    assert rungs[-1] == 1.0
    assert [r for r in rungs if r >= BAR] == [1.0]
    # Every rung is a multiple of 1/10 — that is the "steps of 1/10" claim, stated as the
    # property it actually is. The rungs themselves are [0, .1, .2, .3, .7, .8, .9, 1.0]: the
    # 0.4 gap in the middle is the grounding jump from 0 to 1, not a finer step hiding.
    assert all(abs(r * 10 - round(r * 10)) < 1e-9 for r in rungs), sorted(rungs)
    assert len(rungs) == 8


def test_a_partial_grounding_score_lands_between_the_rungs():
    """The honest caveat on the claim above, asserted so nobody reads the lattice as a promise.

    `ground_literature` returns a fraction for a partial lexical match — real runs produce
    composites like 0.150 and 0.850 — so the lattice is a FLOOR on coarseness, not a statement
    that every score sits on it. Quoting '1/10' as though it bounded the score everywhere would
    be the same over-reading the project has already made about this number twice.
    """
    fine = achievable_composites(DEFAULT_WEIGHTS["numeric"], k=3, grounding=(0.0, 0.55, 1.0))
    assert any(r not in achievable_composites(DEFAULT_WEIGHTS["numeric"], k=3) for r in fine)
    assert ConfidenceComponents(c_grounded=0.95, c_ensemble=1.0).composite(
        DEFAULT_WEIGHTS["numeric"]) >= BAR, "an imperfectly grounded value CAN clear the bar"


def test_a_two_of_three_record_cannot_reach_the_bar():
    """The fact that made the judge a veto in the first place, still true and still the reason
    the accept bar is effectively 'unanimous and fully grounded'."""
    cc = ConfidenceComponents(c_grounded=1.0, c_ensemble=2 / 3)
    assert cc.composite(DEFAULT_WEIGHTS["numeric"]) < BAR


# =========================================================================================
# Who gets judged — the saving, and the audit slice that keeps it honest
# =========================================================================================
def _scored(rid, grounded=1.0, ensemble=1.0, **over):
    rec = _record(grounded, ensemble, "not_run", record_id=rid, **over)
    comp = ConfidenceComponents(c_grounded=grounded,
                                c_ensemble=ensemble).composite(DEFAULT_WEIGHTS["numeric"])
    return {"record": rec, "composite": comp}


CFG = {"auto_accept_threshold": BAR, "review_lane": [], "judge_audit_fraction": 0.2}


def test_only_records_that_survive_selection_are_judged():
    """The saving, stated as a property rather than a percentage."""
    scored = [_scored("keep")] + [_scored(f"thin{i}", grounded=0.6, ensemble=1 / 3)
                                  for i in range(10)]
    pick = run_wave.select_for_judging(scored, CFG, salt="w|P1")
    assert pick["selected"] == ["keep"]
    assert len(pick["to_judge"]) == 1 + 2, "the survivor, plus 20% of 10 rejected"
    assert len(pick["to_judge"]) < len(scored)


def test_the_audit_slice_is_never_silently_empty():
    """ceil, not round: a wave that audited nothing would report a saving it had not earned."""
    scored = [_scored(f"thin{i}", grounded=0.0, ensemble=0.0) for i in range(3)]
    pick = run_wave.select_for_judging(scored, {**CFG, "judge_audit_fraction": 0.01}, salt="s")
    assert len(pick["audit"]) == 1


def test_the_audit_slice_is_the_same_sample_after_a_resume():
    """A resumed leg must re-draw the SAME rows. A reject-side rate measured over a set nobody
    can reconstruct is not evidence, and a fresh random draw per leg would produce exactly that.
    """
    scored = [_scored(f"thin{i}", grounded=0.0, ensemble=0.0) for i in range(40)]
    first = run_wave.select_for_judging(scored, CFG, salt="wave1|PMC1")["audit"]
    again = run_wave.select_for_judging(list(reversed(scored)), CFG, salt="wave1|PMC1")["audit"]
    assert first == again, "the sample must not depend on record order either"
    other = run_wave.select_for_judging(scored, CFG, salt="wave1|PMC2")["audit"]
    assert other != first, "but a different paper must get its own sample"


def test_the_audit_frame_excludes_what_no_verdict_could_overturn():
    """D-081: thin evidence and contradicted rows are auditable; a retracted source, a ratified
    review-only record, and a record whose counter-evidence search never ran are not. Judging
    those spends an adversarial read on an outcome the judge has no power over."""
    retracted = _scored("retracted", grounded=0.0)
    retracted["record"]["fields"][0]["provenance"]["source_status"] = "retracted"
    policy = _scored("policy", grounded=0.0, route="human_review")
    unsearched = _scored("unsearched", grounded=0.0)
    unsearched["record"]["fields"][0]["contradiction_search"] = "not_run"
    contradicted = _scored("contradicted", grounded=0.0)
    contradicted["record"]["fields"][0]["contradictions"] = [
        {"verbatim_quote": "no activity was detected", "char_offset": 5,
         "kind": "negated", "explanation": "the source denies it"}]
    thin = _scored("thin", grounded=0.0)

    pick = run_wave.select_for_judging(
        [retracted, policy, unsearched, contradicted, thin],
        {**CFG, "judge_audit_fraction": 1.0}, salt="s")

    assert pick["selected"] == []
    assert sorted(pick["auditable"]) == ["contradicted", "thin"]
    assert pick["denial_class"]["retracted"] == "status"
    assert pick["denial_class"]["policy"] == "policy"
    assert pick["denial_class"]["unsearched"] == "process"


def test_a_colliding_record_id_is_counted_once_and_reported():
    """FOUND ON THE FIRST LIVE RUN, not by this suite.

    `merge_passes` returned 15 records under 11 ids on PMC10325987 — the fallback1 and ordinal
    identity tiers colliding. Sampling over the raw list drew the same id twice, `to_judge`
    deduplicated it, and the paper REPORTED a 3-record audit slice having judged 2. A run that
    overstates its own sample size is the exact defect class this pipeline exists to catch, and
    it was in the reporting the pipeline uses to describe itself.

    Absorbing it silently would have been worse than the overcount: the output database keys on
    `record_id`, so a collision is a live data-loss hazard and must come back as a finding.
    """
    scored = [_scored("dup", grounded=0.0), _scored("dup", grounded=0.0),
              _scored("other", grounded=0.0)]
    pick = run_wave.select_for_judging(scored, {**CFG, "judge_audit_fraction": 1.0}, salt="s")

    assert pick["duplicate_record_ids"] == ["dup"]
    assert sorted(pick["auditable"]) == ["dup", "other"], "each id considered exactly once"
    assert len(pick["audit"]) == len(set(pick["audit"])) == 2
    assert len(pick["to_judge"]) == len(pick["audit"]), (
        "the reported sample size must equal the number of records actually judged")


def test_the_collision_reaches_the_researcher_as_a_question():
    qs = run_wave.catalogue_questions("PMC1", {"alignment": [], "ensemble": {}}, [], [],
                                      duplicate_record_ids=["ts10"])
    q = [x for x in qs if x["kind"] == "colliding_record_id"]
    assert q and "silently overwrite each other" in q[0]["detail"]


def test_judging_everything_is_still_expressible():
    """A project that wants the old behaviour sets the fraction to 1.0 — the knob is a dial,
    not a switch, and the pre-D-079 setting must remain reachable."""
    scored = [_scored("keep")] + [_scored(f"thin{i}", grounded=0.0) for i in range(5)]
    pick = run_wave.select_for_judging(scored, {**CFG, "judge_audit_fraction": 1.0}, salt="s")
    assert len(pick["to_judge"]) == len(scored)


# --- verdicts land on the right records ---------------------------------------------------
def test_verdicts_are_stamped_and_the_unjudged_stay_apart():
    """Three states, three meanings: answered, asked-but-unreadable, never asked."""
    scored = [_scored("a"), _scored("b"), _scored("c")]
    run_wave.apply_verdicts(
        scored, {"a": {"verdict": "SUPPORTED"},
                 "b": {"verdict": "PARTIAL", "weakest_supported_claim": "a product formed"}},
        judged={"a", "b", "c"})
    assert scored[0]["record"]["judge_verdict"] == "supported"
    assert scored[1]["record"]["judge_verdict"] == "partial"
    assert scored[1]["record"]["judge_note"] == "a product formed"
    assert scored[2]["record"]["judge_verdict"] == "unparseable", "asked, no readable answer"


def test_a_record_never_sent_to_the_judge_is_not_run_not_unparseable():
    scored = [_scored("never")]
    run_wave.apply_verdicts(scored, {}, judged=set())
    assert scored[0]["record"]["judge_verdict"] == "not_run"


def test_every_stamped_verdict_is_a_value_the_gate_recognizes():
    """The driver's wire vocabulary and the contract's enum must not drift apart: a verdict the
    gate cannot read blocks, so a mismatch would look like a working veto and silently deny
    everything the judge supported."""
    for wire, state in run_wave.VERDICT_TO_STATE.items():
        assert JudgeVerdict(state)
        assert not judge_veto_reasons(_record(1.0, 1.0, state)) or state != "supported"
    assert set(run_wave.VERDICT_TO_STATE) == {"SUPPORTED", "PARTIAL", "UNSUPPORTED"}
