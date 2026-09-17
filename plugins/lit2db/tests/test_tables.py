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
from lit2db.tables import parse_tables, recovered_label_share, split_label_share


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


# --- The pre-registered primary outcome (invariance-experiment.md §3) -------------------------
# Fixed 2026-09-01 before any data was analysed. These tests exist so the definition cannot drift
# between runs of the study -- an outcome that is re-typed each time it is computed is not
# pre-registered, whatever a document says.

from lit2db.tables import (  # noqa: E402
    ambiguity_rate, footnote_resolution_rate, header_chain_coverage, typographic_share,
)


def test_a_paper_with_no_footnoted_table_is_None_and_never_zero():
    """The distinction the whole outcome turns on. A paper that declared no footnotes has not
    failed to bind them, and scoring an absent thing as failure is the denominator error that
    made an earlier measurement in this project answer the wrong question (D-177)."""
    doc = _doc("""<table-wrap id="TN"><table><tbody>
      <tr><td>x</td><td>1.0</td></tr></tbody></table></table-wrap>""")
    assert footnote_resolution_rate(parse_tables(doc)) is None


def test_every_declared_footnote_reaching_a_cell_is_one():
    t = parse_tables(MARKUP_BOUND)
    assert footnote_resolution_rate(t) == 1.0


def test_a_declared_footnote_reaching_no_cell_lowers_the_rate():
    """Half the point of the measure: a table can declare a footnote that binds to nothing, and
    that is the residue the invariance experiment is about."""
    doc = _doc("""<table-wrap id="TH"><table><tbody>
      <tr><td>x</td><td>12.4<sup><xref ref-type="table-fn" rid="F1">a</xref></sup></td></tr>
      </tbody></table>
      <table-wrap-foot>
        <fn id="F1"><label>a</label><p>bound</p></fn>
        <fn id="F2"><label>z</label><p>orphan — no cell references it</p></fn>
      </table-wrap-foot></table-wrap>""")
    assert footnote_resolution_rate(parse_tables(doc)) == 0.5


def test_an_ambiguous_binding_does_NOT_count_as_resolved():
    """`4a` may be a position label rather than the value 4 with footnote a (D-175). Counting an
    unratified guess as a success would inflate exactly the number H1 turns on."""
    doc = _doc("""<table-wrap id="TA2"><table>
      <thead><tr><th>Pos</th></tr></thead>
      <tbody><tr><td>4a</td></tr></tbody></table>
      <table-wrap-foot><fn><label>a</label><p>Peaks overlap.</p></fn></table-wrap-foot></table-wrap>""")
    t = parse_tables(doc)
    assert t[0].cell_at(1, 0).marker_source == "ambiguous"
    assert footnote_resolution_rate(t) == 0.0, "declared one, resolved none"


def test_tables_with_no_footnotes_do_not_dilute_the_denominator():
    """Only footnoted tables contribute. A paper with one bound footnote and nine plain tables
    scores 1.0, not 0.1 -- the outcome is about footnotes, not about tables."""
    plain = "".join(f"""<table-wrap id="P{i}"><table><tbody>
        <tr><td>a</td><td>{i}</td></tr></tbody></table></table-wrap>""" for i in range(9))
    doc = _doc(MARKUP_BOUND.split("<sec>")[1].split("</sec>")[0]
               .replace("<title>Results</title>", "") + plain)
    assert footnote_resolution_rate(parse_tables(doc)) == 1.0


@pytest.mark.parametrize("fn,expected", [
    (header_chain_coverage, 1.0),     # every body cell sits under a header in MARKUP_BOUND
    (ambiguity_rate, 0.0),            # both markers are markup-declared
    (typographic_share, 0.0),         # ...so none is typographic
])
def test_the_secondary_measures_are_computable_and_labelled_secondary(fn, expected):
    assert fn(parse_tables(MARKUP_BOUND)) == expected


