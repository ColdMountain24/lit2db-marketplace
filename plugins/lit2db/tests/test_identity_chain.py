"""Identity may be a FALLBACK CHAIN, and what resolved it must travel with the record.

A ratified identity rule is often "this stable id, else this combination of fields" — the
terpenoid project's is *accession, else (organism + enzyme name)*. `merge_passes` originally
took a single field name, so that rule was unimplementable: papers stating no accession had
null identity and the merge halted. Refusing was right (aligning independent passes by
position compares different enzymes), but it blocked 2 of 3 calibration papers.

The rules are not equally trustworthy, so the tier travels with the alignment:
accession aligned 15/15 where the name fallback aligned 0/15 (D-058), and `ordinal` — order of
appearance — is weaker still, because order is not guaranteed stable across independent passes.
A disagreement under `ordinal` may be a mis-pairing rather than evidence, and nothing
downstream can tell the difference unless the tier is recorded.
"""
import pytest

from lit2db.ensemble import merge_passes

SPEC = {"enzyme": {"chain": ["accession", ["organism", "name"]],
                   "ordinal_within": ["organism"]}}


def rec(rid, **fields):
    return {"record_id": rid, "entity_type": "enzyme",
            "fields": [{"field_name": k, "value": v} for k, v in fields.items()
                       if v is not None]}


def test_the_primary_key_wins_and_is_labelled_primary():
    p = [[rec("a", accession="WP_1", organism="S. coelicolor", name="AlphaS")],
         [rec("b", accession="WP_1", organism="S. coelicolor", name="Alpha Synthase")]]
    m = merge_passes(p, identity_fields=SPEC)
    assert len(m["records"]) == 1, "accession must align them despite the differing names"
    assert m["alignment"][0]["identity_tier"] == "primary"


def test_the_composite_fallback_resolves_when_no_accession_is_stated():
    """The case that blocked two calibration papers outright."""
    p = [[rec("a", organism="S. coelicolor", name="AlphaS")],
         [rec("b", organism="S. coelicolor", name="AlphaS")]]
    m = merge_passes(p, identity_fields=SPEC)
    assert len(m["records"]) == 1
    assert m["alignment"][0]["identity_tier"] == "fallback1"


def test_a_composite_level_needs_every_part_present():
    """A half-filled composite key would collide with every other half-filled one, silently
    merging different enzymes — so it must not resolve at all."""
    p = [[rec("a", organism="S. coelicolor")],          # no name
         [rec("b", organism="S. coelicolor")]]
    m = merge_passes(p, identity_fields={"enzyme": {"chain": ["accession",
                                                             ["organism", "name"]]}})
    # No chain level resolves and no ordinal scope is configured -> singleton, one per pass.
    assert all(a["identity_tier"] == "singleton" for a in m["alignment"])


def test_ordinal_counts_only_the_records_the_chain_could_not_identify():
    """The bug this test exists for, measured on PMC12723471.

    Counting every record in scope makes the numbering depend on how many NAMED records
    happened to precede an unnamed one — which differs per pass. That produced 5 ordinal keys
    where 3 enzymes exist, 4 of them matched by a single pass, and it reads as catastrophic
    model disagreement rather than as an alignment bug.
    """
    named = dict(organism="C. japonensis", name="NamedS")
    p = [
        # pass 1 lists the unnamed enzymes FIRST
        [rec("u1", organism="C. japonensis"), rec("u2", organism="C. japonensis"),
         rec("n1", **named)],
        # pass 2 lists the named one first — ordinals must not shift because of it
        [rec("n1", **named), rec("u1", organism="C. japonensis"),
         rec("u2", organism="C. japonensis")],
    ]
    m = merge_passes(p, identity_fields=SPEC)
    ordinals = [a for a in m["alignment"] if a["identity_tier"] == "ordinal"]
    assert len(ordinals) == 2, f"expected 2 unnamed enzymes, got {len(ordinals)}"
    assert all(a["found_by_passes"] == 2 for a in ordinals), \
        "both passes found both unnamed enzymes; ordinal must pair them"


