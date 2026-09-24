"""Aligning records by WHERE they were read, which is what makes a claim mergeable (M13).

A measured value has a name. A claim is a sentence, and a sentence is the worst possible
identity: two independent readings of one finding quote overlapping but different spans of it,
so a text identity does not match and the pair falls to the document-wide ordinal tier. That
tier was already 40% of records on the first BBB wave, and it is the one the ensemble says it
cannot vouch for. For a review pack, where every record is a sentence, it would be the backbone.

The store is the coordinate system, so what is stable between two readings of one finding is
that they point at the same place.
"""
from __future__ import annotations

from lit2db.ensemble import anchor_passes, merge_passes, sentence_bounds, uses_anchors
from lit2db.pipeline import catalogue_questions

TEXT = ("Vitamin D lowered TSH over six months. It did not change FT4 or FT3 in the treated "
        "group. Adverse events were similar between arms.")
CLAIM_IDENTITY = {"claim": {"chain": [], "ordinal_within": ["@sentence"]}}


def _rec(rid, value, quote):
    return {"record_id": rid, "entity_type": "claim",
            "fields": [{"field_name": "claim_text", "value": value, "verbatim_quote": quote}]}


def _merge(p1, p2, text=TEXT, identity=None):
    return merge_passes(anchor_passes([p1, p2], text),
                        identity_fields=identity or CLAIM_IDENTITY)


def test_sentences_tile_the_text_with_no_gaps():
    bounds = sentence_bounds(TEXT)
    assert len(bounds) == 3
    assert TEXT[bounds[1][0]:bounds[1][1]].startswith("It did not change")


def test_two_readings_of_one_finding_align_even_when_they_quote_different_spans():
    """The case that forced this. Text identity gives two singletons; the place gives one
    record found by both readings, which is the agreement signal the gate runs on."""
    m = _merge([_rec("r1", "no change in FT4/FT3",
                     "It did not change FT4 or FT3 in the treated group.")],
               [_rec("r1", "FT4 and FT3 unchanged", "did not change FT4 or FT3")])
    assert len(m["records"]) == 1
    assert m["alignment"][0]["found_by_passes"] == 2

    by_text = merge_passes(
        [[_rec("r1", "no change in FT4/FT3",
               "It did not change FT4 or FT3 in the treated group.")],
         [_rec("r1", "FT4 and FT3 unchanged", "did not change FT4 or FT3")]],
        identity_fields={"claim": "claim_text"})
    assert len(by_text["records"]) == 2, "the defect this exists to fix"


def test_two_claims_in_one_sentence_do_not_collapse_into_one():
    q = "It did not change FT4 or FT3 in the treated group."
    both = [_rec("r1", "FT4 unchanged", q), _rec("r2", "FT3 unchanged", q)]
    m = _merge(both, [_rec("r1", "FT4 unchanged", q), _rec("r2", "FT3 unchanged", q)])
    assert len(m["records"]) == 2
    assert {a["found_by_passes"] for a in m["alignment"]} == {2}


def test_a_reading_that_missed_one_claim_still_lines_up_with_the_one_it_found():
    q = "It did not change FT4 or FT3 in the treated group."
    m = _merge([_rec("r1", "FT4 unchanged", q), _rec("r2", "FT3 unchanged", q)],
               [_rec("r1", "FT4 unchanged", q)])
    found = sorted(a["found_by_passes"] for a in m["alignment"])
    assert found == [1, 2], "the missed claim stays a singleton rather than pairing wrongly"


def test_claims_in_different_sentences_never_merge():
    m = _merge([_rec("r1", "TSH fell", "Vitamin D lowered TSH over six months.")],
               [_rec("r1", "AEs similar", "Adverse events were similar between arms.")])
    assert len(m["records"]) == 2


def test_anchored_is_named_apart_from_ordinal():
    """Both are positional, and calling them the same thing buries the difference: a
    document-wide ordinal is 'the nth unnamed record in this paper', while an anchored one is
    'the nth claim in this one sentence', where both readings were looking at the same 20 words.
    Anchored is still the weakest rule here, so it is labelled, not promoted."""
    m = _merge([_rec("r1", "a", "Adverse events were similar between arms.")],
               [_rec("r1", "a", "Adverse events were similar")])
    assert m["alignment"][0]["identity_tier"] == "anchored"


def test_an_unfindable_quote_is_left_unanchored_rather_than_placed_by_guess():
    passes = anchor_passes([[_rec("r1", "x", "a sentence this paper does not contain")]], TEXT)
    assert "_anchor" not in passes[0][0]


def test_a_quote_that_occurs_twice_raises_a_question_instead_of_being_trusted():
    text = "The dose was 10 mg. The dose was 10 mg. Tail sentence."
    m = _merge([_rec("r1", "dose 10 mg", "The dose was 10 mg.")],
               [_rec("r1", "dose 10 mg", "The dose was 10 mg.")], text=text)
    assert m["alignment"][0]["anchor_unique"] is False
    qs = catalogue_questions("P1", m, [], [], unjudged=0, review_lane=(), vetoed=[],
                             audit_disagreements=[], blocked_on_process=0,
                             duplicate_record_ids=[])
    assert [q["kind"] for q in qs] == ["ambiguous_anchor"]


def test_a_unique_anchor_raises_no_question_at_all():
    """Otherwise a review pack, where every record is anchored, would file a weak-identity
    question for every record it produced and the catalogue would mean nothing."""
    m = _merge([_rec("r1", "a", "Adverse events were similar between arms.")],
               [_rec("r1", "a", "Adverse events were similar")])
    qs = catalogue_questions("P1", m, [], [], unjudged=0, review_lane=(), vetoed=[],
                             audit_disagreements=[], blocked_on_process=0,
                             duplicate_record_ids=[])
    assert qs == []


def test_anchoring_only_runs_for_a_config_that_asked_for_it():
    """It reads the stored text off disk, so a config that never mentions an anchor level must
    not pay for it, and a malformed one must answer no rather than raise mid-paper."""
    assert uses_anchors({"claim": {"chain": [], "ordinal_within": ["@sentence"]}})
    assert uses_anchors({"x": ["@span", "name"]})
    assert not uses_anchors({"compound": {"chain": ["inchikey"], "ordinal_within": ["genus"]}})
    assert not uses_anchors(["accession"]) and not uses_anchors(None) and not uses_anchors("x")
