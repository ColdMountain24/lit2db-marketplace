"""Stage 5: one canonical entity above many per-source rows, and no winner is ever picked.

Without this, a database reports N rows where a curator reports M unique entities and the two
counts cannot be compared — which is the whole point of a benchmark against expert curation.
"""
from lit2db.entity import build_index, canonical_key, explain


def rec(rid, **fields):
    return {"record_id": rid, "fields": [{"field_name": k, "value": v} for k, v in fields.items()]}


def test_accession_is_the_identity_and_the_basis_is_recorded():
    k, basis = canonical_key(rec("r1", accession="WP_153520876", genus_species="X y"))
    assert k == "wp_153520876" and basis == "accession"


def test_the_fallback_runs_only_when_no_accession_exists():
    k, basis = canonical_key(rec("r1", accession=None,
                                 genus_species="Kutzneria kofuensis",
                                 enzyme_name="(+)-δ-Cadinol Synthase"))
    assert basis == "fallback" and "kutzneria kofuensis" in k


def test_an_entity_resolved_by_NAME_is_flagged_as_a_weaker_claim():
    """A database that cannot say which entities were matched on an identifier and which on a
    name is asserting a confidence it did not earn."""
    idx = build_index([rec("a", accession="WP_1", genus_species="A b", enzyme_name="E"),
                       rec("b", genus_species="C d", enzyme_name="F")])
    assert idx["by_basis"] == {"accession": 1, "fallback": 1}


def test_a_record_with_neither_is_unresolved_not_silently_dropped():
    idx = build_index([rec("a", substrate="FPP")])
    assert idx["unresolved"] == ["a"] and idx["n_entities"] == 0


def test_the_same_enzyme_in_two_papers_becomes_ONE_entity():
    """The measurement that makes a benchmark comparison meaningful."""
    idx = build_index([
        rec("p1#1", accession="WP_153520876", source_id="PMC1", genus_species="Streptomyces jumonjiensis"),
        rec("p2#7", accession="WP_153520876", source_id="PMC2", genus_species="Streptomyces jumonjiensis"),
    ])
    assert idx["n_records"] == 2 and idx["n_entities"] == 1
    e = idx["entities"][0]
    assert e["n_sources"] == 2 and sorted(e["member_record_ids"]) == ["p1#1", "p2#7"]


def test_the_evidence_trail_is_never_collapsed():
    """A canonical entity is a LINKAGE layer. Every member row survives with its own id."""
    idx = build_index([rec("a", accession="W1", source_id="S1", substrate="FPP"),
                       rec("b", accession="W1", source_id="S2", substrate="FPP")])
    assert idx["entities"][0]["member_record_ids"] == ["a", "b"]
    assert idx["entities"][0]["n_records"] == 2


def test_a_cross_source_conflict_is_CLASSIFIED_not_adjudicated():
    """Two sources, one entity, different substrates. Both values are reported; neither wins."""
    idx = build_index([rec("a", accession="W1", source_id="S1", substrate="FPP"),
                       rec("b", accession="W1", source_id="S2", substrate="GGPP")])
    assert idx["n_entities"] == 1
    c = idx["conflicts"][0]
    assert sorted(c["fields"]["substrate"]) == ["fpp", "ggpp"]
    assert "winner" not in c and "resolved" not in c


def test_agreement_across_sources_is_not_a_conflict():
    idx = build_index([rec("a", accession="W1", source_id="S1", substrate="FPP"),
                       rec("b", accession="W1", source_id="S2", substrate="fpp  ")])
    assert idx["conflicts"] == [], "normalization applies here too (D-035)"


def test_multi_valued_fields_compare_as_sets_not_by_order():
    idx = build_index([rec("a", accession="W1", source_id="S1", product=["x", "y"]),
                       rec("b", accession="W1", source_id="S2", product=["y", "x"])])
    assert idx["conflicts"] == []


def test_explain_reports_ENTITIES_which_is_the_comparable_number():
    idx = build_index([rec("a", accession="W1", source_id="S1"),
                       rec("b", accession="W1", source_id="S2"),
                       rec("c", accession="W2", source_id="S1")])
    text = explain(idx)
    assert "3 records -> 2 canonical entities" in text
    assert "1 cross-source duplicates linked" in text


def test_two_accession_namespaces_stay_separate_and_the_limit_is_stated():
    """A RefSeq and a UniProt id for one protein are two entities until an authority resolver,
    pinned to a version, says otherwise. Stated rather than silently guessed."""
    idx = build_index([rec("a", accession="WP_153520876"), rec("b", accession="A0A1234")])
    assert idx["n_entities"] == 2
    assert "accession namespaces" in idx["note"]
