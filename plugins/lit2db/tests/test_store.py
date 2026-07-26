"""The Stage-1 store defines the coordinate system every offset is expressed in.

The property under test throughout: **an offset means "index into full.txt" and nothing
else**, and it round-trips. If that fails, every grounded quote in the database points
somewhere other than where it claims to, and the failure is silent — the quote check would
still pass against whatever text happens to sit at the wrong index.
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.store import (
    build_from_jats, find_spans, quote_at, section_of, write_store,
)

JATS = """<article>
  <front>
    <article-meta>
      <article-title>Terpene synthases of <italic>Streptomyces</italic></article-title>
      <abstract><p>We report a kcat of 12.4 s<sup>-1</sup> for the wild type.</p></abstract>
    </article-meta>
  </front>
  <body>
    <sec><title>1. Introduction</title>
      <p>Bacterial terpenoids are structurally diverse.</p>
      <sec><title>1.1 Scope</title><p>We focus on the wild type enzyme.</p></sec>
    </sec>
    <sec><title>2. Results</title>
      <p>The mutant showed a kcat of 3.1 s<sup>-1</sup>.</p>
      <table-wrap>
        <label>Table 1</label><caption><p>Kinetic parameters.</p></caption>
        <table><tbody>
          <tr><td>enzyme</td><td>kcat</td></tr>
          <tr><td>wild type</td><td>12.4</td></tr>
        </tbody></table>
      </table-wrap>
      <fig><label>Fig 1</label><caption><p>Reaction scheme.</p></caption></fig>
    </sec>
  </body>
  <back>
    <sec><title>Appendix A</title><p>Supplementary assay conditions at pH 7.4.</p></sec>
    <ref-list>
      <ref><mixed-citation>Smith J. A kcat of 999.9 was reported. J Irrelevant. 2019.</mixed-citation></ref>
    </ref-list>
  </back>
</article>"""


@pytest.fixture
def store():
    return build_from_jats(JATS, "PMC_TEST", meta={"doi": "10.0/x", "source_status": "active"})


# --- the offset contract ------------------------------------------------------------------

#: "Abstract" is a label this module supplies — JATS <abstract> carries no <title> — so its
#: span holds the abstract prose without repeating the word. Every other label is lifted from
#: the document and must therefore open its own span.
SYNTHETIC_LABELS = {"Abstract", "(untitled)"}


def test_every_section_offset_slices_its_own_text(store):
    for s in store["sections"]:
        span = store["full_text"][s["start"]:s["end"]]
        assert span.strip(), f"section {s['title']!r} has an empty span"
        if s["title"] not in SYNTHETIC_LABELS:
            assert span.lstrip().startswith(s["title"]), \
                f"section {s['title']!r} does not begin at its recorded offset"


def test_the_abstract_span_holds_the_abstract(store):
    """Pinned separately since it is exempt from the title-prefix rule above."""
    s = next(x for x in store["sections"] if x["title"] == "Abstract")
    assert "kcat of 12.4" in store["full_text"][s["start"]:s["end"]]


def test_found_offsets_round_trip_to_the_same_text(store):
    for span in find_spans(store, "12.4"):
        assert quote_at(store, span["start"], span["end"]) == "12.4"


def test_repeated_values_get_distinct_offsets(store):
    """The whole reason the offset is load-bearing: 12.4 appears in the abstract AND in
    Table 1, and those are different pieces of evidence."""
    spans = find_spans(store, "12.4")
    assert len(spans) >= 2
    assert len({s["start"] for s in spans}) == len(spans)


def test_find_spans_returns_all_occurrences_not_just_the_first(store):
    assert len(find_spans(store, "wild type")) >= 2


# --- what is included --------------------------------------------------------------------

def test_table_cells_are_in_the_store():
    """In this literature the measurements live in tables; a store without them would push
    most real values into 'not grounded'."""
    s = build_from_jats(JATS, "x")
    assert "wild type\t12.4" in s["full_text"]
    assert "Table 1" in s["full_text"]


def test_title_abstract_figures_and_appendices_are_included(store):
    t = store["full_text"]
    assert "Terpene synthases of Streptomyces" in t     # title, markup flattened
    assert "kcat of 12.4 s-1" in t                      # abstract, <sup> flattened inline
    assert "Reaction scheme." in t                      # figure caption
    assert "pH 7.4" in t                                # <back> appendix kept


def test_the_reference_list_is_excluded(store):
    """The one deliberate exclusion. A bibliography is dense with OTHER papers' claims, so
    leaving it in lets a value ground against text this paper never asserted."""
    assert "999.9" not in store["full_text"]
    assert "J Irrelevant" not in store["full_text"]


# --- sections ------------------------------------------------------------------------------

def test_section_lookup_returns_the_most_specific_match(store):
    off = store["full_text"].index("We focus on the wild type")
    assert section_of(store, off) == "1.1 Scope"        # not "1. Introduction"


def test_section_of_an_offset_in_results(store):
    off = store["full_text"].index("The mutant showed")
    assert section_of(store, off) == "2. Results"


def test_nested_sections_are_recorded(store):
    titles = [s["title"] for s in store["sections"]]
    assert "Abstract" in titles and "1. Introduction" in titles and "1.1 Scope" in titles


# --- determinism, the property the whole coordinate system rests on -------------------------

def test_rebuilding_produces_byte_identical_text():
    """If a rebuild shifted the text, every offset recorded against the old store would
    silently point somewhere else."""
    a = build_from_jats(JATS, "x")["full_text"]
    b = build_from_jats(JATS, "x")["full_text"]
    assert a == b


def test_namespaced_jats_is_not_silently_empty():
    """An unnamespaced parser against namespaced input would yield an empty store, which
    downstream cannot distinguish from a paper with nothing in it."""
    ns = JATS.replace("<article>", '<article xmlns="http://jats.nlm.nih.gov">', 1)
    assert "12.4" in build_from_jats(ns, "x")["full_text"]


def test_a_source_that_yields_no_text_raises_rather_than_emitting_an_empty_store():
    with pytest.raises(ValueError, match="no text"):
        build_from_jats("<article><body/></article>", "empty")


def test_unparseable_xml_raises(store):
    with pytest.raises(Exception):
        build_from_jats("<article><unclosed>", "bad")


# --- on-disk layout, which is how the extractor reaches it ----------------------------------

def test_write_store_lays_out_the_three_files(tmp_path, store):
    paths = write_store(store, tmp_path)
    assert Path(paths["full_text"]).read_text(encoding="utf-8") == store["full_text"]
    secs = json.loads(Path(paths["sections"]).read_text())
    assert secs == store["sections"]
    meta = json.loads(Path(paths["meta"]).read_text())
    assert meta["source_id"] == "PMC_TEST" and meta["doi"] == "10.0/x"
    assert meta["chars"] == len(store["full_text"])


def test_offsets_still_round_trip_after_a_write_and_read(tmp_path, store):
    """The offset must survive the trip through disk — that is the path the extractor uses."""
    paths = write_store(store, tmp_path)
    on_disk = Path(paths["full_text"]).read_text(encoding="utf-8")
    span = find_spans(store, "3.1")[0]
    assert quote_at(on_disk, span["start"], span["end"]) == "3.1"


def test_source_id_is_sanitised_into_a_safe_directory_name(tmp_path):
    s = build_from_jats(JATS, "doi:10.1234/abc def")
    d = Path(write_store(s, tmp_path)["dir"])
    assert d.exists() and "/" not in d.name and " " not in d.name