@pytest.mark.parametrize("fn", [header_chain_coverage, ambiguity_rate, typographic_share])
def test_every_secondary_is_None_rather_than_zero_when_it_has_no_denominator(fn):
    doc = _doc("""<table-wrap id="TE"><table><tbody><tr><th>h</th></tr></tbody></table></table-wrap>""")
    assert fn(parse_tables(doc)) is None


# --- Recovering a marker the publisher never tagged --------------------------------------------
# D-194 measured the hole first: 21–29% of footnotes in PLOS, MDPI and PLoS One carry no
# `<label>`. The marker is still printed at the front of the footnote, where a human reads it;
# only the machine-readable tag is missing, and without it the footnote reaches no cell at all.
# Recovery is licensed by the grid and by nothing else — a value in the same table has to wear
# the marker — because the failure being avoided is not a missed footnote, it is a marker
# invented onto a value that never had one.

RECOVERABLE = _doc("""<table-wrap id="T3"><label>Table 3</label>
 <table><thead><tr><th>Variant</th><th>kcat (s-1)</th></tr></thead>
 <tbody>
  <tr><td>WT</td><td>12.4a</td></tr>
  <tr><td>M1</td><td>0.8</td></tr>
 </tbody></table>
 <table-wrap-foot>
  <fn id="TF9"><p>a Mean of three replicates.</p></fn>
 </table-wrap-foot></table-wrap>""")

# The cell is the letter A itself — a sample name, not a marked value. Nothing in this table
# wears `A` as a marker, so the footnote's opening capital stays part of its sentence.
UNCORROBORATED = _doc("""<table-wrap id="T4"><label>Table 4</label>
 <table><thead><tr><th>Sample</th><th>Yield (mg/L)</th></tr></thead>
 <tbody><tr><td>A</td><td>110</td></tr></tbody></table>
 <table-wrap-foot>
  <fn><p>A total of 12 isolates were screened.</p></fn>
 </table-wrap-foot></table-wrap>""")


def test_an_untagged_marker_is_recovered_when_a_value_wears_it():
    t = parse_tables(RECOVERABLE)[0]
    fn = t.footnotes[0]
    assert fn.label == "a", "the marker was printed, just never tagged"
    assert fn.label_source == "text", "and the record has to say we inferred it"
    assert fn.text == "Mean of three replicates.", "the marker leaves the sentence with it"
    assert [n.text for n in t.footnotes_for(1, 1)] == ["Mean of three replicates."]
    assert t.cell_at(1, 1).value_text == "12.4", "the value is now clean of its marker too"


def test_recovery_is_refused_when_no_value_wears_the_marker():
    """The sentence that opens with a capital is the whole reason for the corroboration rule."""
    t = parse_tables(UNCORROBORATED)[0]
    fn = t.footnotes[0]
    assert fn.label is None, "no cell wears `A`, so nothing licenses reading it as a marker"
    assert fn.label_source == "none"
    assert fn.text == "A total of 12 isolates were screened.", "the prose is left intact"
    assert t.cell_at(1, 0).value_text == "A", "and the sample name keeps its name"


def test_a_symbol_marker_is_recovered_the_same_way():
    t = parse_tables(_doc("""<table-wrap id="T5"><table><tbody>
      <tr><td>WT</td><td>12.4*</td></tr></tbody></table>
     <table-wrap-foot><fn><p>*p &lt; 0.05 versus wild type.</p></fn>
     </table-wrap-foot></table-wrap>"""))[0]
    fn = t.footnotes[0]
    assert (fn.label, fn.label_source) == ("*", "text")
    assert fn.text == "p < 0.05 versus wild type."
    assert [n.text for n in t.footnotes_for(0, 1)] == ["p < 0.05 versus wild type."]


