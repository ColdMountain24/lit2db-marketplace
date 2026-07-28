"""Organism -> taxon resolution. Offline: the authority is injected, never called.

Nine of twenty-four adjudicated records were one organism written two ways, which was the
largest single class of researcher attention in the queue. Measured against NCBI Taxonomy on
those exact strings: 0 of 4 resolved as stored, 4 of 4 with the strain stripped.

What these tests pin, in order of what would hurt most if it broke:

  1. **`unreachable` is not `no match`.** One is a run to retry, the other is evidence about the
     organism (D-094). A wave of the first read as the second is plausible and wrong.
  2. **A fold may never merge two real strains.** `KC 191` and `KC 192` are different organisms
     and no rung may touch a numeral.
  3. **The matched rung is reported.** "Confirmed to genus" and "confirmed to species" are
     different facts and must not collapse into one boolean.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lit2db.taxonomy import (candidates, fold_strain, resolve_taxon,  # noqa: E402
                             same_organism, taxon_fields)


def fake(hits: dict):
    """An authority that answers from a dict, keyed on the EXACT query term.

    Exact, not substring: a loose fake let `Nostoc commune` answer for the query
    `Nostoc commune EAWAG 122b`, so the test reported a strain-level match that the real
    authority refuses — measured, NCBI returns nothing for the strain-bearing string. A fixture
    more permissive than the thing it stands in for tests a system that does not exist.

    `None` means UNREACHABLE; an absent key means reached-and-empty.
    """
    import urllib.parse

    def _fetch(url, timeout_s=20.0):
        term = urllib.parse.unquote_plus(url.split("term=", 1)[1])
        if term in hits:
            ids = hits[term]
            return None if ids is None else {"esearchresult": {"idlist": ids}}
        return {"esearchresult": {"idlist": []}}
    return _fetch


# --- 1. fails closed, and says which way ------------------------------------------------
def test_unreachable_is_not_no_match():
    out = resolve_taxon("Streptomyces", "sp. X", fetch=fake({"Streptomyces sp. X": None}))
    assert out["resolved"] is False
    assert out["unreachable"] is True
    assert "NOT evidence" in out["why"]


def test_reached_but_empty_is_evidence_not_a_retry():
    out = resolve_taxon("Nowhereia", "fictus", fetch=fake({}))
    assert out["resolved"] is False
    assert out["unreachable"] is False
    assert "no taxon matched" in out["why"]


def test_same_organism_returns_None_when_either_side_did_not_resolve():
    """None rather than False: 'we could not check' is not 'they differ'. Returning False would
    silently convert an unreachable authority into a disagreement."""
    ok = resolve_taxon("Nostoc", "commune", fetch=fake({"Nostoc commune": ["1178"]}))
    bad = resolve_taxon("Nowhereia", "", fetch=fake({}))
    assert same_organism(ok, bad) is None
    assert same_organism(ok, ok) is True


# --- 2. folds remove conventions, never content -----------------------------------------
def test_two_different_strains_never_fold_together():
    assert fold_strain("sp. KC 191")[0] != fold_strain("sp. KC 192")[0]
    assert fold_strain("Tü 6071")[0] != fold_strain("Tue 6072")[0]


def test_each_fold_reports_the_rung_it_applied():
    """A ladder reported as one number hides which rung did the work."""
    folded, rungs = fold_strain("commune Vaucher (EAWAG 122b)")
    assert folded == "commune eawag 122b"
    assert rungs == ["authority_citation", "brackets"]
    assert fold_strain("commune EAWAG 122b") == ("commune eawag 122b", [])


def test_the_measured_pairs_fold_to_the_same_string():
    """The four real disagreements from the adjudication queue."""
    for a, b in (("sp (#HK18)", "sp. (#HK18)"), ("sp. KC 191", "strain KC 191"),
                 ("commune Vaucher (EAWAG 122b)", "commune EAWAG 122b"),
                 ("sp. Tü 6071", "sp. Tue 6071")):
        assert fold_strain(a)[0] == fold_strain(b)[0], (a, b)


# --- 3. the rung is a reported fact -----------------------------------------------------
def test_sp_is_never_treated_as_a_species_name():
    """`sp.` means 'species not determined'. Querying `Streptomyces sp` as a binomial would ask
    the authority for an organism that does not exist."""
    assert [q for _, q in candidates("Streptomyces", "sp. KC 191")] == \
           ["Streptomyces sp. KC 191", "Streptomyces"]


def test_a_real_species_produces_a_genus_species_rung():
    rungs = [r for r, _ in candidates("Nostoc", "commune EAWAG 122b")]
    assert "genus_species" in rungs


def test_the_matched_rung_is_recorded():
    out = resolve_taxon("Nostoc", "commune EAWAG 122b",
                        fetch=fake({"Nostoc commune": ["1178"]}))
    assert out["taxid"] == "1178"
    assert out["matched_rung"] == "genus_species"      # not merely "resolved"


def test_fields_are_ADDITIVE_and_empty_when_unresolved():
    """The frozen organism fields are never rewritten — this only ever ADDS columns, which is
    what makes it safe while a wave is running against the frozen spec."""
    assert taxon_fields({"resolved": False}, "PMC1", "test") == []
    out = resolve_taxon("Nostoc", "commune", fetch=fake({"Nostoc commune": ["1178"]}))
    fields = taxon_fields(out, "PMC1", "test")
    assert {f["field_name"] for f in fields} == {"organism_taxid", "organism_taxon_rung"}
    assert all(f["provenance"]["kind"] == "structured" for f in fields)
    assert all(f["provenance"]["database"] == "ncbi_taxonomy" for f in fields)
