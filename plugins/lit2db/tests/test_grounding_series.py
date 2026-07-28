"""A value named as one member of an enumerated series grounds against the sentence naming it.

Ratified 2026-07-28 from the compound pilot. A source writing "we propose the trivial names
corvol ethers A and B" reports corvol ether A, but the singular string never appears, so the
strict substring rule scored it 0.0. **Five of six correct records in that run died this way,
all on the identity field.**

This LOOSENS a check that guards the database, so the tests below are weighted toward what it
must still refuse. The rule is structural and domain-blind: `STEM DESIGNATOR` grounds when the
quote contains that stem, optionally pluralised, followed by an enumeration including the
designator. Nothing in it knows what a compound is.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.grounding import ground_literature  # noqa: E402


def g(value, quote):
    return ground_literature(value, quote)["c_grounded"]


# --- the real quotes from the pilot ---------------------------------------------------------

CORVOL = ("two previously unknown and unstable sesquiterpene ethers for which we propose the "
          "trivial names corvol ethers A and B")
SULFA = ("We report the unexpected discovery of three fully unprecedented, sulfonyl-bridged "
         "alkaloid dimers (sulfadixiamycins A-C) from recombinant Streptomyces species")


@pytest.mark.parametrize("value", ["corvol ether A", "corvol ether B"])
def test_a_two_member_list_supports_each_member(value):
    assert g(value, CORVOL) == 1.0


@pytest.mark.parametrize("value", ["sulfadixiamycin A", "sulfadixiamycin B", "sulfadixiamycin C"])
def test_a_range_supports_every_member_including_the_unwritten_middle(value):
    """B is named by 'A-C' without appearing in it. That is the whole point of a range."""
    assert g(value, SULFA) == 1.0


def test_the_pilot_records_that_died_now_ground():
    """The five records this rule exists for, together."""
    assert all(g(v, CORVOL) == 1.0 for v in ("corvol ether A", "corvol ether B"))
    assert all(g(v, SULFA) == 1.0
               for v in ("sulfadixiamycin A", "sulfadixiamycin B", "sulfadixiamycin C"))


# --- what it must still refuse --------------------------------------------------------------

def test_a_member_outside_the_range_is_still_absent():
    assert g("sulfadixiamycin D", SULFA) == 0.0


def test_a_member_outside_the_list_is_still_absent():
    assert g("corvol ether C", CORVOL) == 0.0


def test_a_different_stem_does_not_borrow_the_series():
    assert g("corvol ester A", CORVOL) == 0.0
    assert g("dixiamycin A", SULFA) == 0.0


def test_an_unrelated_quote_grounds_nothing():
    assert g("corvol ether A", "The organism was grown in liquid culture for six days.") == 0.0


def test_a_stem_with_no_enumeration_after_it_does_not_match():
    """'corvol ethers were isolated' names no members, so it supports no particular one."""
    assert g("corvol ether A", "the corvol ethers were isolated from the extract") == 0.0


def test_a_number_elsewhere_in_the_sentence_is_not_an_enumeration():
    assert g("hapalindole 7", "hapalindoles were isolated after 7 days of culture") == 0.0


def test_a_descending_pair_is_not_a_range():
    """'C-A' is not an ascending range; treating it as one would invent membership."""
    assert g("xiamycin B", "the dimers (xiamycins C-A) were obtained") == 0.0


def test_a_short_stem_is_refused_because_it_would_match_almost_anything():
    assert g("cp A", "the cps A and B were characterised") == 0.0


def test_the_value_must_end_in_a_designator_not_a_word():
    assert g("corvol ether oxide", CORVOL) == 0.0


# --- the ordinary rules are untouched -------------------------------------------------------

def test_an_exact_substring_still_grounds_the_plain_way():
    r = ground_literature("pseudomonol", "the new sesquiterpene pseudomonol was isolated")
    assert r["c_grounded"] == 1.0 and r["mode"] == "string_match"


def test_numeric_grounding_is_unaffected():
    assert g(12.4, "a kcat of 12.4 s-1 was measured") == 1.0
    assert g(99.0, "a kcat of 12.4 s-1 was measured") == 0.0


def test_a_genuinely_absent_value_is_still_absent():
    r = ground_literature("merosterol A", "meroterpenoids related to pelorol were identified")
    assert r["c_grounded"] == 0.0 and r["mode"] == "string_absent"


def test_the_mode_names_the_rule_that_fired_so_it_is_auditable():
    assert ground_literature("corvol ether A", CORVOL)["mode"] == "series_match"
    assert ground_literature("sulfadixiamycin B", SULFA)["mode"] == "series_range_match"


# --- a second defect, found by the tests above and older than the series rule ----------------

def test_a_name_containing_a_digit_is_not_grounded_numerically():
    """The false positive this uncovered: `_norm_num` searched anywhere in the value, so any
    name with a digit took the numeric path and matched any nearby number in the quote.
    `hapalindole 7` grounded against "after 7 days of culture" — a wrong write, not a miss."""
    r = ground_literature("hapalindole 7", "hapalindoles were isolated after 7 days of culture")
    assert r["c_grounded"] == 0.0
    assert r["mode"] != "numeric_match"


def test_a_measurement_leading_with_its_number_still_grounds_numerically():
    for value in (12.4, "12.4", "12.4 s-1", "-0.5 kcal/mol"):
        r = ground_literature(value, "we measured 12.4 s-1 and -0.5 kcal/mol respectively")
        assert r["c_grounded"] == 1.0, value
        assert r["mode"] == "numeric_match", value


def test_a_numbered_compound_name_grounds_as_a_string_not_a_number():
    """The schema tells extractors to keep `compound 3` when that is all the paper gives."""
    assert g("compound 3", "the structure of compound 3 was solved by NMR") == 1.0
    assert g("compound 3", "the structure was solved after 3 attempts") == 0.0


def test_the_rule_is_domain_blind():
    """Same structure, nothing to do with chemistry."""
    assert g("mutant 3", "the mutants 1-5 were assayed in triplicate") == 1.0
    assert g("mutant 9", "the mutants 1-5 were assayed in triplicate") == 0.0