def test_two_footnotes_claiming_one_marker_are_both_refused():
    """A cell wearing `a` would have no single referent, and picking one would be a coin toss."""
    t = parse_tables(_doc("""<table-wrap id="T6"><table><tbody>
      <tr><td>WT</td><td>12.4a</td></tr></tbody></table>
     <table-wrap-foot>
      <fn><p>a Mean of three replicates.</p></fn>
      <fn><p>a Assayed at 30 °C.</p></fn>
     </table-wrap-foot></table-wrap>"""))[0]
    assert [f.label for f in t.footnotes] == [None, None]
    assert t.footnotes_for(0, 1) == []


def test_an_ambiguous_cell_corroborates_nothing():
    """`4a` may be a ring-fusion carbon rather than the value 4 with marker `a` (D-175).

    If structure cannot tell what the cell is, the cell cannot be the evidence that licenses a
    binding — otherwise the ambiguity we refuse to resolve in one place reappears as a fact in
    another.
    """
    t = parse_tables(_doc("""<table-wrap id="T7"><table>
     <thead><tr><th>Pos</th><th>ROESY</th></tr></thead>
     <tbody><tr><td>1</td><td>4a</td></tr></tbody></table>
     <table-wrap-foot><fn><p>a Ring fusion carbon.</p></fn>
     </table-wrap-foot></table-wrap>"""))[0]
    assert t.footnotes[0].label is None
    assert t.footnotes[0].label_source == "none"


def test_a_declared_label_is_still_recorded_as_declared():
    for fn in parse_tables(MARKUP_BOUND)[0].footnotes:
        assert fn.label_source == "markup", "recovery must not blur what the publisher tagged"


def test_the_share_of_recovered_labels_is_reportable_and_absent_when_there_is_nothing():
    assert recovered_label_share(parse_tables(RECOVERABLE)) == 1.0
    assert recovered_label_share(parse_tables(MARKUP_BOUND)) == 0.0
    assert recovered_label_share(parse_tables(UNCORROBORATED)) is None, (
        "no footnote carries a marker at all — that is not a recovery rate of zero")


# --- One `<fn>` carrying several definitions ---------------------------------------------------
# Publishers concatenate a whole table's notes into a single element. The parser kept the first and
# everything after it was unreachable, so cells wearing the later markers bound to nothing while the
# paper had said in plain words what they meant. Measured before the fix over the 2026-09-04 corpus:
# 24 of 2971 footnotes, 45 definitions lost, 114 of 234 orphan symbol markers ours rather than the
# papers'. A split is taken only where a value wears the marker — D-195's corroboration, unchanged.

CROWDED_NUMBERED = _doc("""<table-wrap id="T8"><label>Table 8</label>
 <table><tbody>
  <tr><td>Other 1</td><td>44</td></tr>
  <tr><td>Induction therapy 2</td><td>17</td></tr>
 </tbody></table>
 <table-wrap-foot>
  <fn><p>1 Other diseases included reflux nephropathy, Alport syndrome, etc.
   2 Induction therapy used in combination with methylprednisolone.</p></fn>
 </table-wrap-foot></table-wrap>""")

CROWDED_SYMBOLS = _doc("""<table-wrap id="T9"><label>Table 9</label>
 <table><tbody>
  <tr><td>N*</td><td>Proportion**</td></tr>
 </tbody></table>
 <table-wrap-foot>
  <fn><p>*the number of annotated features; **the proportion of annotated reads.</p></fn>
 </table-wrap-foot></table-wrap>""")


def test_a_footnote_holding_two_numbered_notes_becomes_two_footnotes():
    t = parse_tables(CROWDED_NUMBERED)[0]
    assert [(f.label, f.label_source) for f in t.footnotes] == [("1", "text"), ("2", "split")]
    assert t.footnotes[0].text == "Other diseases included reflux nephropathy, Alport syndrome, etc."
    assert [n.label for n in t.footnotes_for(0, 0)] == ["1"]
    assert [n.label for n in t.footnotes_for(1, 0)] == ["2"], (
        "the second note was unreachable before the split, and its cell wears the marker")


