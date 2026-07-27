"""A multi-valued field must ground per element, not as a stringified list.

The defect this pins was found by running the wave-1 calibration slice, not by a test: a
`list[str]` value was compared via `str(value)`, so `["(+)-δ-cadinol"]` became the literal
`"['(+)-δ-cadinol']"` and scored `string_absent` — while the identical scalar scored 1.0.

Its shape is the one this project keeps re-finding: the check did not error, it returned a
confident wrong answer. `product` and `product_class` are ratified `list[...]` fields in the
frozen terpenoid schema, so EVERY multi-valued value in that database scored 0.0 and could
never auto-accept, and the run reported it as extractor failure rather than a missing code
path. D-052 had already given the ensemble per-element unanimity; grounding never got the
matching treatment, and nothing compared the two.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("srv", ROOT / "mcp" / "lit2db_mcp" / "server.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
ground = S.ground_literature


QUOTE = "1\tKutzneria kofuensis DSM 43851\tMBB5895433\t(+)-δ-cadinola"


def test_the_exact_defect_a_single_element_list_grounds_like_its_scalar():
    """The regression, verbatim from the calibration run."""
    assert ground(value="(+)-δ-cadinol", quote=QUOTE)["c_grounded"] == 1.0
    assert ground(value=["(+)-δ-cadinol"], quote=QUOTE)["c_grounded"] == 1.0


@pytest.mark.parametrize("value", ["(+)-δ-cadinol", ["(+)-δ-cadinol"]])
def test_scalar_and_list_never_disagree_on_the_same_content(value):
    assert ground(value=value, quote=QUOTE)["c_grounded"] == 1.0


def test_partial_grounding_is_partial_and_names_what_is_missing():
    """A five-product list with unsupported entries must not pass whole or fail whole —
    the fraction is what routes it to repair instead of accept or review."""
    q = "the enzyme produced epi-isozizaene and myrcene from the substrate"
    r = ground(value=["epi-isozizaene", "myrcene", "sylvestrene", "terpinolene"], quote=q)
    assert r["c_grounded"] == 0.5
    assert r["mode"] == "list_partial"
    assert set(r["ungrounded"]) == {"sylvestrene", "terpinolene"}


def test_a_fully_ungrounded_list_scores_zero():
    r = ground(value=["nope", "also-nope"], quote=QUOTE)
    assert r["c_grounded"] == 0.0 and r["mode"] == "list_absent"


def test_an_empty_list_is_the_absence_of_a_value_not_a_grounded_one():
    """Averaging over zero elements must not become a vacuous 1.0 — that would auto-accept
    a field nobody extracted."""
    assert ground(value=[], quote=QUOTE)["c_grounded"] == 0.0


def test_numeric_elements_still_use_numeric_tolerance():
    """The list path delegates to the same scalar rule, so 4.20 still matches 4.2."""
    r = ground(value=[4.2, 7.0], quote="values of 4.20 and 7.00 were recorded")
    assert r["c_grounded"] == 1.0


def test_a_missing_quote_still_fails_closed_for_lists():
    assert ground(value=["anything"], quote="")["c_grounded"] == 0.0
