"""Ensemble agreement: dissent must imply substance, not typography (D-035).

The bar the routing rule applies is only meaningful if disagreement between extraction
passes reflects a real difference in what was read. These tests pin that property from both
sides: formatting differences must NOT count as dissent, and substantive differences must.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.ensemble import (
    agreement, as_number, consistency, normalize, summarize, values_agree,
)


# --- formatting is not dissent ----------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    (4.2, "4.20"),                              # trailing zero
    ("4.2 uM", "4.2 µM"),                  # micro sign
    ("4.2 uM", "4.2 μM"),                  # greek mu
    ("12.4 s⁻¹", "12.4 s-1"),         # superscript via NFKC
    ("Geosmin", "geosmin"),                     # case
    ("  geosmin ", "geosmin"),                  # whitespace
    ("−4.2", "-4.2"),                      # unicode minus
    ("1,200", "1200"),                          # thousands separator
])
def test_formatting_differences_are_agreement(a, b):
    assert values_agree(a, b), f"{a!r} vs {b!r} should agree after normalization"


def test_numeric_tolerance_absorbs_rounding():
    assert values_agree(4.20, 4.21)             # within the default 1%
    assert not values_agree(4.2, 4.9)


# --- substantive differences are dissent -------------------------------------------------

@pytest.mark.parametrize("a,b", [
    (12.4, 14.2),                               # different measurement
    ("geosmin", "pentalenene"),                 # different compound
    (4.2, None),                                # one pass found nothing
    (12.4, 1240.0),                             # order-of-magnitude / unit slip
])
def test_substantive_differences_are_dissent(a, b):
    assert not values_agree(a, b)


def test_a_missing_value_is_its_own_outcome():
    """'The pass found nothing' must not collapse into 'the pass found this' — otherwise a
    field two passes could not locate would look unanimous."""
    assert values_agree(None, None)
    assert not values_agree(None, 4.2)
    r = agreement([12.4, 12.4, None])
    assert r["c_ensemble"] == pytest.approx(2 / 3)


def test_booleans_are_not_treated_as_numbers():
    """bool subclasses int; True must not compare numerically equal to 1.0."""
    assert as_number(True) is None
    assert not values_agree(True, 1.0)


@pytest.mark.parametrize("a,b", [
    ("2-MIB", "2-methylisoborneol"),            # same locant, different compound
    ("2-methylisoborneol", "2-methylbutanol"),
    ("NRRL 12345", "NRRL 67890"),               # strain identifiers
    ("4.2-4.8", "4.2-9.9"),                     # ranges, not scalars
])
def test_a_number_embedded_in_a_name_is_not_a_measurement(a, b):
    """The bug this pins: a permissive 'first number anywhere' rule read every 2-something
    compound as the value 2.0, so two unrelated compounds reported unanimous agreement."""
    assert as_number(a) is None
    assert not values_agree(a, b)


@pytest.mark.parametrize("v,expected", [
    ("4.2 uM", 4.2), ("4.2uM", 4.2), ("12.4 s-1", 12.4),
    ("-4.2", -4.2), ("1,200", 1200.0), ("1.2e3", 1200.0),
])
def test_real_measurements_still_parse(v, expected):
    """The strictness must not cost us actual measurements with units attached."""
    assert as_number(v) == pytest.approx(expected)


# --- the agreement fraction --------------------------------------------------------------

def test_unanimous():
    r = agreement([4.2, "4.20", "4.2"])
    assert r["c_ensemble"] == 1.0 and r["n_agreeing"] == 3
    assert not r["ambiguous_modal"] and r["modal_value"] is not None


def test_one_dissenter():
    r = agreement([12.4, 12.4, 14.2])
    assert r["c_ensemble"] == pytest.approx(2 / 3)
    assert as_number(r["modal_value"]) == 12.4


def test_total_disagreement():
    r = agreement([1.0, 2.0, 3.0])
    assert r["c_ensemble"] == pytest.approx(1 / 3)


def test_a_tie_reports_no_modal_value():
    """k=2 split, or 2-2 at k=4: the fraction is still right but there is no consensus value,
    and nothing downstream should be handed one."""
    for vals in ([12.4, 14.2], [12.4, 12.4, 14.2, 14.2]):
        r = agreement(vals)
        assert r["ambiguous_modal"] and r["modal_value"] is None


def test_empty_input_yields_no_signal_rather_than_a_default():
    r = agreement([])
    assert r["c_ensemble"] is None       # absent, not 0.0 and not 1.0


def test_grouping_is_not_transitive_chaining():
    """Numeric tolerance is not transitive. Single-link chaining would merge 100 and 102 via
    101 and report false unanimity; grouping against a representative must not."""
    r = agreement([100.0, 101.0, 102.0], rel_tol=0.015)
    assert r["c_ensemble"] < 1.0, "chained near-misses were merged into a false consensus"


def test_grouping_is_order_stable():
    a = agreement([12.4, 14.2, 12.4])["c_ensemble"]
    b = agreement([14.2, 12.4, 12.4])["c_ensemble"]
    assert a == b == pytest.approx(2 / 3)


# --- caller-supplied domain knowledge, never baked in -------------------------------------

def test_synonyms_come_from_the_caller():
    """The scaffold carries no domain content: two names denote one entity only because the
    ratified instantiation says so."""
    assert not values_agree("2-MIB", "2-methylisoborneol")
    assert values_agree("2-MIB", "2-methylisoborneol",
                        synonyms={"2-MIB": "2-methylisoborneol"})


def test_binomial_expansion_is_opt_in():
    vals = ["Streptomyces coelicolor", "S. coelicolor", "Streptomyces coelicolor"]
    assert agreement(vals)["c_ensemble"] == pytest.approx(2 / 3)
    assert agreement(vals, expand_binomials=True)["c_ensemble"] == 1.0


def test_binomial_expansion_cannot_invent_agreement():
    """It only ever expands an initial to a genus another pass actually proposed, so it can
    merge passes that already agree — never fabricate agreement between two organisms."""
    r = agreement(["Streptomyces coelicolor", "S. griseus"], expand_binomials=True)
    assert r["c_ensemble"] == 0.5


def test_unknown_normalizer_raises_rather_than_no_op():
    """A typo'd normalizer that silently did nothing would move the agreement bar invisibly."""
    with pytest.raises(ValueError):
        normalize("x", steps=("case", "typo"))