def test_a_symbol_series_splits_without_needing_a_sentence_boundary():
    """`**` follows a letter in `...features; **the proportion` — a symbol needs no boundary,
    because unlike a digit it can never be part of the word in front of it."""
    t = parse_tables(CROWDED_SYMBOLS)[0]
    assert [(f.label, f.label_source) for f in t.footnotes] == [("*", "text"), ("**", "split")]
    assert [n.label for n in t.footnotes_for(0, 1)] == ["**"]


def test_prose_that_merely_counts_is_not_split():
    """`heated to 2 degrees` is a sentence; `etc. 2 Immunosuppressive` is a second footnote.
    The sentence boundary is the only thing that separates them, so a digit requires one."""
    t = parse_tables(_doc("""<table-wrap id="TA"><table><tbody>
      <tr><td>Yield 1</td><td>4.2</td></tr>
      <tr><td>Run 2</td><td>4.4</td></tr></tbody></table>
     <table-wrap-foot><fn><p>1 Sample was heated to 2 degrees above ambient.</p></fn>
     </table-wrap-foot></table-wrap>"""))[0]
    assert [f.label for f in t.footnotes] == ["1"], "no split: nothing opens a second definition"
    assert t.footnotes[0].text == "Sample was heated to 2 degrees above ambient."


def test_an_interior_definition_no_value_wears_is_left_merged():
    """D-195 again: a split adds a label, and a new label is a new way for a cell to acquire a
    footnote it never had. Over-reaching text is a display problem; an invented binding is a
    wrong value, so the corroboration requirement does not relax for splitting."""
    t = parse_tables(_doc("""<table-wrap id="TB"><table><tbody>
      <tr><td>Other 1</td><td>44</td></tr></tbody></table>
     <table-wrap-foot><fn><p>1 Other diseases included Alport syndrome.
      2 Immunosuppressive treatment at discharge.</p></fn>
     </table-wrap-foot></table-wrap>"""))[0]
    assert [f.label for f in t.footnotes] == ["1"], "no cell wears `2`, so nothing is cut out"
    assert "Immunosuppressive" in t.footnotes[0].text, "the definition is kept, not discarded"


def test_the_split_share_is_reportable_beside_the_recovered_share():
    assert split_label_share(parse_tables(CROWDED_SYMBOLS)) == 0.5
    assert split_label_share(parse_tables(MARKUP_BOUND)) == 0.0
    assert split_label_share(parse_tables(UNCORROBORATED)) is None


def test_a_general_note_that_defines_a_symbol_partway_through_is_split():
    """88 of the 98 orphan markers left after the series pass were this shape: a footnote with no
    marker of its own whose prose defines `*` somewhere inside. There is no series to walk from,
    so the anchor is the symbol's own form plus a value wearing it."""
    t = parse_tables(_doc("""<table-wrap id="TC"><table><tbody>
      <tr><td>Age</td><td>0.008*</td></tr></tbody></table>
     <table-wrap-foot><fn><p>Values are medians with interquartile range.
      *Significant at the 5% level.</p></fn>
     </table-wrap-foot></table-wrap>"""))[0]
    labels = [(f.label, f.label_source) for f in t.footnotes]
    assert (None, "none") in labels, "the general note keeps its own text and stays unlabelled"
    assert ("*", "split") in labels
    assert [n.text for n in t.footnotes_for(0, 1)] == ["Significant at the 5% level."]


def test_a_letter_inside_an_unlabelled_note_is_NOT_split_out():
    """A symbol cannot be part of the word in front of it; a letter can. With no series to anchor
    on, a letter is indistinguishable from the sentence around it, so this case is refused rather
    than guessed — the same line D-195 draws."""
    t = parse_tables(_doc("""<table-wrap id="TD"><table><tbody>
      <tr><td>Yield</td><td>4.2a</td></tr></tbody></table>
     <table-wrap-foot><fn><p>Values are means. a Single replicate.</p></fn>
     </table-wrap-foot></table-wrap>"""))[0]
    assert [f.label for f in t.footnotes] == [None], "no marker of its own, and `a` is not a symbol"
