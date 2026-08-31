"""The grid, the footnotes, and the binding between them.

Pins a defect found 2026-08-30 by running `build_from_jats` over a table whose footnotes are
bound EXPLICITLY in markup — the easy case. The flat store fused the marker into the value
(`12.4a`, `0.8b`) and dropped the footnote text entirely, so `0.8` survived while "below the
limit of detection; upper bound reported" did not. A value that is wrong while looking right
is the failure the verification thesis exists to catch, and here the store was producing it.
"""
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from lit2db.store import build_from_jats
from lit2db.tables import parse_tables


def _doc(table_xml: str) -> str:
    return f"""<article><front><article-meta><title-group>
    <article-title>T</article-title></title-group>
    <abstract><p>a</p></abstract></article-meta></front>
    <body><sec><title>Results</title>{table_xml}</sec></body></article>"""


MARKUP_BOUND = _doc("""<table-wrap id="T1"><label>Table 1</label>
 <caption><p>Kinetic parameters.</p></caption>
 <table><thead><tr><th>Variant</th><th>kcat (s-1)</th></tr></thead>
 <tbody>
  <tr><td>WT</td><td>12.4<sup><xref ref-type="table-fn" rid="TF1">a</xref></sup></td></tr>
  <tr><td>M1</td><td>0.8<sup><xref ref-type="table-fn" rid="TF2">b</xref></sup></td></tr>
 </tbody></table>
 <table-wrap-foot>
  <fn id="TF1"><label>a</label><p>p &lt; 0.05 versus wild type.</p></fn>
  <fn id="TF2"><label>b</label><p>Below the limit of detection; upper bound reported.</p></fn>
 </table-wrap-foot></table-wrap>""")

# The majority case. Two independent studies put 67.9% / 75.0% of footnoted tables here:
# the marker is typography the reader's eye joins, with no xref and no rid to follow.
TYPOGRAPHIC = _doc("""<table-wrap id="T2"><label>Table 2</label>
 <table><thead><tr><th>Sample</th><th>Yield (mg/L)</th></tr></thead>
 <tbody>
  <tr><td>A</td><td>110</td></tr>
  <tr><td>B</td><td>4.2b</td></tr>
 </tbody></table>
 <table-wrap-foot>
  <fn><label>b</label><p>Single replicate.</p></fn>
 </table-wrap-foot></table-wrap>""")


def test_the_flat_store_still_loses_the_footnote_and_fuses_the_marker():
    """The finding itself, pinned. If `store.py` ever learns tables, this test says so."""
    text = build_from_jats(MARKUP_BOUND, source_id="X")["full_text"]
    assert "12.4a" in text, "the marker used to fuse into the value; that is why this exists"
    assert "Below the limit of detection" not in text, (
        "the flat store dropped table footnote text — if it no longer does, the sidecar's "
        "justification changed and this test should be rewritten, not deleted")


def test_the_sidecar_recovers_the_value_and_its_meaning():
    t = parse_tables(MARKUP_BOUND)[0]
    censored = t.cell_at(2, 1)
    assert censored.text == "0.8b", "the cell as printed is kept for quote matching"
    assert censored.value_text == "0.8", "the token an extractor parses has the marker removed"
    notes = t.footnotes_for(2, 1)
    assert [n.text for n in notes] == ["Below the limit of detection; upper bound reported."]
    assert censored.marker_source == "markup"


def test_unit_assignment_has_a_binding_the_text_stream_cannot_express():
    t = parse_tables(MARKUP_BOUND)[0]
    assert t.column_headers(1) == ["kcat (s-1)"]
    assert t.column_headers(0) == ["Variant"]


def test_a_typographic_marker_binds_when_a_matching_footnote_label_exists():
    """No xref, no rid. This is the 67.9–75% case, and markup alone never reaches it."""
    t = parse_tables(TYPOGRAPHIC)[0]
    cell = t.cell_at(2, 1)
    assert cell.value_text == "4.2"
    assert cell.marker_source == "typographic"
    assert [n.text for n in t.footnotes_for(2, 1)] == ["Single replicate."]


