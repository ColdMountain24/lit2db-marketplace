"""A store built from a PDF keeps the same coordinate contract as one built from JATS.

`full.txt` is the only coordinate system, so the properties under test are the ones a quote's
offset depends on: a sentence found in the store slices back out of it unchanged, every offset
can be reported as a page, and each normalisation (wraps joined, hyphens, running heads, the
reference list) is applied by a stated rule and counted in meta rather than done silently.

The PDFs are written by hand below (Helvetica, no embedded fonts), so the suite needs nothing
but poppler's `pdftotext`. Tests that need it skip with that reason when it is absent; the test
that it is MISSING runs everywhere.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db import project, store                                     # noqa: E402
from lit2db.review import anchor, load_store                          # noqa: E402
from lit2db.store import (                                            # noqa: E402
    PdfToTextMissing, build_from_pdf, find_spans, page_of, quote_at, section_of, store_dirname,
    write_store,
)

HAVE = shutil.which("pdftotext") is not None
needs_pdftotext = pytest.mark.skipif(not HAVE, reason="pdftotext (poppler) is not installed")


def _caps():
    return store._pdftotext_caps(shutil.which("pdftotext")) if HAVE else {}


needs_hyphen_option = pytest.mark.skipif(
    not HAVE or not _caps().get("remove_hyphens"),
    reason="this pdftotext has no -remove-hyphens option, so it joins every line-end hyphen "
           "itself and lit2db's hyphen rule cannot apply")


# --- a minimal PDF writer -----------------------------------------------------------------------

def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list) -> bytes:
    """One PDF page per item of `pages`. An item is a list of lines: a string is set at the left
    margin on the next line down, `None` leaves a blank line, `(x, y, text)` is placed exactly."""
    n = len(pages)
    page_ids = [4 + 2 * i for i in range(n)]
    content_ids = [5 + 2 * i for i in range(n)]
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: ("<< /Type /Pages /Kids [%s] /Count %d >>"
                % (" ".join(f"{p} 0 R" for p in page_ids), n)).encode(),
            3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
               b"/Encoding /WinAnsiEncoding >>"}
    for i, items in enumerate(pages):
        ops, y = [], 740
        for it in items:
            if it is None:
                y -= 12
                continue
            if isinstance(it, tuple):
                x, yy, text = it
            else:
                x, yy, text = 72, y, it
                y -= 12
            ops.append(f"BT /F1 10 Tf {x} {yy} Td ({_esc(text)}) Tj ET")
        stream = "\n".join(ops).encode("latin-1")
        objs[content_ids[i]] = (b"<< /Length %d >>\nstream\n" % len(stream) + stream
                                + b"\nendstream")
        objs[page_ids[i]] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                             f"/Resources << /Font << /F1 3 0 R >> >> "
                             f"/Contents {content_ids[i]} 0 R >>").encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for oid in sorted(objs):
        offsets[oid] = len(out)
        out += f"{oid} 0 obj\n".encode() + objs[oid] + b"\nendobj\n"
    xref, size = len(out), max(objs) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for oid in range(1, size):
        out += f"{offsets[oid]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


HEADER = "Journal of Widgets 12 (2020) 101-104"


def _page(n, body):
    """A page with a running head at the top and its page number (100 + n) at the foot."""
    return [HEADER, None, *body, None, (300, 60, str(100 + n))]


PAGES = [
    _page(1, [
        "Widget Patterns in Practice", None,
        "Abstract",
        "We study widgets of several kinds across a wide range of settings",
        "and report what we found when the measure-",
        "ment was repeated under the same conditions by a second team.", None,
        "1. Introduction",
        "Widgets are common in many settings and they are often studied by",
        "groups who compare a widget of the newer type-",
        "B design against the standard model used in the earlier reports.",
        "A well-known result is that widgets behave the same in every lab.",
    ]),
    _page(2, [
        "2. Methods",
        "Every widget was measured three times on each of two separate days",
        "by two people who did not know which widget they were measuring.",
        "We used the method that is well-",
        "known to give the same answer whichever person runs it on the day.",
        "The widgets were stored in a dry room between the measurements.",
        "3. Results",
        "The mean widget length was 4.2 cm and the spread was very small.",
    ]),
    _page(3, [
        "4. Discussion",
        "The widgets in this sample were longer than those reported before",
        "and the difference is larger than the spread between the teams.",
        "References",
        "Smith, A. (2001). A widget length of 9.9 cm. Widget Letters 3, 1-2.",
        "Jones, B. (2005). Widgets revisited. Widget Review 7, 10-20.",
        "Appendix A. Extra tables",
        "The appendix lists every widget length that was measured in the lab.",
    ]),
    _page(4, [
        "The appendix continues on this page with more of the measured values",
        "so that a reader can check each one against the summary in the text.",
        "Every value was recorded by hand in the lab notebook on the same day.",
    ]),
]


@pytest.fixture(scope="module")
def pdf():
    return make_pdf(PAGES)


@pytest.fixture(scope="module")
def built(pdf):
    if not HAVE:
        pytest.skip("pdftotext (poppler) is not installed")
    return build_from_pdf(pdf, "PDF_TEST", meta={"doi": "10.0/widget"})


# --- the coordinate contract ---------------------------------------------------------------------

@needs_pdftotext
def test_same_store_shape_as_the_jats_builder(built):
    assert set(built) == {"source_id", "full_text", "sections", "tables", "meta", "stats"}
    assert built["meta"]["source_text_scope"] == "full_text_pdf"
    assert built["meta"]["sections_method"] == "heuristic"
    assert built["meta"]["doi"] == "10.0/widget", "caller meta is kept"
    assert built["meta"]["extractor"].startswith("pdftotext ")
    assert built["stats"]["chars"] == len(built["full_text"])
    for s in built["sections"]:
        assert set(s) == {"title", "start", "end", "depth"}


@needs_pdftotext
def test_a_sentence_round_trips_through_its_offsets(built):
    """The contract every grounded quote relies on: find it, slice at its offsets, get it back."""
    sentence = ("Every widget was measured three times on each of two separate days by two "
                "people who did not know which widget they were measuring.")
    spans = find_spans(built, sentence)
    assert len(spans) == 1, "a hard-wrapped sentence is one line of full.txt, not two"
    s = spans[0]
    assert quote_at(built, s["start"], s["end"]) == sentence
    assert anchor(built["full_text"], sentence, s["start"])["state"] == "exact"
    assert section_of(built, s["start"]) == "2. Methods"
    assert page_of(built, s["start"]) == 2


@needs_pdftotext
def test_page_ranges_cover_each_page_in_order(built):
    pages = built["meta"]["pages"]
    assert built["meta"]["page_count"] == 4 and len(pages) == 4
    for (s0, e0), (s1, _e1) in zip(pages, pages[1:]):
        assert s0 <= e0 <= s1, "ranges are ordered and do not overlap"
    text = built["full_text"]
    assert "Widget Patterns in Practice" in text[slice(*pages[0])]
    assert "The mean widget length" in text[slice(*pages[1])]
    assert "The mean widget length" not in text[slice(*pages[2])]
    assert pages[-1][1] == len(text)
    gap = pages[0][1]                                   # the separator after page 1
    assert page_of(built, gap) is None, "the separator between pages belongs to neither"
    assert page_of({"meta": {}}, 0) is None, "a store without a page map answers None"


@needs_pdftotext
def test_written_and_read_back_the_page_map_survives(built, tmp_path):
    paths = write_store(built, tmp_path)
    back = load_store(tmp_path, "PDF_TEST")
    assert back["full_text"] == built["full_text"]
    assert back["meta"]["pages"] == built["meta"]["pages"]
    i = back["full_text"].index("The mean widget length")
    assert page_of(back, i) == 2
    assert Path(paths["dir"]).name == "PDF_TEST"


@needs_pdftotext
def test_sha256_of_the_exact_bytes_is_stamped(built, pdf):
    assert built["meta"]["pdf_sha256"] == hashlib.sha256(pdf).hexdigest()


@needs_pdftotext
def test_a_path_and_the_same_bytes_build_the_same_store(pdf, built, tmp_path):
    f = tmp_path / "w.pdf"
    f.write_bytes(pdf)
    again = build_from_pdf(f, "PDF_TEST", meta={"doi": "10.0/widget"})
    assert again["full_text"] == built["full_text"]
    assert again["meta"]["pdf_sha256"] == built["meta"]["pdf_sha256"]


# --- normalisation, each by a stated rule and each counted -------------------------------------

@needs_hyphen_option
def test_a_line_end_hyphen_before_a_lowercase_letter_is_removed(built):
    assert "the measurement was repeated" in built["full_text"]
    assert "measure-" not in built["full_text"]
    assert built["meta"]["normalisation"]["hyphens_removed"] >= 1


@needs_hyphen_option
def test_a_line_end_hyphen_before_anything_else_is_kept(built):
    """`type-` + `B design` is `type-B design`: removing the hyphen would invent `typeB`."""
    assert "the newer type-B design" in built["full_text"]
    assert built["meta"]["normalisation"]["hyphens_kept_before_non_lowercase"] >= 1


@needs_hyphen_option
def test_the_documents_own_spelling_keeps_a_compound_hyphen(built):
    """`well-` + `known` before a lowercase letter would become `wellknown` by the bare rule.
    The document prints `well-known` unbroken elsewhere and never `wellknown`, so it is kept."""
    assert "the method that is well-known to give" in built["full_text"]
    assert "wellknown" not in built["full_text"]
    assert built["meta"]["normalisation"]["hyphens_kept_compound"] >= 1


@needs_pdftotext
def test_hard_wrapped_lines_are_joined_and_counted(built):
    assert ("We study widgets of several kinds across a wide range of settings and report"
            in built["full_text"])
    assert built["meta"]["normalisation"]["lines_joined"] > 0


@needs_pdftotext
def test_running_heads_and_page_numbers_are_dropped_and_listed_exactly(built):
    text = built["full_text"]
    assert HEADER not in text
    dropped = {(d["text"], d["kind"]): d["pages"] for d in built["meta"]["headers_footers_dropped"]}
    assert dropped[(HEADER, "running_text")] == [1, 2, 3, 4]
    for n in (1, 2, 3, 4):
        assert dropped[(str(100 + n), "page_number")] == [n]
    assert built["meta"]["headers_footers_dropped_lines"] == 8
    assert "4.2 cm" in text, "a number inside the text is never taken for a page number"


@needs_pdftotext
def test_a_line_on_too_few_pages_is_not_a_running_head():
    pages = [[f"Only on page one, a title line {n}" if n == 1 else
              "Ordinary text that is long enough to count as a body line here.",
              "More ordinary body text that is long enough to count as a line."]
             for n in (1, 2, 3)]
    pages[0].append("Two pages carry this footer line")
    pages[1].append("Two pages carry this footer line")
    s = build_from_pdf(make_pdf(pages), "FEW")
    assert s["full_text"].count("Two pages carry this footer line") == 2
    assert all(d["text"] != "Two pages carry this footer line"
               for d in s["meta"]["headers_footers_dropped"])


# --- the reference list ----------------------------------------------------------------------

@needs_pdftotext
def test_the_reference_list_is_cut_but_a_following_appendix_is_kept(built):
    text = built["full_text"]
    assert "Smith, A. (2001)" not in text and "9.9 cm" not in text, \
        "a value must not ground against the bibliography"
    assert "Appendix A. Extra tables" in text
    assert "recorded by hand in the lab notebook" in text, "the appendix runs on past the page"
    cut = built["meta"]["references_cut"]
    assert cut["cut"] is True and cut["heading"] == "References" and cut["page"] == 3
    assert isinstance(cut["raw_offset"], int)
    assert cut["appendix_kept"]["heading"] == "Appendix A. Extra tables"
    titles = [s["title"] for s in built["sections"]]
    assert "Appendix A. Extra tables" in titles and "References" not in titles


@needs_pdftotext
def test_without_an_appendix_everything_after_the_references_is_cut():
    pages = [["1. Introduction",
              "A body line that is long enough to be read as an ordinary line.",
              "Another body line that is long enough to be an ordinary line too.",
              "Bibliography",
              "Doe, J. (1999). A reference entry. A Journal 1, 1-9.",
              "Author biography text that follows the reference list here."]]
    s = build_from_pdf(make_pdf(pages), "NOAPP")
    assert "Doe, J." not in s["full_text"] and "biography" not in s["full_text"]
    assert s["meta"]["references_cut"]["appendix_kept"] is None


@needs_pdftotext
def test_with_no_reference_heading_nothing_is_cut_and_it_says_so():
    s = build_from_pdf(make_pdf([["A short note with no reference list at all, and so",
                                  "nothing to cut: the text is kept exactly as it stands."]]),
                       "NOREF")
    cut = s["meta"]["references_cut"]
    assert cut["cut"] is False and "nothing was cut" in cut["why"]
    assert "nothing to cut" in s["full_text"]


# --- sections --------------------------------------------------------------------------------

@needs_pdftotext
def test_numbered_and_named_headings_become_sections(built):
    titles = [(s["title"], s["depth"]) for s in built["sections"]]
    assert ("Abstract", 0) in titles and ("1. Introduction", 0) in titles
    assert ("2. Methods", 0) in titles and ("4. Discussion", 0) in titles
    assert built["meta"]["heading_rules"].get("numbered") == 4
    m = next(s for s in built["sections"] if s["title"] == "2. Methods")
    r = next(s for s in built["sections"] if s["title"] == "3. Results")
    assert m["end"] == r["start"], "a section ends where the next one at its depth begins"


def test_section_numbers_must_run_in_sequence():
    """A list item `1. The first` deep inside section 3 is not a new section 1."""
    assert store._successor(None, (1,)) and store._successor((1,), (2,))
    assert store._successor((2,), (2, 1)) and store._successor((2, 1), (3,))
    assert store._successor((2, 3), (2, 4)) and store._successor((3,), (5,))
    assert not store._successor((3,), (1,)) and not store._successor((3,), (9,))
    assert not store._successor((2, 1), (2, 1))


@needs_pdftotext
def test_without_numbering_short_title_case_lines_are_headings():
    body = "This is a body line that is long enough to be read as ordinary text."
    s = build_from_pdf(make_pdf([["Introduction", body, body + " End.", "Study Design",
                                  body, body + " End.", "What We Found", body]]), "STYLED")
    titles = [x["title"] for x in s["sections"]]
    assert titles == ["Introduction", "Study Design", "What We Found"]
    assert s["meta"]["heading_rules"] == {"named": 1, "styled": 2}


# --- reading order: flagged, not guessed -------------------------------------------------------

def test_reading_back_into_the_left_column_is_flagged():
    """Column by column: every left line of a band, then every right line. Going back left after
    the right column, with no full-width line between, is the misorder measured on real pages."""
    W = 600.0
    left = lambda y: (40, y, 280, y + 9)                                # noqa: E731
    right = lambda y: (320, y, 560, y + 9)                              # noqa: E731
    wide = lambda y: (40, y, 560, y + 9)                                # noqa: E731
    in_order = [left(100), left(112), right(100), right(112)]
    assert store._column_returns(W, in_order) == 0
    assert store._column_returns(W, [right(100), left(100), right(112)]) == 1
    assert store._column_returns(W, [left(100), right(100), wide(300), left(320)]) == 0, \
        "a full-width line starts a new band"
    interleaved = [left(100), right(100), left(112), right(112), left(124), right(124)]
    assert store._column_hops(W, interleaved) == 3, "each line read beside its neighbour"
    assert store._column_hops(W, in_order) == 0


def test_only_an_exact_repeat_is_collapsed():
    block = [f"line {i}" for i in range(5)]
    kept, copies = store._collapse_exact_repeats(["head"] + block * 3 + ["tail"])
    assert copies == 3 and kept == ["head"] + block + ["tail"]
    altered = block * 2 + block[:4] + ["line 4, but different"]
    kept, copies = store._collapse_exact_repeats(altered)
    assert copies == 2, "only the copies that are identical are collapsed"
    assert kept[-5:] == block[:4] + ["line 4, but different"]
    assert store._collapse_exact_repeats(block) == (block, 1)


def _overprinted(copies_text):
    items = []
    for c, lines in enumerate(copies_text):
        for i, t in enumerate(lines):
            items.append((72 + 3 * c, 700 - 12 * i + c, t))
    return make_pdf([items])


BLOCK = [f"Line {w} of the block that a publisher drew several times over." for w in
         ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")]


@needs_pdftotext
def test_a_page_drawn_several_times_over_is_recovered_when_the_copies_are_identical():
    """Some publishers' first pages draw the same text several times at slightly shifted
    positions, and pdftotext interleaves the copies word by word (measured on a real
    IFAC-PapersOnLine PDF: six copies). Identical copies are collapsed, losslessly."""
    s = build_from_pdf(_overprinted([BLOCK] * 3), "OVER")
    flag = s["meta"]["overprinted_pages"]
    assert flag and flag[0]["recovered"] is True and flag[0]["identical_copies"] == 3
    for line in BLOCK:
        assert s["full_text"].count(line) == 1


@needs_pdftotext
def test_copies_that_differ_are_flagged_and_left_as_emitted():
    changed = BLOCK[:-1] + ["Line ten of the block, which this copy prints differently."]
    s = build_from_pdf(_overprinted([BLOCK, changed, BLOCK]), "OVER2")
    flag = s["meta"]["overprinted_pages"]
    assert flag and flag[0]["recovered"] is False and "guess" in flag[0]["why"]


# --- refusals --------------------------------------------------------------------------------

def test_missing_pdftotext_raises_naming_what_to_install(monkeypatch):
    monkeypatch.setenv(store.PDFTOTEXT_ENV, "lit2db-no-such-pdftotext-binary")
    with pytest.raises(PdfToTextMissing, match="poppler"):
        build_from_pdf(b"%PDF-1.4", "X")


@needs_pdftotext
def test_a_pdf_with_no_text_layer_is_refused_not_stored_empty():
    with pytest.raises(ValueError, match="no text"):
        build_from_pdf(make_pdf([[]]), "SCANNED")


@needs_pdftotext
def test_a_file_that_is_not_a_pdf_is_refused():
    with pytest.raises(RuntimeError, match="pdftotext failed"):
        build_from_pdf(b"this is not a pdf", "JUNK")


# --- add_pdf: a project source like any other ---------------------------------------------------

JATS = b"""<article><front><article-meta>
<article-title>Widgets in the wild</article-title>
<abstract><p>We report widget A.</p></abstract>
</article-meta></front>
<body><sec><title>Results</title><p>Widget A was isolated from site 3.</p></sec></body>
</article>"""


class FakeEPMC:
    """Europe PMC replaced by one open-access hit, as in test_project."""

    def __init__(self, doi="10.9/open"):
        self.doi = doi

    def __call__(self, url):
        if "/search?" in url:
            hit = {"pmid": "1", "pmcid": "PMC111", "doi": self.doi, "title": "open",
                   "pubYear": "2022", "isOpenAccess": "Y", "inEPMC": "Y", "license": "cc by",
                   "journalInfo": {"journal": {"title": "J"}}, "pubTypeList": {"pubType": []}}
            return json.dumps({"hitCount": 1, "nextCursorMark": "*",
                               "resultList": {"result": [hit]}}).encode()
        if url.endswith("/fullTextXML"):
            return JATS
        raise AssertionError(f"unexpected request {url}")


def _fetch(root, doi="10.9/open"):
    return project.fetch(root, limit=5, allow_abstracts=False, get=FakeEPMC(doi),
                         query="widget", pause=0)


@pytest.fixture
def pdf_file(tmp_path, pdf):
    f = tmp_path / "paper.pdf"
    f.write_bytes(pdf)
    return f


DOI_DIR = store_dirname("DOI:10.1234/widget.1")


@needs_pdftotext
def test_an_added_pdf_is_a_readable_source(tmp_path, pdf_file, pdf):
    out = project.add_pdf(tmp_path, pdf_file, doi="10.1234/widget.1", title="Widget Patterns",
                          how_found="cited by the review")
    assert out["source_id"] == "DOI:10.1234/widget.1" and out["outcome"] == "stored"
    assert project.readable_sources(tmp_path) == [DOI_DIR]
    meta = json.loads((project.paths(tmp_path)["sources"] / DOI_DIR / "meta.json").read_text())
    assert meta["acquisition_route"] == "local_pdf"
    assert meta["source_text_scope"] == "full_text_pdf"
    assert meta["how_found"] == "cited by the review"
    entry = json.loads(project.paths(tmp_path)["search"].read_text())["papers"][0]
    assert entry["route"] == "local_pdf" and entry["outcome"] == "full_text"
    assert entry["pdf_sha256"] == hashlib.sha256(pdf).hexdigest()
    assert entry["how_found"] == "cited by the review"


@needs_pdftotext
def test_the_source_id_is_the_doi_when_there_is_one(tmp_path, pdf_file):
    a = project.add_pdf(tmp_path, pdf_file, doi="https://doi.org/10.1234/Widget.1",
                        source_id="ignored")
    assert a["source_id"] == "DOI:10.1234/widget.1", "resolver prefix and case folded"
    b = project.add_pdf(tmp_path / "other", pdf_file, source_id="LOCAL_widget_notes")
    assert b["source_id"] == "LOCAL_widget_notes"
    with pytest.raises(ValueError, match="DOI or a source_id"):
        project.add_pdf(tmp_path / "third", pdf_file)


@needs_pdftotext
def test_fetch_then_add_pdf_keeps_both(tmp_path, pdf_file):
    _fetch(tmp_path)
    project.add_pdf(tmp_path, pdf_file, doi="10.1234/widget.1")
    assert project.readable_sources(tmp_path) == ["PMC111", DOI_DIR]


@needs_pdftotext
def test_add_pdf_then_fetch_keeps_the_pdf(tmp_path, pdf_file):
    """`fetch` rewrites the corpus record from scratch on every run. A PDF added before it must
    survive, and must not be counted as one of the query's hits."""
    project.add_pdf(tmp_path, pdf_file, doi="10.1234/widget.1", how_found="cited by the review")
    out = _fetch(tmp_path)
    assert project.readable_sources(tmp_path) == ["PMC111", DOI_DIR]
    assert out["counts"]["examined"] == 1 and out["counts"]["full_text"] == 1
    assert out["counts"]["local_pdf"] == 1
    rec = json.loads(project.paths(tmp_path)["search"].read_text())
    kept = [e for e in rec["papers"] if e.get("route") == "local_pdf"]
    assert len(kept) == 1 and kept[0]["how_found"] == "cited by the review"
    _fetch(tmp_path)                                    # and again: still exactly one
    assert project.readable_sources(tmp_path) == ["PMC111", DOI_DIR]


