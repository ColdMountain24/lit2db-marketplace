"""Spec-derived context (D-111): it formats the researcher's ratified words and adds nothing.

The invariant under test is the one that makes this feature safe to have at all. Giving a model
context about what the database wants is only free if every sentence of that context is something
the researcher already ratified. The moment the renderer can be fed an unratified field, "context"
becomes a channel for domain content to enter a domain-blind scaffold — which is the regression
this project exists to prevent, and it would arrive looking like a helpfulness improvement.
"""
import json

import pytest

from lit2db.contracts import (
    RatificationLedger, LedgerItem, RatificationStatus, FieldSpec, CorpusQuery, SourceScope,
    SchemaReadySpec, MLTask,
)
from lit2db.spec_context import load_spec, spec_context


def _ledger():
    return RatificationLedger(items=[
        LedgerItem(item_id="F1", kind="field", summary="ok",
                   status=RatificationStatus.ACCEPTED),
        LedgerItem(item_id="F2", kind="field", summary="not ratified",
                   status=RatificationStatus.PROPOSED),
        LedgerItem(item_id="Q1", kind="source_scope", summary="corpus query",
                   status=RatificationStatus.ACCEPTED),
    ])


def _scope():
    return SourceScope(adapters=["literature"],
                       queries=[CorpusQuery(corpus="europepmc", query='TITLE:"x"',
                                            ledger_item_id="Q1")])


def _spec(**over):
    kw = dict(
        research_question="Which widgets are reported, and on what evidence?",
        ml_task=MLTask.regression,
        unit_of_analysis="(widget, source)",
        fields=[FieldSpec(name="widget_name", type="str", definition="The name as printed.",
                          provenance_granularity="per-source", ledger_item_id="F1"),
                FieldSpec(name="basis", type="enum", enum=["isolated", "inferred"],
                          definition="How the link is evidenced.",
                          provenance_granularity="per-source", ledger_item_id="F1")],
        negative_data_policy="A source with nothing qualifying is a recorded negative.",
        inclusion_exclusion={"scope": "Widgets only. [C1]"},
        source_scope=_scope(), ledger=_ledger(), spec_version="test-v1")
    kw.update(over)
    return SchemaReadySpec(**kw)


def test_renders_only_ratified_content():
    out = spec_context(_spec())
    for expected in ("Which widgets are reported", "(widget, source)", "Widgets only. [C1]",
                     "widget_name", "The name as printed.", "isolated | inferred",
                     "recorded negative", "test-v1"):
        assert expected in out, f"missing ratified content: {expected!r}"


def test_unratified_field_cannot_reach_a_prompt():
    """The load-bearing test. The guard is the TYPE, not a convention in the renderer.

    A hand-built dict carrying a field that traces to a PROPOSED ledger item must fail at
    `load_spec`, before a single character of it is formatted.
    """
    bad = _spec().model_dump(mode="json")
    bad["fields"].append({
        "name": "sneaky", "type": "str", "definition": "domain content nobody ratified",
        "provenance_granularity": "x", "ledger_item_id": "F2",   # PROPOSED, not ACCEPTED
    })
    with pytest.raises(Exception):
        load_spec(bad)
    with pytest.raises(Exception):
        spec_context(bad)


def test_empty_vocab_bindings_are_PRINTED_not_omitted():
    """Silence reads as 'this project has no naming conventions'; the truth is 'none ratified'.

    D-112 measured the cost of the difference: the only unstable name judgement across both
    shadow-grounding arms was `sp. RJA2961` vs "the Streptomyces strain RJA2961", an equivalence
    the frozen compound spec does not contain. An omitted heading hides that gap.
    """
    out = spec_context(_spec(controlled_vocab_bindings={}))
    assert "Naming conventions ratified for this project" in out
    assert "None ratified for this project" in out
    assert "do not assume an equivalence this project has not ratified" in out


def test_ratified_bindings_are_rendered():
    out = spec_context(_spec(controlled_vocab_bindings={"sp.": "interchangeable with 'strain'"}))
    assert "sp.: interchangeable with 'strain'" in out
    assert "None ratified" not in out


def test_only_fields_narrows_and_refuses_unknown_names():
    out = spec_context(_spec(), only_fields=["basis"])
    assert "basis" in out and "isolated | inferred" in out
    assert "widget_name" not in out
    with pytest.raises(KeyError):
        spec_context(_spec(), only_fields=["no_such_field"])


def test_context_declares_it_is_not_evidence():
    """Context must never be usable as a source. A model handed the research question and asked
    'does this quote support this value' could otherwise ground a value against the SPEC."""
    out = spec_context(_spec())
    assert "does not tell you what any particular source says" in out
    assert "Do not treat it as evidence about a source" in out


def test_accepts_a_path(tmp_path):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(_spec().model_dump(mode="json")), encoding="utf-8")
    assert "Which widgets are reported" in spec_context(p)
    assert "Which widgets are reported" in spec_context(str(p))


def test_the_real_frozen_spec_renders_if_present():
    """Against the actual ratified compound spec when the private workspace is checked out."""
    import pathlib
    real = (pathlib.Path(__file__).resolve().parents[4] / "lit2db-workspace" / "reference-data"
            / "terpenoid-compounds" / "schema_ready_spec_compound_FROZEN.json")
    if not real.exists():
        pytest.skip("private workspace not present")
    out = spec_context(real, only_fields=["species"])
    assert "compound-v1-FROZEN" in out
    assert "Re-isolation of a known bacterial compound does not earn an entry" in out
    # the gap D-112 found, still open: no sp./strain equivalence is ratified
    assert "None ratified for this project" in out