def test_a_trailing_letter_that_is_not_a_footnote_label_is_left_alone():
    """The over-strip guard. Dropping a trailing letter blindly turns `5 h` into `5` and
    `WT a` into `WT`, which is a worse error than the one being fixed."""
    doc = _doc("""<table-wrap id="T3"><table><tbody>
      <tr><td>Incubation</td><td>5 h</td></tr></tbody></table>
      <table-wrap-foot><fn><label>a</label><p>note</p></fn></table-wrap-foot></table-wrap>""")
    t = parse_tables(doc)[0]
    cell = t.cell_at(0, 1)
    assert cell.value_text == "5 h", "'h' is not one of this table's footnote labels"
    assert cell.marker_source == "none"


def test_a_spanning_header_still_reaches_the_cells_beneath_it():
    """Units routinely live in a group header spanning several columns, with the quantity
    name in the row under it. Without colspan expansion the columns shift and every cell
    inherits the wrong header, which is unit assignment failing silently."""
    doc = _doc("""<table-wrap id="T4"><table>
      <thead>
       <tr><th>Strain</th><th colspan="2">Titre (mg/L)</th></tr>
       <tr><th></th><th>day 3</th><th>day 7</th></tr>
      </thead>
      <tbody><tr><td>S1</td><td>12</td><td>30</td></tr></tbody></table></table-wrap>""")
    t = parse_tables(doc)[0]
    assert t.n_cols == 3
    assert t.cell_at(2, 2).text == "30"
    assert t.column_headers(2) == ["Titre (mg/L)", "day 7"], (
        "the spanning header must reach column 2, outermost first")


def test_footnotes_are_found_outside_table_wrap_foot():
    """Publishers put `<fn>` directly under `<table-wrap>` too. A footnote missed is a value
    whose meaning is silently lost, so the search is over the whole wrap.

    Uses `9.5c` rather than `9c`: a bare integer with a letter is the ambiguous label form and
    would be reported rather than bound, which is a different behaviour from the one under test.
    """
    doc = _doc("""<table-wrap id="T5"><table><tbody>
      <tr><td>x</td><td>9.5c</td></tr></tbody></table>
      <fn id="F"><label>c</label><p>Estimated.</p></fn></table-wrap>""")
    t = parse_tables(doc)[0]
    assert [n.text for n in t.footnotes_for(0, 1)] == ["Estimated."]


def test_a_table_with_no_footnotes_produces_no_bindings_and_no_stripping():
    doc = _doc("""<table-wrap id="T6"><table><tbody>
      <tr><td>a</td><td>1.0</td></tr></tbody></table></table-wrap>""")
    t = parse_tables(doc)[0]
    assert t.footnotes == []
    assert t.cell_at(0, 1).value_text == "1.0"
    assert t.footnotes_for(0, 1) == []


def test_every_table_wrap_in_the_document_is_parsed():
    doc = _doc(MARKUP_BOUND.split("<sec>")[1].split("</sec>")[0].replace("<title>Results</title>", "")
               + """<table-wrap id="T9"><table><tbody><tr><td>z</td></tr></tbody></table></table-wrap>""")
    ids = [t.table_id for t in parse_tables(doc)]
    assert ids == ["T1", "T9"]


# --- The over-strip false positives, taken from real papers ---------------------------------
# Every typographic "hit" in the first 70-table corpus run was a false positive of one shape:
# a table declaring a footnote labelled `a`, and the detector amputating the last letter of any
# cell that ended in one. These are the actual cells, not invented ones.

import pytest  # noqa: E402


@pytest.mark.parametrize("cell_text", [
    "P. aeruginosa",                          # ACS PMC13316986 tbl3
    "Klebsiella oxytoca",                     # ASM PMC13274388 T1
    "gactctagaggatccccagcccgcactaagca",       # ASM PMC13274388 T2 — a DNA sequence
])
def test_a_word_ending_in_a_footnote_letter_is_not_amputated(cell_text):
    doc = _doc(f"""<table-wrap id="TX"><table><tbody>
      <tr><td>x</td><td>{cell_text}</td></tr></tbody></table>
      <table-wrap-foot><fn><label>a</label><p>A note.</p></fn></table-wrap-foot></table-wrap>""")
    t = parse_tables(doc)[0]
    cell = t.cell_at(0, 1)
    assert cell.value_text == cell_text, "corrupting real text is worse than missing a footnote"
    assert cell.marker_source == "none"


