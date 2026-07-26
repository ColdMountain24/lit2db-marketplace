"""Merging k extraction passes — where ALIGNMENT, not comparison, is the hard part.

Three independent passes over one paper may find five compounds, four, and six. Before any
field can be compared you must know which record in pass A is the same entity as which record
in pass B. Get that wrong and you compare one enzyme's kcat against a different enzyme's —
a failure that produces a confident, well-grounded, entirely wrong record.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.ensemble import merge_passes

PROV = {"kind": "literature", "source_id": "S1", "retrieval_timestamp": "2026-07-19T00:00:00Z",
        "producing_process": "p@1", "source_status": "active",
        "verbatim_quote": "q", "char_offset": 1}

IDS = {"compound": "name"}


def _rec(rid, name, **fields):
    fs = [{"field_name": "name", "value": name, "provenance": PROV}]
    fs += [{"field_name": k, "value": v, "provenance": {**PROV, "verbatim_quote": f"{k}={v}"}}
           for k, v in fields.items()]
    return {"record_id": rid, "entity_type": "compound", "fields": fs}


def _cc(res, ident, field):
    rec = next(r for r in res["records"]
               if any(f["field_name"] == "name" and f["value"] == ident for f in r["fields"]))
    return next(f for f in rec["fields"] if f["field_name"] == field)


# --- alignment ----------------------------------------------------------------------------

def test_records_align_on_the_identity_field_not_on_order():
    """The passes list the same two compounds in OPPOSITE order. Positional alignment would
    compare geosmin's yield against pentalenene's."""
    a = [_rec("a1", "geosmin", yield_mg=12.4), _rec("a2", "pentalenene", yield_mg=3.1)]
    b = [_rec("b1", "pentalenene", yield_mg=3.1), _rec("b2", "geosmin", yield_mg=12.4)]
    res = merge_passes([a, b], IDS)
    assert len(res["records"]) == 2
    assert _cc(res, "geosmin", "yield_mg")["confidence_components"]["c_ensemble"] == 1.0
    assert _cc(res, "pentalenene", "yield_mg")["confidence_components"]["c_ensemble"] == 1.0


def test_identity_matching_is_normalized():
    """'Geosmin' and 'geosmin' are one entity, not two."""
    res = merge_passes([[_rec("a", "Geosmin", yield_mg=1.0)],
                        [_rec("b", "geosmin", yield_mg=1.0)]], IDS)
    assert len(res["records"]) == 1


def test_a_record_only_some_passes_found_cannot_reach_full_agreement():
    """The key move: a record a pass did not find is a MISSING VALUE for every one of its
    fields. So a compound 1 of 3 passes saw cannot auto-accept on any field, with no separate
    record-level score that could drift out of sync with the field-level one."""
    a = [_rec("a1", "geosmin", yield_mg=12.4), _rec("a2", "cattleyene", yield_mg=0.4)]
    b = [_rec("b1", "geosmin", yield_mg=12.4)]
    c = [_rec("c1", "geosmin", yield_mg=12.4)]
    res = merge_passes([a, b, c], IDS)
    assert _cc(res, "geosmin", "yield_mg")["confidence_components"]["c_ensemble"] == 1.0
    assert _cc(res, "cattleyene", "yield_mg")["confidence_components"]["c_ensemble"] == \
        pytest.approx(1 / 3)


def test_disagreement_on_a_shared_field_is_reported():
    a = [_rec("a", "geosmin", kcat=12.4)]
    b = [_rec("b", "geosmin", kcat=12.4)]
    c = [_rec("c", "geosmin", kcat=99.9)]
    res = merge_passes([a, b, c], IDS)
    fv = _cc(res, "geosmin", "kcat")
    assert fv["confidence_components"]["c_ensemble"] == pytest.approx(2 / 3)
    assert fv["value"] == 12.4                       # the modal value survives


def test_a_field_only_one_pass_extracted_is_marked_as_such():
    """Same mechanism as a missing record: absence is a value."""
    res = merge_passes([[_rec("a", "geosmin", smiles="CCO")],
                        [_rec("b", "geosmin")],
                        [_rec("c", "geosmin")]], IDS)
    assert _cc(res, "geosmin", "smiles")["confidence_components"]["c_ensemble"] == \
        pytest.approx(1 / 3)


# --- provenance must not be reassembled from the wrong pass ---------------------------------

def test_provenance_comes_from_a_pass_that_produced_the_surviving_value():
    """Pairing the modal value with a dissenting pass's quote would manufacture evidence: the
    record would carry a quote that does not support the value beside it."""
    a = [_rec("a", "geosmin", kcat=99.9)]            # dissenter, listed FIRST
    b = [_rec("b", "geosmin", kcat=12.4)]
    c = [_rec("c", "geosmin", kcat=12.4)]
    fv = _cc(merge_passes([a, b, c], IDS), "geosmin", "kcat")
    assert fv["value"] == 12.4
    assert fv["provenance"]["verbatim_quote"] == "kcat=12.4"      # not "kcat=99.9"


# --- the alignment failure mode that must never be silent ------------------------------------

def test_multiple_records_without_an_identity_field_raises():
    """Positional alignment across INDEPENDENT passes is a coin flip. Refuse rather than
    quietly compare different entities to each other."""
    a = [_rec("a1", "geosmin", y=1), _rec("a2", "pentalenene", y=2)]
    with pytest.raises(ValueError, match="identity field"):
        merge_passes([a, a], identity_fields={})


def test_a_single_record_per_pass_aligns_without_an_identity_field():
    """The one-row-per-paper case is safe and must not be made to invent an identity field."""
    res = merge_passes([[_rec("a", "geosmin", y=1)], [_rec("b", "geosmin", y=1)]],
                       identity_fields={})
    assert len(res["records"]) == 1
    assert res["records"][0]["fields"][0]["confidence_components"]["c_ensemble"] == 1.0


# --- the report, and what it is for -----------------------------------------------------------

def test_the_report_travels_alongside_rather_than_inside_the_record():
    """FieldValue is a frozen contract; widening it to carry debug detail is exactly the quiet
    schema growth the ratification invariant exists to prevent."""
    res = merge_passes([[_rec("a", "geosmin", kcat=12.4)],
                        [_rec("b", "geosmin", kcat=99.9)]], IDS)
    key = "compound:geosmin:kcat"
    assert key in res["ensemble"]
    assert res["ensemble"][key]["k"] == 2
    assert len(res["ensemble"][key]["groups"]) == 2      # both candidates visible to a human
    for f in res["records"][0]["fields"]:
        assert "groups" not in f and "ensemble" not in f


def test_alignment_summary_reports_how_many_passes_found_each_entity():
    a = [_rec("a1", "geosmin", y=1), _rec("a2", "cattleyene", y=2)]
    b = [_rec("b1", "geosmin", y=1)]
    got = {x["identity"]: x["found_by_passes"] for x in merge_passes([a, b], IDS)["alignment"]}
    assert got == {"geosmin": 2, "cattleyene": 1}


def test_empty_pass_list_raises():
    with pytest.raises(ValueError):
        merge_passes([])


def test_a_pass_that_returned_nothing_still_counts_toward_k():
    """An extractor that found nothing is evidence, not an absence of evidence — it must lower
    agreement, not be quietly dropped from the denominator."""
    res = merge_passes([[_rec("a", "geosmin", y=1)], [], []], IDS)
    assert res["k"] == 3
    assert _cc(res, "geosmin", "y")["confidence_components"]["c_ensemble"] == pytest.approx(1 / 3)