@needs_pdftotext
def test_jats_is_read_in_preference_to_a_pdf_of_the_same_paper(tmp_path, pdf_file):
    _fetch(tmp_path, doi="10.1234/widget.1")
    out = project.add_pdf(tmp_path, pdf_file, doi="10.1234/WIDGET.1")
    assert "JATS" in out["note"]
    assert project.readable_sources(tmp_path) == ["PMC111"], "one paper, one source"


@needs_pdftotext
def test_re_adding_is_a_no_op_and_a_different_pdf_is_refused(tmp_path, pdf_file):
    project.add_pdf(tmp_path, pdf_file, doi="10.1234/widget.1")
    again = project.add_pdf(tmp_path, pdf_file, doi="10.1234/widget.1")
    assert again["outcome"] == "already stored"
    rec = json.loads(project.paths(tmp_path)["search"].read_text())
    assert len(rec["papers"]) == 1
    other = tmp_path / "other.pdf"
    other.write_bytes(make_pdf([["A different paper that happens to be filed under the same",
                                 "identifier, which would move every recorded offset."]]))
    with pytest.raises(ValueError, match="move every offset"):
        project.add_pdf(tmp_path, other, doi="10.1234/widget.1")


@needs_pdftotext
def test_a_manifest_adds_many_and_reports_each_failure(tmp_path, pdf_file):
    m = tmp_path / "pdfs.json"
    m.write_text(json.dumps([
        {"pdf": "paper.pdf", "doi": "10.1234/widget.1", "title": "Widget Patterns",
         "how_found": "cited by the review"},
        {"pdf": "missing.pdf", "source_id": "LOCAL_gone"},
    ]))
    out = project.add_pdfs(tmp_path, m)
    assert [a["source_id"] for a in out["added"]] == ["DOI:10.1234/widget.1"]
    assert len(out["failed"]) == 1 and "missing.pdf" in out["failed"][0]["error"]
    assert project.readable_sources(tmp_path) == [DOI_DIR]


