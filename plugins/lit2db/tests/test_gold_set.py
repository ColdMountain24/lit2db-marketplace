"""A threshold must carry its calibration, or say it has none.

The failure this prevents is in the project's own history: a threshold recalibrated 0.95 -> 0.74
on n=25 from a pilot went on to govern a 191-record database, with the caveat detached from the
number everywhere it was quoted.
"""
import pytest
from pydantic import ValidationError

from lit2db.contracts.spec import (
    CorpusQuery, GoldSet, LedgerItem, RatificationLedger, RatificationStatus,
    SchemaReadySpec, SourceScope,
)


def _ledger(*ids, status=RatificationStatus.ACCEPTED):
    return RatificationLedger(items=[
        LedgerItem(item_id=i, kind="source_scope", summary=f"item {i}", status=status)
        for i in ids])


def _spec(gold_set=None, gold_status=RatificationStatus.ACCEPTED):
    items = [LedgerItem(item_id="L1", kind="source_scope", summary="corpus",
                        status=RatificationStatus.ACCEPTED)]
    if gold_set is not None:
        items.append(LedgerItem(item_id=gold_set.ledger_item_id, kind="axis_setting",
                                summary="gold set", status=gold_status))
    return SchemaReadySpec(
        research_question="q", ml_task="knowledge_graph", unit_of_analysis="(e, s)",
        fields=[], negative_data_policy="none",
        source_scope=SourceScope(adapters=["literature"], corpora=["c"], queries=[
            CorpusQuery(corpus="c", query="TITLE:x", ledger_item_id="L1")]),
        ledger=RatificationLedger(items=items), gold_set=gold_set)


def _gold(**kw):
    base = dict(n_records=50, n_papers=15, annotators=["Aryan"], sampling_frame="source",
                ledger_item_id="G1")
    base.update(kw)
    return GoldSet(**base)


def test_no_gold_set_means_the_datasheet_says_uncalibrated():
    spec = _spec()
    assert "UNCALIBRATED" in spec.threshold_provenance
    assert "placeholder" in spec.threshold_provenance


def test_a_gold_set_without_a_threshold_still_reads_uncalibrated():
    """Annotating is not calibrating. Having a gold set does not license a number."""
    spec = _spec(_gold())
    assert "UNCALIBRATED" in spec.threshold_provenance


def test_a_calibrated_threshold_carries_its_n_and_its_frame():
    spec = _spec(_gold(calibrated_auto_accept_threshold=0.81))
    p = spec.threshold_provenance
    assert "0.81" in p and "50 records" in p and "15 papers" in p
    assert "Aryan" in p
    assert "precision and recall" in p, "a source-sampled frame can measure both"


def test_an_output_sampled_gold_set_says_precision_only():
    """A record the extractor never proposed cannot appear in an output sample, so recall is
    structurally unmeasurable — the datasheet must not imply otherwise."""
    spec = _spec(_gold(sampling_frame="output", calibrated_auto_accept_threshold=0.81))
    assert "precision only" in spec.threshold_provenance


def test_output_sampling_attaches_its_own_limitation():
    g = _gold(sampling_frame="output", calibrated_auto_accept_threshold=0.81)
    assert "recall is structurally unmeasurable" in (g.adjudication_log or "")


def test_calibrating_on_the_benchmark_is_refused():
    """The D-024 trap: tune the gate until it agrees with the ground truth and the subsequent
    comparison measures nothing."""
    with pytest.raises(ValidationError, match="blind comparison"):
        _gold(calibrated_auto_accept_threshold=0.81, independent_of_benchmark=False)


def test_a_gold_set_nobody_ratified_cannot_license_a_threshold():
    with pytest.raises(ValidationError, match="not ACCEPTED"):
        _spec(_gold(calibrated_auto_accept_threshold=0.81),
              gold_status=RatificationStatus.PROPOSED)


def test_a_gold_set_needs_at_least_one_annotator_and_one_record():
    with pytest.raises(ValidationError):
        _gold(annotators=[])
    with pytest.raises(ValidationError):
        _gold(n_records=0)


def test_per_field_agreement_is_free_form_so_it_stays_domain_blind():
    g = _gold(per_field_agreement={"product": 0.62, "source_organism": 0.98})
    assert g.per_field_agreement["product"] == 0.62


def test_a_single_annotator_design_can_still_record_test_retest():
    g = _gold(annotators=["Aryan"], intra_annotator_agreement=0.88,
              adjudication_log="analysis/gold/adjudications.jsonl")
    assert g.intra_annotator_agreement == 0.88
    assert g.adjudication_log.endswith(".jsonl")


def test_the_toggle_is_the_whole_point():
    """Same spec, one field different, and the datasheet claim flips."""
    off = _spec()
    on = _spec(_gold(calibrated_auto_accept_threshold=0.74))
    assert "UNCALIBRATED" in off.threshold_provenance
    assert "UNCALIBRATED" not in on.threshold_provenance
    assert "calibrated to 0.74" in on.threshold_provenance