# --- consistency is a different fact from agreement ---------------------------------------

def test_consistency_separates_a_near_miss_from_a_wild_one():
    """Both are 2/3 agreement; they are not equally worrying. 13.0 is outside the 1%
    tolerance so it genuinely dissents, but it dissents by far less than 1240 does."""
    near, wild = [12.4, 12.4, 13.0], [12.4, 12.4, 1240.0]
    assert agreement(near)["c_ensemble"] == agreement(wild)["c_ensemble"] == pytest.approx(2 / 3)
    assert consistency(near) > consistency(wild)


def test_consistency_is_none_for_non_numeric_and_for_a_single_value():
    assert consistency(["geosmin", "geosmin"]) is None
    assert consistency([4.2]) is None
    assert consistency([]) is None


def test_consistency_is_none_when_a_pass_found_nothing():
    """Zero spread among the values that exist is NOT consistency when a third of the
    ensemble came back empty. Reporting 1.0 would let the composite reward a field for the
    very gap that c_ensemble is penalising."""
    assert consistency([12.4, 12.4, None]) is None
    assert summarize([12.4, 12.4, None])["c_consistency"] is None
    # ...while the agreement fraction still records the gap
    assert summarize([12.4, 12.4, None])["c_ensemble"] == pytest.approx(2 / 3)


def test_a_mixed_numeric_and_text_field_yields_no_consistency():
    """Spread is undefined if not every pass produced a scalar."""
    assert consistency([12.4, "not reported", 12.4]) is None


def test_consistency_is_one_when_identical():
    assert consistency([4.2, 4.2, 4.2]) == pytest.approx(1.0)


def test_summarize_emits_both_signals_for_a_confidence_composite():
    s = summarize([12.4, 12.4, 14.2])
    assert s["c_ensemble"] == pytest.approx(2 / 3)
    assert 0.0 <= s["c_consistency"] <= 1.0
    assert s["k"] == 3 and s["n_agreeing"] == 2


# --- absence is a dissenting vote, never a candidate -----------------------------------------
# The bug this pins: when more passes missed a value than found it, None won the vote, the
# modal became "nothing", and the whole record was dropped. A compound one pass found and two
# missed is the single most interesting thing an ensemble can surface — deleting it is the
# worst available outcome, and it happened silently.

def test_a_value_only_one_pass_found_survives_with_low_agreement():
    r = agreement(["geosmin", None, None])
    assert r["modal_value"] == "geosmin"        # NOT None
    assert r["c_ensemble"] == pytest.approx(1 / 3)
    assert r["n_missing"] == 2


def test_absence_still_costs_even_though_it_cannot_win():
    """It stays in the denominator: 2 of 3 agreeing is 2/3, not 2/2."""
    assert agreement([12.4, 12.4, None])["c_ensemble"] == pytest.approx(2 / 3)
    assert agreement([12.4, 12.4])["c_ensemble"] == 1.0


def test_two_proposals_and_one_absence_is_a_tie_not_a_consensus():
    r = agreement(["a", "b", None])
    assert r["ambiguous_modal"] and r["modal_value"] is None
    assert r["c_ensemble"] == pytest.approx(1 / 3)


def test_all_passes_missing_yields_no_signal():
    r = agreement([None, None, None])
    assert r["c_ensemble"] is None and r["n_missing"] == 3