@needs_pdftotext
def test_the_cli_adds_a_pdf(tmp_path, pdf_file):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "project.py"), "--root",
                        str(tmp_path), "add-pdf", "--pdf", str(pdf_file), "--doi",
                        "10.1234/widget.1", "--how-found", "cited by the review"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] and out["source_id"] == "DOI:10.1234/widget.1"
    bad = subprocess.run([sys.executable, str(ROOT / "scripts" / "project.py"), "--root",
                          str(tmp_path), "add-pdf", "--pdf", str(pdf_file)],
                         capture_output=True, text=True)
    assert bad.returncode != 0 and "DOI or a source_id" in bad.stdout


@needs_pdftotext
def test_prisma_counts_added_pdfs_as_identified_by_other_methods(tmp_path, pdf_file):
    project.add_pdf(tmp_path, pdf_file, doi="10.1234/widget.1")
    counts = project.prisma(tmp_path)["counts"]
    assert counts["identified_other_methods"] == 1 and counts["assessed_other_methods"] == 1
    assert counts["identified"] is None and counts["assessed_full_text"] is None, \
        "no query ran, so the database arm reads not counted, never 0"
    _fetch(tmp_path)
    counts = project.prisma(tmp_path)["counts"]
    assert counts["identified"] == 1 and counts["assessed_full_text"] == 1
    assert counts["identified_other_methods"] == 1

