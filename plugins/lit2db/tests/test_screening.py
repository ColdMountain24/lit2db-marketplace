"""Screen for the ENTITY, not just the organism — and never call a miss an absence.

Measured gap: a terpenoid wave-1 subset screened on source organism held 382 papers
unambiguously about bacteria, of which only 24% named terpene-synthase machinery anywhere in
title or abstract. Two random picks were gut microbiota of hepatitis B patients, and ginger.
Extracting the other 76% would have cost ~50M tokens to produce nothing, and the near-zero yield
would have read as a broken pipeline.
"""
import pytest

from lit2db.contracts.spec import FieldSpec
from lit2db.screening import screen_corpus, screen_source

TERMS = ["terpene synthase", "terpene cyclase", "TPS"]


def test_a_source_naming_the_entity_is_present_with_its_evidence():
    r = screen_source("Functional characterisation of twelve terpene synthases from actinobacteria",
                      require_any=TERMS)
    assert r["verdict"] == "present"
    assert r["matched"][0]["term"] == "terpene synthase"
    assert "terpene synthase" in r["matched"][0]["quote"]


def test_the_real_false_positives_do_not_pass():
    """Both were in the 'clearly bacterial' bucket; neither is about the entity."""
    for title in ("Gut microbiota of hepatitis B virus-infected patients in the immune-tolerant "
                  "phase show characteristic bacterial composition",
                  "Design and Evaluation of a Zingiber officinale-based antimicrobial formulation"):
        assert screen_source(title, require_any=TERMS)["verdict"] != "present"


def test_a_miss_on_the_HEAD_is_uncertain_not_absent():
    """The honest split. A paper can report an enzyme in its results without announcing it in the
    abstract, so calling a head-miss 'absent' would silently discard real sources."""
    r = screen_source("A study of soil bacteria" + " x" * 5000, require_any=TERMS)
    assert r["verdict"] == "uncertain"


def test_only_a_full_text_miss_may_be_called_absent():
    r = screen_source("A study of soil bacteria", require_any=TERMS, full_text=True)
    assert r["verdict"] == "absent"


def test_an_exclusion_outranks_a_hit():
    """A disqualifier beats a match — a correction notice naming the entity is still not a paper."""
    r = screen_source("Correction to: twelve terpene synthases from actinobacteria",
                      require_any=TERMS, exclude_any=["Correction to:"])
    assert r["verdict"] == "excluded"
    assert r["excluded"][0]["term"] == "Correction to:"


def test_terms_match_on_word_boundaries_so_a_short_abbreviation_stays_narrow():
    """Substring matching would make ratified `TPS` hit ATPSynthase and quietly widen the screen."""
    assert screen_source("mitochondrial ATPSynthase activity", require_any=["TPS"])["verdict"] \
        != "present"
    assert screen_source("the TPS enzyme was assayed", require_any=["TPS"])["verdict"] == "present"


def test_no_required_terms_means_uncertain_not_a_free_pass():
    r = screen_source("anything at all", require_any=[])
    assert r["verdict"] == "uncertain"


def test_screening_partitions_a_corpus_and_never_shrinks_it():
    sources = [("A", "twelve terpene synthases"), ("B", "gut microbiota of patients"),
               ("C", "Correction to: terpene synthase paper"), ("D", "")]
    rep = screen_corpus(sources, require_any=TERMS, exclude_any=["Correction to:"])
    assert rep["n_sources"] == 4, "every source must land in exactly one bucket"
    assert rep["by_verdict"]["present"] == ["A"]
    assert rep["by_verdict"]["excluded"] == ["C"]
    assert sum(rep["counts"].values()) == 4


def test_the_report_says_uncertain_is_not_absent():
    rep = screen_corpus([("A", "x")], require_any=TERMS)
    assert "not the same as absent" in rep["note"]


# --- the other half of the same bug: a prose value in a named-entity field -----------------

def _f(name="product", type_="list[str]", **kw):
    return FieldSpec(name=name, type=type_, definition="d", provenance_granularity="p",
                     ledger_item_id="T9", **kw)


def test_the_value_that_actually_broke_the_run_is_flagged():
    """`product` was declared a named entity, so it was predicted to auto-accept. The extractor
    returned a sentence, so three passes phrased it three ways and it became the binding
    constraint — while nothing reported a schema violation."""
    issues = _f().shape_issues(
        "thujopsan-2β-ol (major product) and thujopsene (minor product)")
    assert any("PROSE" in i for i in issues)
    assert any("declared list[str] but got a single str" in i for i in issues)


def test_a_properly_split_list_is_clean():
    assert _f().shape_issues(["thujopsan-2β-ol", "thujopsene"]) == []


def test_a_long_value_in_a_str_field_is_flagged():
    assert _f(type_="str").shape_issues("a" * 120) != []


def test_a_value_outside_a_ratified_vocabulary_is_flagged():
    f = _f(name="product_class", type_="enum", enum=["monoterpene", "sesquiterpene"])
    assert f.shape_issues("sesquiterpene") == []
    assert any("ratified vocabulary" in i for i in f.shape_issues("triterpene"))


def test_absent_values_raise_nothing():
    assert _f().shape_issues(None) == [] and _f().shape_issues([]) == []


def test_it_is_advisory_and_decides_nothing():
    """Returns findings; the gate and the ledger decide. A schema check that silently rejected
    would be originating a scope decision."""
    assert isinstance(_f().shape_issues("a and b"), list)


def test_a_ratified_term_matches_its_PLURAL():
    """The bug this screen shipped with: `terpene synthase` missed "twelve terpene SYNTHASES",
    the exact title it exists to catch. A screen that misses the plural of its own term is worse
    than none, because it reports a confident `absent`."""
    for title in ("twelve terpene synthases from actinobacteria",
                  "a terpene synthase from Streptomyces",
                  "terpene cyclases in bacteria"):
        assert screen_source(title, require_any=TERMS)["verdict"] == "present", title


def test_the_plural_allowance_does_not_widen_a_short_abbreviation():
    assert screen_source("mitochondrial TPSase", require_any=["TPS"])["verdict"] != "present"
