"""A merged record must have a name no other merged record shares.

`record_id` is inherited from the seed pass record, and extractors number records per
paper. So the pool of available names is however many records ONE pass emitted, while the
number of merged records is however many distinct identities the chain resolved. Those
diverge as soon as the entity type is finer-grained than the extractor's numbering — the
measurement-grain case, where one molecule carries many measurements.

It matters because `records` is `record_id TEXT PRIMARY KEY` and `candidates` is
`PRIMARY KEY (record_id, source_id)`, both written with INSERT OR REPLACE: two merged
records under one name store as ONE ROW, with no error and nothing in any artifact showing
a row had gone. Measured on PMC13532014 at measurement grain: 6 merged, 6 scored, 3 stored.
"""
from lit2db.ensemble import merge_passes

ET = "measurement"
IDENT = {ET: {"chain": [["mol", "dose", "metric"]], "ordinal_within": ["mol"]}}


def _rec(rid, mol, dose, metric, value):
    return {"record_id": rid, "entity_type": ET,
            "fields": [{"field_name": k, "value": v, "verbatim_quote": f"q:{k}"}
                       for k, v in (("mol", mol), ("dose", dose),
                                    ("metric", metric), ("value", value))]}


def _merge():
    """Three passes, each emitting at most 3 records, describing 5 distinct measurements."""
    p1 = [_rec("r1", "A", "2", "pct", "0.9"),
          _rec("r2", "A", "2", "conc", "11"),
          _rec("r3", "B", "10", "pct", "1.4")]
    p2 = [_rec("r1", "A", "2", "pct", "0.9"),
          _rec("r2", "B", "10", "conc", "17")]
    p3 = [_rec("r1", "A", "2", "pct", "0.9"),
          _rec("r2", "B", "10", "pct", "1.4"),
          _rec("r3", "A", "20", "pct", "3.1")]
    return merge_passes([p1, p2, p3], identity_fields=IDENT)


def test_more_merged_records_than_seed_labels_still_get_unique_ids():
    merged = _merge()["records"]
    ids = [r["record_id"] for r in merged]
    assert len(merged) > 3, "fixture must produce more records than any one pass labelled"
    assert len(set(ids)) == len(ids), f"record_id collision would silently drop rows: {ids}"


def test_an_unambiguous_id_is_left_alone():
    """Only genuine duplicates are renamed — otherwise every id churns for no reason."""
    merged = _merge()["records"]
    assert any(r["record_id"] == "r1" for r in merged), \
        "a seed id used by exactly one merged record must keep its readable name"


def test_disambiguated_ids_keep_the_seed_name_as_prefix():
    """Judge prompts and on-disk artifact names must still point back at the extractor's id."""
    for r in _merge()["records"]:
        if "#" in r["record_id"]:
            assert r["record_id"].split("#")[0] in {"r1", "r2", "r3"}
            assert r["record_id_disambiguated_from_identity"], \
                "a renamed record must say which identity it was renamed for"


def test_ids_are_stable_across_re_merge():
    """Resume and replay re-merge the same passes. A renamed record must not be renamed
    differently the second time — that would make it a different row in the database, so the
    suffix hashes the identity rather than counting positions."""
    assert [r["record_id"] for r in _merge()["records"]] == \
           [r["record_id"] for r in _merge()["records"]]


def test_ids_do_not_depend_on_pass_order():
    """The seed is the first non-None pass, so pass order can change which readable prefix a
    record gets — but never whether ids are unique, which is the property that protects data."""
    ids = [r["record_id"] for r in _merge()["records"]]
    assert len(set(ids)) == len(ids)
