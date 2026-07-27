"""Per-element unanimity: the bar moved from the set to the element, it did not drop.

Set-equality unanimity is the wrong bar for a field that is legitimately a list. Measured on the
terpenoid e2e run, `product` blocked 5/9 records — more than any other field — because one pass
spotting an extra trace peak makes the sets differ and takes down the products all three passes
agreed on.
"""
import pytest

from lit2db.ensemble import agreement, agreement_elementwise


def test_full_agreement_puts_everything_in_the_core():
    r = agreement_elementwise([["thujopsene", "thujopsan-2b-ol"]] * 3)
    assert sorted(r["core"]) == ["thujopsan-2b-ol", "thujopsene"]
    assert r["deferred"] == [] and r["c_ensemble"] == 1.0


def test_the_case_this_exists_for_one_extra_element_no_longer_destroys_the_record():
    """Two passes find two products, the third also reports a trace peak."""
    passes = [["thujopsene", "thujopsan-2b-ol"],
              ["thujopsene", "thujopsan-2b-ol"],
              ["thujopsene", "thujopsan-2b-ol", "trace-cadinene"]]

    # Under set-equality the whole field fails: two distinct sets, best group 2 of 3.
    assert agreement([tuple(p) for p in passes])["c_ensemble"] == pytest.approx(2 / 3)

    r = agreement_elementwise(passes)
    assert sorted(r["core"]) == ["thujopsan-2b-ol", "thujopsene"]      # survives
    assert [d["value"] for d in r["deferred"]] == ["trace-cadinene"]   # reaches a human
    assert r["deferred"][0]["n_agreeing"] == 1
    assert r["c_ensemble"] == 1.0                                      # the CORE is unanimous


def test_the_guarantee_is_unchanged_nothing_partial_enters_the_core():
    r = agreement_elementwise([["a", "b"], ["a", "b"], ["a"]])
    assert r["core"] == ["a"]
    assert [d["value"] for d in r["deferred"]] == ["b"]
    assert r["deferred"][0]["c_ensemble"] == pytest.approx(2 / 3)


def test_no_unanimous_element_means_the_field_fails():
    """No core, so c_ensemble falls back to the best element and the field cannot auto-accept."""
    r = agreement_elementwise([["a"], ["b"], ["c"]])
    assert r["core"] == [] and r["empty_core"] is True
    assert r["c_ensemble"] == pytest.approx(1 / 3)


def test_a_missing_pass_still_costs_because_absence_dissents():
    """Same rule as `agreement`: absence stays in the denominator."""
    r = agreement_elementwise([["a"], ["a"], None])
    assert r["n_missing"] == 1
    assert r["core"] == []                       # only 2 of 3 passes proposed it
    assert r["c_ensemble"] == pytest.approx(2 / 3)


def test_normalization_applies_to_elements():
    """Dissent must imply substance, not typography (D-035)."""
    r = agreement_elementwise([["Thujopsene"], ["thujopsene "], ["THUJOPSENE"]])
    assert len(r["core"]) == 1 and r["deferred"] == []


def test_a_pass_repeating_an_element_votes_once():
    """Otherwise a sloppy pass could manufacture unanimity on its own."""
    r = agreement_elementwise([["a", "a", "a"], ["b"], ["b"]])
    assert r["core"] == []
    assert dict((d["value"], d["n_agreeing"]) for d in r["deferred"]) == {"a": 1, "b": 2}


def test_a_scalar_is_accepted_as_a_one_element_list():
    r = agreement_elementwise(["geosmin", "geosmin", "geosmin"])
    assert r["core"] == ["geosmin"] and r["c_ensemble"] == 1.0


def test_deferred_is_ranked_so_the_near_misses_surface_first():
    r = agreement_elementwise([["a", "b", "c"], ["a", "b"], ["a"]])
    assert r["core"] == ["a"]
    assert [d["value"] for d in r["deferred"]] == ["b", "c"]


def test_empty_and_degenerate_inputs():
    assert agreement_elementwise([])["c_ensemble"] is None
    assert agreement_elementwise([None, None])["c_ensemble"] is None
    assert agreement_elementwise([[], [], []])["core"] == []


def test_it_recovers_the_blocked_terpenoid_records():
    """The measured shape: 5/9 records blocked by `product` under set-equality. Per element,
    a record whose passes share a core survives."""
    blocked = [["compound-1", "compound-2"], ["compound-1", "compound-2"],
               ["compound-1", "compound-2", "compound-3"]]
    assert agreement([tuple(p) for p in blocked])["c_ensemble"] < 1.0   # was blocked
    assert agreement_elementwise(blocked)["c_ensemble"] == 1.0          # now ships its core
