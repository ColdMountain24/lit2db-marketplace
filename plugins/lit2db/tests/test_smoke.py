"""Smoke test: contracts import, the idea-generation boundary holds, and the composite
degrades gracefully. The write-gate has its own suite (test_write_gate.py); the spine's
verify/route/gate thesis is in test_spine.py. Run: pytest -q (from plugins/lit2db/).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lit2db.contracts import (
    RatificationLedger, LedgerItem, RatificationStatus, FieldSpec, CorpusQuery, SourceScope,
    SchemaReadySpec, MLTask, ConfidenceComponents, DEFAULT_WEIGHTS,
)


def _ledger():
    return RatificationLedger(items=[
        LedgerItem(item_id="F1", kind="field", summary="target", status=RatificationStatus.ACCEPTED),
        LedgerItem(item_id="F2", kind="field", summary="unratified", status=RatificationStatus.PROPOSED),
        LedgerItem(item_id="Q1", kind="source_scope", summary="corpus query",
                   status=RatificationStatus.ACCEPTED),
    ])


def _query(ledger_item_id="Q1"):
    return CorpusQuery(corpus="europepmc", query='TITLE:"x" AND (FIRST_PDATE:[2020 TO 2025])',
                       ledger_item_id=ledger_item_id)


def _scope(**over):
    kw = {"adapters": ["literature"], "queries": [_query()]}
    kw.update(over)
    return SourceScope(**kw)


def test_ratified_field_accepted():
    led = _ledger()
    f = FieldSpec(name="y", type="float", definition="d", provenance_granularity="per-instance",
                  ledger_item_id="F1")
    spec = SchemaReadySpec(research_question="q", ml_task=MLTask.regression,
        unit_of_analysis="(e,m,c,s)", fields=[f], negative_data_policy="p",
        source_scope=_scope(), ledger=led)
    assert len(spec.fields) == 1


def test_unratified_field_rejected():
    """The load-bearing invariant: an agent cannot inject an unratified field."""
    led = _ledger()
    f = FieldSpec(name="sneaky", type="str", definition="d", provenance_granularity="x",
                  ledger_item_id="F2")   # F2 is only PROPOSED
    try:
        SchemaReadySpec(research_question="q", ml_task=MLTask.regression,
            unit_of_analysis="u", fields=[f], negative_data_policy="p",
            source_scope=_scope(), ledger=led)
        assert False, "unratified field was accepted — boundary broken"
    except Exception:
        pass


# --- The corpus half of the same invariant ------------------------------------------
# A spec could previously name a corpus ("europepmc") and freeze without recording the
# query that defines it. The database is then unreproducible in exactly the way the method
# claims it is not, and a substantive researcher decision — which papers are in scope —
# hides behind a structural-looking string. Found the hard way: the query behind the M7
# corpus was never written down and could only be reconstructed to within 2%.

def _spec(scope, led=None):
    led = led or _ledger()
    f = FieldSpec(name="y", type="float", definition="d", provenance_granularity="per-instance",
                  ledger_item_id="F1")
    return SchemaReadySpec(research_question="q", ml_task=MLTask.regression,
        unit_of_analysis="(e,m,c,s)", fields=[f], negative_data_policy="p",
        source_scope=scope, ledger=led)


def test_literature_corpus_without_a_query_is_rejected():
    try:
        _spec(SourceScope(adapters=["literature"], corpora=["europepmc"]))
        assert False, "a named-but-undefined corpus was accepted — not reproducible"
    except Exception as exc:
        assert "CorpusQuery" in str(exc)


def test_corpus_query_must_be_ratified():
    """Which papers are in scope is researcher substance, not scaffold structure."""
    try:
        _spec(_scope(queries=[_query(ledger_item_id="F2")]))   # F2 is only PROPOSED
        assert False, "an unratified corpus query was accepted"
    except Exception as exc:
        assert "not ACCEPTED" in str(exc)


def test_structured_only_spec_needs_no_corpus_query():
    """The requirement is scoped to the literature adapter; a structured-only project has
    no search query to record and must not be forced to invent one."""
    spec = _spec(SourceScope(adapters=["structured"], structured_databases=["pubchem"]))
    assert spec.source_scope.queries == []


def test_corpus_query_records_what_the_run_returned():
    """result_counts is how a refresh detects drift: the same query against a growing index
    returns more next month, and that must be visible rather than silently absorbed."""
    q = CorpusQuery(corpus="europepmc", query='TITLE:"x"', ledger_item_id="Q1",
                    endpoint="https://example.org/rest",
                    executed_at="2026-07-26T00:00:00Z",
                    result_counts={"hits": 100, "retrievable": 49})
    spec = _spec(_scope(queries=[q]))
    got = spec.source_scope.queries[0]
    assert got.result_counts["hits"] == 100 and got.executed_at.startswith("2026-07-26")


def test_composite_degrades_without_logprob():
    cc = ConfidenceComponents(c_grounded=0.92, c_verbal=0.8, c_ensemble=1.0,
                              c_judge=0.9, c_consistency=0.85)  # no c_logprob
    v = cc.composite(DEFAULT_WEIGHTS["numeric"])
    assert 0.0 <= v <= 1.0