@pytest.mark.parametrize("cell_text,expected,why", [
    ("12.4a",   "12.4", "stem ends in a digit, so the letter is a marker"),
    ("WT a",    "WT",   "whitespace means the writer separated it"),
    ("3.1*",    "3.1",  "a symbol marker never occurs inside a word"),
])
def test_a_marker_that_really_is_one_still_binds(cell_text, expected, why):
    label = cell_text[-1]
    doc = _doc(f"""<table-wrap id="TY"><table><tbody>
      <tr><td>x</td><td>{cell_text}</td></tr></tbody></table>
      <table-wrap-foot><fn><label>{label}</label><p>A note.</p></fn></table-wrap-foot></table-wrap>""")
    t = parse_tables(doc)[0]
    cell = t.cell_at(0, 1)
    assert cell.value_text == expected, why
    assert cell.marker_source == "typographic"


def test_an_unseparated_digit_marker_is_refused_as_ambiguous():
    """`12.41` is indistinguishable from a value. Guessing there silently changes a number."""
    doc = _doc("""<table-wrap id="TZ"><table><tbody>
      <tr><td>x</td><td>12.41</td></tr></tbody></table>
      <table-wrap-foot><fn><label>1</label><p>A note.</p></fn></table-wrap-foot></table-wrap>""")
    t = parse_tables(doc)[0]
    assert t.cell_at(0, 1).value_text == "12.41"
    assert t.cell_at(0, 1).marker_source == "none"


# --- Ambiguity is a third outcome, not a coin flip (D-175) -----------------------------------

@pytest.mark.parametrize("cell_text", ["4a", "12a", "7b"])
def test_a_small_integer_with_a_letter_is_reported_ambiguous_not_bound(cell_text):
    """`4a` is a ring-fusion carbon in NMR and also "4, see footnote a". Structure cannot tell,
    and the scaffold may not hold the chemistry that would (D-175). So it reports rather than
    guesses: the value stays intact and no binding is asserted."""
    doc = _doc(f"""<table-wrap id="TA"><table>
      <thead><tr><th>Pos</th></tr></thead>
      <tbody><tr><td>{cell_text}</td></tr></tbody></table>
      <table-wrap-foot><fn><label>{cell_text[-1]}</label><p>Peaks are overlapped.</p></fn>
      </table-wrap-foot></table-wrap>""")
    t = parse_tables(doc)[0]
    cell = t.cell_at(1, 0)
    assert cell.marker_source == "ambiguous"
    assert cell.value_text == cell_text, "an unratified guess must not corrupt the value"
    assert t.footnotes_for(1, 0) == [], "no binding may be asserted"
    assert [f.text for f in t.candidate_footnotes(1, 0)] == ["Peaks are overlapped."]


def test_a_decimal_stem_is_not_ambiguous_and_still_binds():
    """`12.4a` is not a label form — a decimal point settles it structurally."""
    doc = _doc("""<table-wrap id="TB"><table><tbody>
      <tr><td>x</td><td>12.4a</td></tr></tbody></table>
      <table-wrap-foot><fn><label>a</label><p>note</p></fn></table-wrap-foot></table-wrap>""")
    t = parse_tables(doc)[0]
    assert t.cell_at(0, 1).marker_source == "typographic"
    assert t.cell_at(0, 1).value_text == "12.4"


def test_a_separated_marker_on_an_integer_is_not_ambiguous():
    """`120 a` was written apart by the author, so it is not the `4a` label form."""
    doc = _doc("""<table-wrap id="TC"><table><tbody>
      <tr><td>x</td><td>12 a</td></tr></tbody></table>
      <table-wrap-foot><fn><label>a</label><p>note</p></fn></table-wrap-foot></table-wrap>""")
    t = parse_tables(doc)[0]
    assert t.cell_at(0, 1).marker_source == "typographic"
    assert t.cell_at(0, 1).value_text == "12"
