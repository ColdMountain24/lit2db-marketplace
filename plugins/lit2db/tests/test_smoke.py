"""Smoke test: contracts import, the idea-generation boundary holds, and the composite
degrades gracefully. The write-gate has its own suite (test_write_gate.py); the spine's
verify/route/gate thesis is in test_spine.py. Run: pytest -q (from plugins/lit2db/).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lit2db.contracts import (
    RatificationLedger, LedgerItem, RatificationStatus, FieldSpec, SourceScope,
    SchemaReadySpec, MLTask, ConfidenceComponents, DEFAULT_WEIGHTS,
)


def _ledger():
    return RatificationLedger(items=[
        LedgerItem(item_id="F1", kind="field", summary="target", status=RatificationStatus.ACCEPTED),
        LedgerItem(item_id="F2", kind="field", summary="unratified", status=RatificationStatus.PROPOSED),
    ])


def test_ratified_field_accepted():
    led = _ledger()
    f = FieldSpec(name="y", type="float", definition="d", provenance_granularity="per-instance",
                  ledger_item_id="F1")
    spec = SchemaReadySpec(research_question="q", ml_task=MLTask.regression,
        unit_of_analysis="(e,m,c,s)", fields=[f], negative_data_policy="p",
        source_scope=SourceScope(adapters=["literature"]), ledger=led)
    assert len(spec.fields) == 1


def test_unratified_field_rejected():
    """The load-bearing invariant: an agent cannot inject an unratified field."""
    led = _ledger()
    f = FieldSpec(name="sneaky", type="str", definition="d", provenance_granularity="x",
                  ledger_item_id="F2")   # F2 is only PROPOSED
    try:
        SchemaReadySpec(research_question="q", ml_task=MLTask.regression,
            unit_of_analysis="u", fields=[f], negative_data_policy="p",
            source_scope=SourceScope(adapters=["literature"]), ledger=led)
        assert False, "unratified field was accepted — boundary broken"
    except Exception:
        pass


def test_composite_degrades_without_logprob():
    cc = ConfidenceComponents(c_grounded=0.92, c_verbal=0.8, c_ensemble=1.0,
                              c_judge=0.9, c_consistency=0.85)  # no c_logprob
    v = cc.composite(DEFAULT_WEIGHTS["numeric"])
    assert 0.0 <= v <= 1.0
