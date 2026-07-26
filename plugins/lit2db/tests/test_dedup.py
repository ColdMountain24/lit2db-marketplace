"""One canonical work per paper — and never by wrongly collapsing two distinct ones.

The asymmetry these tests defend: a false merge destroys a record and its provenance with no
trace; a false split leaves two rows and a flag a human clears in seconds. So rung 4 flags and
never merges, and the threshold is high.

The motivating real case is in `test_the_real_corpus_defect`: the frozen terpenoid corpus counted
49 papers when it holds 47 distinct works, because two correction notices sit alongside the
papers they correct.
"""
import pytest

from lit2db.dedup import (
    FUZZY_TITLE_RATIO,
    Registry,
    dedupe,
    normalize_title,
    strip_correction_prefix,
    title_similarity,
)

P1 = {"pmcid": "PMC1", "doi": "10.1/abc", "pmid": "111",
      "title": "Genome mining of terpene synthases in Streptomyces", "year": "2024"}


def test_rung_1_doi_is_a_true_identifier():
    reg = Registry()
    assert reg.register(P1).verdict == "new"
    dup = reg.register({"pmcid": "PMC2", "doi": "10.1/ABC", "title": "Totally different title"})
    assert dup.verdict == "doi" and dup.matched == "PMC1"
    assert dup.is_duplicate and not dup.needs_review


def test_rung_2_pmid_catches_what_a_missing_doi_would_not():
    reg = Registry()
    reg.register(P1)
    dup = reg.register({"pmcid": "PMC2", "pmid": "111", "title": "Different"})
    assert dup.verdict == "pmid" and dup.matched == "PMC1"


def test_rung_3_source_ids_cover_the_identifier_less_tail():
    reg = Registry()
    reg.register({"pmcid": "PMCX", "arxiv": "2401.00001", "title": "A preprint"})
    dup = reg.register({"id": "other", "arxiv": "2401.00001", "title": "A preprint, renamed"})
    assert dup.verdict == "source_id"


def test_rung_4_flags_a_near_duplicate_and_keeps_BOTH():
    """The load-bearing rule: fuzzy marks, it does not combine."""
    reg = Registry()
    reg.register(P1)
    near = reg.register({"pmcid": "PMC9",
                         "title": "Genome mining of terpene synthases in Streptomyces.",
                         "year": "2024"})
    assert near.verdict == "fuzzy_flagged"
    assert near.needs_review and not near.is_duplicate
    assert len(reg) == 2, "both rows must remain — flagging is not merging"
    assert "kept BOTH" in near.reason


def test_two_genuinely_different_papers_are_not_collapsed():
    reg = Registry()
    reg.register(P1)
    other = reg.register({"pmcid": "PMC3", "year": "2024",
                          "title": "Structural basis of bacterial diterpene cyclase catalysis"})
    assert other.verdict == "new"


def test_markup_is_stripped_before_comparison():
    """Europe PMC returns titles both ways; two renderings of one title are one work."""
    a = "Genome Mining of <i>Streptomyces</i> Terpene Synthases"
    b = "Genome Mining of &lt;i&gt;Streptomyces&lt;/i&gt; Terpene Synthases"
    assert normalize_title(a) == normalize_title(b)
    assert title_similarity(a, b) == 1.0


@pytest.mark.parametrize("title,expected", [
    ("Correction to: Discovery of bifunctional diterpene cyclases", True),
    ("Correction: Structural insights into a bacterial terpene cyclase", True),
    ("Corrigendum to: Terpene biosynthesis", True),
    ("Erratum: Terpene synthases", True),
    ("Publisher Correction: Terpenoids in bacteria", True),
    ("Retraction: A terpene synthase", True),
    ("Discovery of bifunctional diterpene cyclases", False),
    ("Corrections to genome assemblies are common", False),
])
def test_correction_prefixes_are_recognised(title, expected):
    assert strip_correction_prefix(title)[0] is expected


def test_a_correction_is_linked_to_the_work_it_corrects():
    reg = Registry()
    reg.register({"pmcid": "PMC11469919", "year": "2024",
                  "title": "Discovery of bifunctional diterpene cyclases/synthases in bacteria"})
    corr = reg.register({"pmcid": "PMC12302711", "year": "2025",
                         "title": "Correction to: Discovery of bifunctional diterpene "
                                  "cyclases/synthases in bacteria"})
    assert corr.verdict == "correction_of"
    assert corr.matched == "PMC11469919"
    assert corr.needs_review


def test_a_correction_whose_original_is_absent_is_still_not_a_research_paper():
    reg = Registry()
    corr = reg.register({"pmcid": "PMC1", "title": "Erratum: Something not in this corpus"})
    assert corr.verdict == "correction_of" and corr.matched is None


def test_the_real_corpus_defect():
    """49 papers, 47 distinct works — both corrections have their originals present.

    Measured against the frozen terpenoid corpus on 2026-07-26. This is the case the module
    exists for: a 1,697-char correction notice yields either nothing or a spurious duplicate of
    a record already extracted from the 39,562-char original.
    """
    papers = [
        {"pmcid": "PMC11469919", "year": "2024",
         "title": "Discovery of bifunctional diterpene cyclases/synthases in bacteria supports a "
                  "bacterial origin"},
        {"pmcid": "PMC12302711", "year": "2025",
         "title": "Correction to: Discovery of bifunctional diterpene cyclases/synthases in "
                  "bacteria supports a bacterial origin"},
        {"pmcid": "PMC12365925", "year": "2025",
         "title": "Structural insights into a bacterial terpene cyclase fused with haloacid "
                  "dehalogenase"},
        {"pmcid": "PMC12653010", "year": "2025",
         "title": "Correction: Structural insights into a bacterial terpene cyclase fused with "
                  "haloacid dehalogenase"},
        {"pmcid": "PMC12298776", "year": "2025",
         "title": "Genome Mining of Terpene Synthases from Fourteen Streptomyces"},
    ]
    rep = dedupe(papers)
    assert rep["n_in"] == 5
    assert rep["unique"] == 3, "5 records, 3 distinct works"
    assert rep["by_verdict"]["correction_of"] == 2
    assert {f["matched"] for f in rep["flagged"]} == {"PMC11469919", "PMC12365925"}


def test_nothing_is_ever_deleted():
    """A corpus build may not lose a paper to dedup. Everything is kept and reported."""
    papers = [P1,
              {"pmcid": "PMC2", "doi": "10.1/abc", "title": "dup by doi"},
              {"pmcid": "PMC3", "title": "Genome mining of terpene synthases in Streptomyces."}]
    rep = dedupe(papers)
    assert len(rep["results"]) == len(papers)


def test_the_threshold_is_high_because_the_costs_are_asymmetric():
    assert FUZZY_TITLE_RATIO >= 0.9
    reg = Registry()
    reg.register({"pmcid": "A", "title": "Terpene synthases in marine bacteria"})
    # Same topic, different work — must NOT be flagged.
    r = reg.register({"pmcid": "B", "title": "Terpene synthases in soil fungi"})
    assert r.verdict == "new"
