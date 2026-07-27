"""A near-zero yield is either the gate working or a field that cannot be verified.

Nothing distinguished those two before this module. The difference is WHICH FIELD blocked, so
these tests pin the ranking, the review-lane exclusion, and the property that made the real
finding possible: a prose field routing to review is not a failure and must never be counted as
one.
"""
import pytest

from lit2db.contracts.spec import FieldSpec
from lit2db.yield_projection import explain, project, review_lane_from_spec


def fv(name, grounded=1.0, ensemble=1.0):
    return {"field_name": name, "value": "x",
            "confidence_components": {"c_grounded": grounded, "c_ensemble": ensemble}}


def rec(rid, *fields):
    return {"record_id": rid, "fields": list(fields)}


def test_a_clean_record_auto_accepts():
    p = project([rec("r1", fv("a"), fv("b"))])
    assert p["n_auto_accept"] == 1 and p["yield_fraction"] == 1.0
    assert p["blocking_fields"] == []


def test_one_weak_field_blocks_the_whole_record():
    """The gate is all-or-nothing per record — that is the point of a gate."""
    p = project([rec("r1", fv("a"), fv("b", ensemble=0.67))])
    assert p["n_auto_accept"] == 0
    assert p["per_record"][0]["blocked_by"] == ["b"]


def test_either_signal_missing_blocks():
    assert project([rec("r", fv("a", grounded=0.5))])["n_auto_accept"] == 0
    assert project([rec("r", fv("a", ensemble=0.67))])["n_auto_accept"] == 0
    nosig = {"field_name": "a", "value": "x", "confidence_components": {}}
    assert project([rec("r", nosig)])["n_auto_accept"] == 0


def test_the_review_lane_is_excused_not_counted_as_a_blocker():
    """THE finding this module exists for. `function` failing is the schema working; counting it
    as a blocker made every record look broken and the yield read as a dead pipeline."""
    r = rec("r1", fv("a"), fv("function", grounded=0.4, ensemble=0.33))
    assert project([r])["n_auto_accept"] == 0                      # counted -> looks broken
    p = project([r], review_lane={"function"})
    assert p["n_auto_accept"] == 1                                  # excused -> the truth
    assert "function" not in dict(p["blocking_fields"])
    assert dict(p["review_lane_routed"])["function"] == 1


def test_a_review_lane_field_that_happens_to_clear_is_not_reported_as_routed():
    p = project([rec("r1", fv("a"), fv("function"))], review_lane={"function"})
    assert p["n_auto_accept"] == 1
    assert p["review_lane_routed"] == []


def test_blocking_fields_are_ranked_so_the_binding_constraint_is_first():
    recs = [rec("r1", fv("product", ensemble=0.67), fv("name")),
            rec("r2", fv("product", ensemble=0.67), fv("name")),
            rec("r3", fv("product", ensemble=0.67), fv("name", ensemble=0.67))]
    p = project(recs)
    assert p["blocking_fields"][0] == ("product", 3)
    assert dict(p["blocking_fields"])["name"] == 1


def test_the_real_measurement_reproduces():
    """The terpenoid e2e shape: 9 records, `function` in the review lane, `product` binding."""
    recs = []
    for i in range(9):
        fields = [fv("enzyme_name", ensemble=1.0 if i < 5 else 0.67),
                  fv("source_organism", ensemble=1.0 if i < 5 else 0.67),
                  fv("product", ensemble=1.0 if i in (1, 4) else 0.67),
                  fv("function", ensemble=0.33)]
        recs.append(rec(f"ts{i}", *fields))
    p = project(recs, review_lane={"function"})
    assert p["n_auto_accept"] == 2
    assert round(p["yield_fraction"], 2) == 0.22
    assert p["blocking_fields"][0][0] == "product"


def test_the_bar_is_a_lever_not_a_convenience():
    """D-034 made the agreement bar a ratified integer setting. Lowering it changes the yield,
    which is exactly why it must be ratified rather than tuned until the number looks good."""
    recs = [rec("r1", fv("product", ensemble=0.67))]
    assert project(recs, bar=1.0)["n_auto_accept"] == 0
    assert project(recs, bar=0.66)["n_auto_accept"] == 1


def test_review_lane_is_read_from_the_ratified_spec_not_guessed():
    fields = [
        FieldSpec(name="source_organism", type="str", definition="d",
                  provenance_granularity="per enzyme", ledger_item_id="T7"),
        FieldSpec(name="function", type="str", definition="prose",
                  provenance_granularity="per enzyme", ledger_item_id="T11",
                  auto_acceptable=False),
    ]
    class _S:
        pass
    s = _S(); s.fields = fields
    assert review_lane_from_spec(s) == {"function"}
    # and from a plain dict, so it works pre-validation
    assert review_lane_from_spec({"fields": [
        {"name": "function", "auto_acceptable": False}, {"name": "a"}]}) == {"function"}


def test_fields_are_auto_acceptable_by_default():
    f = FieldSpec(name="x", type="str", definition="d",
                  provenance_granularity="p", ledger_item_id="L1")
    assert f.auto_acceptable is True


def test_explain_says_which_field_to_interrogate_first():
    p = project([rec("r1", fv("product", ensemble=0.5))])
    text = explain(p)
    assert "product" in text and "binding constraint" in text
    assert "YIELD IS ZERO" in text


def test_an_empty_run_does_not_divide_by_zero():
    p = project([])
    assert p["n_records"] == 0 and p["yield_fraction"] == 0.0