def test_the_tier_counts_are_reported_so_weak_alignment_is_visible():
    p = [[rec("a", accession="WP_1", organism="O", name="N"),
          rec("b", organism="O", name="Other"),
          rec("c", organism="O")],
         [rec("a", accession="WP_1", organism="O", name="N"),
          rec("b", organism="O", name="Other"),
          rec("c", organism="O")]]
    m = merge_passes(p, identity_fields=SPEC)
    assert m["identity_tiers"]["primary"] == 1
    assert m["identity_tiers"]["fallback1"] == 1
    assert m["identity_tiers"]["ordinal"] == 1


def test_a_bare_field_name_still_works():
    """Back-compatibility: every existing caller passes a string."""
    p = [[rec("a", accession="WP_1")], [rec("b", accession="WP_1")]]
    m = merge_passes(p, identity_fields={"enzyme": "accession"})
    assert len(m["records"]) == 1 and m["alignment"][0]["identity_tier"] == "primary"


def test_multi_record_types_without_any_identity_still_refuse():
    """The original protection must survive: no rule at all means no guessing."""
    p = [[rec("a", name="X"), rec("b", name="Y")], [rec("c", name="X")]]
    with pytest.raises(ValueError, match="identity"):
        merge_passes(p, identity_fields={})


# --- D-069: the last resort may not have a prerequisite ---------------------------------
CHASSIS = {"enzyme": {"chain": ["accession", ["organism", "name"]],
                      "ordinal_within": ["organism"]}}


def test_a_record_with_no_organism_still_gets_an_identity():
    """Measured: 2 of 3 fresh corpus papers died here, both chassis studies putting 8 and 23
    synthases through one host — the richest papers in the corpus, and unrepresentable.

    The chain's last resort was ordinal scoped INSIDE `organism`, so a record missing the
    organism had no accession, no name-pair and no ordinal: no identity at all, and
    merge_passes refused the entire paper.
    """
    p = [[rec("a", name="bisabolene synthase")]] * 2
    m = merge_passes(p, identity_fields=CHASSIS)
    assert len(m["records"]) == 1
    assert m["alignment"][0]["identity_tier"] == "ordinal_unscoped"


def test_unscoped_ordinal_is_a_DIFFERENT_tier_from_scoped():
    """It is weaker again — nothing scopes it, so two passes listing entities in different
    orders mis-pair. Blending it into `ordinal` would hide that from everything downstream."""
    p = [[rec("a", organism="S. albus"), rec("b", name="orphan synthase")]] * 2
    m = merge_passes(p, identity_fields=CHASSIS)
    tiers = {a["identity_tier"] for a in m["alignment"]}
    assert tiers == {"ordinal", "ordinal_unscoped"}


def test_a_record_with_an_organism_does_not_consume_an_unscoped_number():
    """The two sequences must not share a counter, or they drift apart per pass — the same
    class of bug as counting ordinals over all records instead of unidentified ones."""
    a = [rec("x", organism="S. albus"), rec("y", name="orphan"), rec("z", organism="S. albus")]
    b = [rec("y", name="orphan"), rec("x", organism="S. albus"), rec("z", organism="S. albus")]
    m = merge_passes([a, b], identity_fields=CHASSIS)
    assert all(al["found_by_passes"] == 2 for al in m["alignment"]), (
        "reordering between passes must not break alignment for either sequence")


def test_ordinal_stays_OFF_when_the_researcher_ratified_no_tiebreak():
    """`ordinal_within` absent is a DECISION, not an omission: it says this entity type may not
    be aligned by position. Regressed once — the D-069 fix initially applied ordinal to every
    spec with a chain, silently enabling positional alignment nobody ratified."""
    p = [[rec("a", organism="S. coelicolor")], [rec("b", organism="S. coelicolor")]]
    m = merge_passes(p, identity_fields={"enzyme": {"chain": ["accession",
                                                             ["organism", "name"]]}})
    assert all(a["identity_tier"] == "singleton" for a in m["alignment"])
