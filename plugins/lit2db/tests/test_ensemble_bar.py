"""The ensemble agreement bar is a RATIFIED SETTING, not a scaffold constant.

`c_ensemble` is an agreement fraction over k independent extraction passes, so the only
achievable values are j/k. Two consequences drive every test here:

  1. A float threshold is the wrong shape for the knob. At k=3 anything in (0.667, 1.0] is
     identical to demanding unanimity, so an operator who "lowers the bar to 0.95" has
     changed nothing. The setting is therefore an integer pair, and the fraction derived.
  2. j/k is float-inexact, so the comparison needs an epsilon. The previous hardcoded 0.999
     was that epsilon in disguise — it meant "== 1.0" and nothing else.

How tolerant to be of extraction disagreement is the researcher's call, not the scaffold's:
a dissenting pass may have read a different table row, taken the mutant instead of the wild
type, or hallucinated, and the fraction cannot distinguish those.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.contracts import ExtractedRecord, RouteDecision, default_route, required_agreement
from lit2db.contracts.routing import DEFAULT_ENSEMBLE_K, DEFAULT_MIN_AGREEING

PROV = {"kind": "literature", "source_id": "S1", "retrieval_timestamp": "2026-07-19T00:00:00Z",
        "producing_process": "p@1", "source_status": "active",
        "verbatim_quote": "kcat was 12.4 s-1", "char_offset": 10}


def _fv(ensemble):
    """A field perfect on every other signal, so the ensemble term is isolated."""
    cc = {"c_grounded": 1.0, "c_judge": 1.0, "c_verbal": 1.0, "c_consistency": 1.0}
    if ensemble is not None:
        cc["c_ensemble"] = ensemble
    rec = ExtractedRecord.model_validate(
        {"record_id": "r1", "entity_type": "x",
         "fields": [{"field_name": "kcat", "value": 12.4, "provenance": PROV,
                     "confidence_components": cc}]})
    return rec.fields[0]


# --- the default is unanimity ----------------------------------------------------------

def test_default_is_unanimity():
    assert DEFAULT_MIN_AGREEING is None          # None == unanimous, and tracks k
    assert DEFAULT_ENSEMBLE_K == 3
    assert required_agreement() == 1.0


def test_unanimous_auto_accepts_under_the_default():
    assert default_route(_fv(1.0)) is RouteDecision.auto_accept


def test_majority_does_not_auto_accept_under_the_default():
    assert default_route(_fv(2 / 3)) is RouteDecision.cheap_repair


def test_absent_ensemble_is_human_review_not_silently_accepted():
    """Blocker-3 behaviour, kept deliberately: 'we did not run an ensemble' must never read
    as agreement. Fail closed — an unmeasured signal is not a passing one."""
    assert default_route(_fv(None)) is RouteDecision.human_review


# --- the bar is settable, in the units the signal can actually take ---------------------

def test_lowering_the_bar_to_a_reachable_value_admits_the_majority():
    bar = required_agreement(k=3, min_agreeing=2)
    assert default_route(_fv(2 / 3), bar) is RouteDecision.auto_accept


def test_a_bar_between_achievable_values_is_the_same_as_unanimity():
    """The trap the integer pair exists to prevent: at k=3 there is nothing between 2/3 and
    1.0, so 'lowering' the float bar to 0.95 changes nothing at all."""
    assert default_route(_fv(2 / 3), 0.95) is RouteDecision.cheap_repair
    assert default_route(_fv(1.0), 0.95) is RouteDecision.auto_accept


@pytest.mark.parametrize("k,j", [(2, 1), (3, 2), (4, 3), (5, 4), (7, 5), (10, 7)])
def test_exact_fractions_clear_their_own_bar_despite_float_error(k, j):
    """j/k is not exactly representable; a bare >= comparison silently denies values that
    exactly meet the ratified bar. This is what the removed 0.999 was papering over."""
    assert default_route(_fv(j / k), required_agreement(k, j)) is RouteDecision.auto_accept


def test_one_pass_short_of_the_bar_is_refused():
    bar = required_agreement(k=5, min_agreeing=4)
    assert default_route(_fv(3 / 5), bar) is RouteDecision.cheap_repair
    assert default_route(_fv(4 / 5), bar) is RouteDecision.auto_accept


# --- the setting cannot be incoherent ---------------------------------------------------

@pytest.mark.parametrize("k,j", [(3, 4), (3, 0), (0, 0), (3, -1), (-2, 1)])
def test_impossible_agreement_settings_are_rejected(k, j):
    """'4 of 3 passes must agree' is unsatisfiable and would silently deny everything."""
    with pytest.raises(ValueError):
        required_agreement(k, j)


# --- the two footguns that come with making k toggleable --------------------------------

@pytest.mark.parametrize("k", [1, 0, -1])
def test_k_below_two_is_refused(k):
    """A single pass trivially agrees with itself, so k=1 would yield c_ensemble=1.0 and turn
    the agreement gate from a BLOCK into a PASS — asserting agreement nobody measured. That
    is strictly worse than having no ensemble, because the absent-signal path fails closed.
    Running without an ensemble must go through c_ensemble=None, not through k=1."""
    with pytest.raises(ValueError) as exc:
        required_agreement(k)
    assert "ensemble_k must be >=" in str(exc.value)


def test_running_without_an_ensemble_fails_closed_rather_than_open():
    """The supported way to skip the ensemble, contrasted with the k=1 trap above."""
    assert default_route(_fv(None)) is RouteDecision.human_review


def test_unanimity_tracks_k_instead_of_pinning_an_integer():
    """Footgun 2: an operator raising k for MORE rigour must not land on LESS. With the
    policy stored as the literal 3, bumping k from 3 to 5 would silently mean 3-of-5 — a
    bare majority. `None` keeps the meaning fixed while the number moves."""
    assert required_agreement(3) == 1.0
    assert required_agreement(5) == 1.0
    assert required_agreement(10) == 1.0
    # and the value that would have quietly passed under a pinned 3-of-5 is still refused
    assert default_route(_fv(3 / 5), required_agreement(5)) is RouteDecision.cheap_repair


def test_pinning_a_majority_is_still_possible_when_it_is_deliberate():
    """Tracking unanimity by default must not make a ratified majority unexpressible."""
    assert default_route(_fv(3 / 5), required_agreement(5, 3)) is RouteDecision.auto_accept


def test_the_bar_never_admits_a_lone_dissenting_pass_by_accident():
    """A minimum of 1-of-k is legal to express but means 'no agreement required'; it must at
    least still be reachable rather than misrouting, so the operator sees what they asked for."""
    assert default_route(_fv(1 / 3), required_agreement(3, 1)) is RouteDecision.auto_accept
