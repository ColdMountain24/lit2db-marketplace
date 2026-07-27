"""Stage 5 — one canonical entity above many per-source rows. Classify disagreement; never resolve it.

`merge_passes` aligns k passes WITHIN one source. `dedup` collapses duplicate PAPERS. Neither
says that the enzyme in paper A is the enzyme in paper B — and without that, a database reports N
rows where a curator reports M unique entities, and the two counts cannot be compared at all.
That gap is why this module exists.

**Canonical rows sit ABOVE per-source rows; the evidence trail is never collapsed.** Every member
keeps its own provenance, its own quote, its own gate decision. A canonical entity is a *linkage
layer*, not a merge: nothing is rewritten, nothing is averaged, and no source row is deleted
because another source said something different.

**Disagreement is CLASSIFIED, not adjudicated.** When two sources give different values for the
same field of the same entity, this records the conflict and surfaces it. It does not pick a
winner — the same rule the ensemble follows, for the same reason: picking would be a judgement,
and this layer exists precisely so that judgements are made by people and models that can be held
to account, not by a comparison function.

Identity is the ratified rule (D-046 T4), applied across SOURCES instead of across passes:
accession where the source states one, else the normalized `(genus_species + enzyme_name)` pair.
Measured on a real paper, accession was captured by all three models for 15/15 records while the
name-based fallback aligned 0/15 before the compound fields were split — which is why the
accession-first order is not a preference but the thing that makes this work.

**Known limit, stated rather than hidden:** one protein can carry different accessions in
different namespaces (RefSeq `WP_`, GenBank, UniProt). This module treats those as distinct
entities, because collapsing them requires an external authority (UniProt/NCBI) pinned to a
version — a Stage-1 structured-adapter job, not something to improvise per record. Entities
resolved by fallback rather than accession are flagged so that cost is visible.

Deliberately STDLIB-ONLY, like `gate`, `accounting` and `dedup`.
"""
from __future__ import annotations

from collections import defaultdict

from .ensemble import normalize

# Identity, in priority order. Same rule as T4; the fallback only runs when no accession exists.
DEFAULT_IDENTITY = ("accession",)
DEFAULT_FALLBACK = ("genus_species", "enzyme_name")

# Provenance, not values. Two sources necessarily carry different source_ids — that is what makes
# them two sources, not a disagreement about the entity. Sweeping them as fields reported every
# multi-source entity as conflicted, which would have buried the real conflicts in noise.
PROVENANCE_FIELDS = frozenset({"source_id", "record_id", "entity_type", "retrieval_timestamp",
                               "producing_process", "process_fingerprint", "doi", "pmid",
                               "char_offset", "verbatim_quote", "section", "source_status"})


def _fields(record: dict) -> dict:
    """Accept both raw `{field_name, value}` lists and already-flattened dicts."""
    if "fields" in record:
        return {f.get("field_name"): f.get("value") for f in record["fields"]}
    return {k: v for k, v in record.items() if k not in ("record_id", "entity_type", "fields")}


def canonical_key(record: dict, *, identity=DEFAULT_IDENTITY, fallback=DEFAULT_FALLBACK) -> tuple:
    """`(key, basis)` — basis is 'accession', 'fallback', or 'unresolved'.

    The basis travels with the key on purpose. An entity resolved by name is a weaker claim than
    one resolved by accession, and a database that cannot say which is which is asserting a
    confidence it did not earn.
    """
    f = _fields(record)
    for name in identity:
        v = f.get(name)
        if v not in (None, "", [], {}):
            return normalize(str(v)), "accession"
    parts = [normalize(str(f.get(n) or "")) for n in fallback]
    if all(parts):
        return " | ".join(parts), "fallback"
    return None, "unresolved"


def build_index(records, *, identity=DEFAULT_IDENTITY, fallback=DEFAULT_FALLBACK,
                compare_fields=None) -> dict:
    """Group per-source records into canonical entities and report cross-source conflicts.

    `records` are gate-eligible records, each carrying its own provenance (and so its own
    `source_id`). `compare_fields` limits the conflict sweep; default is every field seen.

    Returns `{entities, n_entities, n_records, by_basis, conflicts, unresolved}`.
    """
    groups: dict = defaultdict(list)
    unresolved = []
    for r in records:
        key, basis = canonical_key(r, identity=identity, fallback=fallback)
        if key is None:
            unresolved.append(r.get("record_id"))
            continue
        groups[(key, basis)].append(r)

    entities, conflicts = [], []
    for (key, basis), members in sorted(groups.items()):
        sources = sorted({(_fields(m).get("source_id")
                           or (m.get("fields") or [{}])[0].get("provenance", {}).get("source_id")
                           or m.get("source_id") or "?") for m in members})
        # Cross-source conflicts: the same field of one entity, valued differently by two sources.
        seen: dict = defaultdict(set)
        for m in members:
            for fname, v in _fields(m).items():
                if fname in PROVENANCE_FIELDS:
                    continue
                if compare_fields and fname not in compare_fields:
                    continue
                if v in (None, "", [], {}):
                    continue
                seen[fname].add(normalize(str(v)) if not isinstance(v, (list, tuple))
                                else " ; ".join(sorted(normalize(str(x)) for x in v)))
        entity_conflicts = {f: sorted(vals) for f, vals in seen.items() if len(vals) > 1}
        if entity_conflicts:
            conflicts.append({"canonical_key": key, "basis": basis, "sources": sources,
                              "fields": entity_conflicts})
        entities.append({
            "canonical_key": key, "basis": basis, "n_records": len(members),
            "sources": sources, "n_sources": len(sources),
            "member_record_ids": [m.get("record_id") for m in members],
            "conflicts": entity_conflicts,
        })

    by_basis: dict = defaultdict(int)
    for e in entities:
        by_basis[e["basis"]] += 1
    return {
        "entities": entities,
        "n_entities": len(entities),
        "n_records": len(list(records)),
        "by_basis": dict(by_basis),
        "conflicts": conflicts,
        "unresolved": unresolved,
        "note": ("Canonical rows sit ABOVE per-source rows; no source row is rewritten or "
                 "deleted. Conflicts are CLASSIFIED for review, never adjudicated here. Entities "
                 "with basis='fallback' were matched on name, not identifier — a weaker claim. "
                 "One protein under two accession namespaces will appear as two entities until "
                 "an authority resolver, pinned to a version, collapses them."),
    }


def explain(index: dict) -> str:
    """The lines a run manifest should print — including the number that makes a benchmark
    comparison meaningful: unique ENTITIES, not rows."""
    n_e, n_r = index["n_entities"], index["n_records"]
    lines = [f"{n_r} records -> {n_e} canonical entities "
             f"({n_r - n_e} cross-source duplicates linked)"]
    if index["by_basis"]:
        lines.append("  resolved by: " + ", ".join(f"{k}={v}" for k, v in index["by_basis"].items()))
    if index["unresolved"]:
        lines.append(f"  UNRESOLVED (no accession and an incomplete fallback): "
                     f"{len(index['unresolved'])}")
    if index["conflicts"]:
        lines.append(f"  {len(index['conflicts'])} entities carry cross-source field conflicts "
                     f"-> human review, not adjudicated here")
        for c in index["conflicts"][:3]:
            f, vals = next(iter(c["fields"].items()))
            lines.append(f"    {c['canonical_key'][:28]}: {f} = {vals[:2]}")
    return "\n".join(lines)
