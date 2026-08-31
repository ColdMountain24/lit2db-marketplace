"""Table structure preserved beside the text stream, because the stream cannot carry it.

`store.py` flattens a `<table-wrap>` into caption plus tab-joined rows, and says why: the
extractor greps the store, a row that reads as a line is one a quote can be lifted from
verbatim, and a parsed grid would need a second coordinate system. That reasoning is right
FOR GROUNDING and wrong for everything that has to know what a value means.

Measured on a minimal JATS table whose footnotes are bound EXPLICITLY in markup -- the easy
case, where nothing typographic is required of the reader:

    Variant  kcat (s-1)  KM (µM)
    WT       12.4a       3.1
    M1       0.8b        44

Three things happen, and the third is the one that matters.

1. The header text survives (`kcat (s-1)`) with no binding to any cell, so unit assignment
   has to be re-inferred from column position that the stream no longer records.
2. The footnote marker survives FUSED INTO THE VALUE: `12.4a`, `0.8b`. A reader parsing a
   number off that line gets a corrupted token, and a lenient parser gets `12.4` while
   silently discarding the fact that a marker was ever there.
3. **The footnote text is dropped outright.** `_emit_table` reads `label`, `caption` and
   `tr` and never descends into `<table-wrap-foot>`, so `p < 0.05` and `Below the limit of
   detection; upper bound reported` do not enter the store at all.

For `0.8b` that means the store keeps `0.8` and loses "this is an upper bound." The value is
not missing, it is WRONG while looking right, which is the same failure the verification
thesis exists to catch, one layer further down.

This module does not replace the text stream and does not touch it. It writes a sidecar, so
`full.txt` remains the single coordinate system for quotes (D-038) while the grid, the
footnote definitions and the marker-to-cell bindings live where they can be scored.

Domain-blind: nothing here knows what a terpene, a polymer or a kinetic constant is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional
import xml.etree.ElementTree as ET

__all__ = ["Footnote", "Cell", "Table", "parse_tables", "tables_from_jats"]

# A trailing run of footnote markers on an otherwise numeric or textual cell. Deliberately
# NOT used to find markers when markup already declares them -- markup wins every time, and
# this is only the fallback for the typographic case (see `Cell.marker_source`).
_TRAILING_MARKERS = re.compile(r"[\s]*([a-zA-Z]|[*†‡§¶#]{1,3}|\d{1,2})$")


def _tag(el) -> str:
    """Local tag name, namespace-insensitive. Mirrors `store._tag` deliberately."""
    t = el.tag
    return t.rsplit("}", 1)[-1] if isinstance(t, str) and "}" in t else t


def _flat(el) -> str:
    """Inline text of one element, whitespace-collapsed."""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


@dataclass
class Footnote:
    """One `<fn>` from a `<table-wrap-foot>`.

    `label` is the marker a reader looks for ("a", "*", "1"); `fn_id` is what the markup
    uses to point at it. Either can be absent in real papers, which is why both are kept.
    """
    fn_id: Optional[str]
    label: Optional[str]
    text: str


@dataclass
class Cell:
    """One grid position after colspan/rowspan expansion.

    `text` is what the cell reads as, markers included, so it can still be matched against
    the text stream. `value_text` is the same with trailing markers removed -- the token an
    extractor should actually parse. Keeping BOTH is the point: the difference between them
    is exactly what the flat store threw away.
    """
    text: str
    value_text: str
    row: int
    col: int
    is_header: bool = False
    footnote_ids: list[str] = field(default_factory=list)
    footnote_labels: list[str] = field(default_factory=list)
    marker_source: str = "none"          # "markup" | "typographic" | "none"
    spans: tuple[int, int] = (1, 1)      # (rowspan, colspan) as declared

    def covers(self, col: int) -> bool:
        """Whether this cell occupies `col`, spans included.

        A spanning cell is stored ONCE, at its leftmost position. Anything looking a cell up
        by column has to ask this rather than compare `col`, or every column but the first
        falls outside the header that visibly sits above it.
        """
        return self.col <= col < self.col + self.spans[1]


@dataclass
class Table:
    """One `<table-wrap>`: the grid, its footnotes, and the bindings between them."""
    table_id: Optional[str]
    label: str
    caption: str
    cells: list[Cell]
    footnotes: list[Footnote]
    n_rows: int = 0
    n_cols: int = 0

    # -- the two things no existing benchmark scores -------------------------------------

    def column_headers(self, col: int) -> list[str]:
        """Header texts sitting above `col`, outermost first.

        This is the binding unit assignment needs and the flat stream cannot express. It
        returns the header CHAIN rather than one header, because units routinely live in a
        spanning group header with the quantity name in the row beneath it.

        Matched on the span a header COVERS rather than its declared column, and ordered by
        row so the outermost group header comes first.
        """
        return [c.text for c in sorted(self.cells, key=lambda c: c.row)
                if c.is_header and c.covers(col) and c.text]

    def footnotes_for(self, row: int, col: int) -> list[Footnote]:
        """The footnotes attached to one cell, resolved from marker to definition.

        Returns nothing for an AMBIGUOUS cell. `4a` may be the label of a position rather than
        the value 4 with footnote `a`, and asserting the binding would put a claim into the
        database that no human ratified. Use `candidate_footnotes` to see what it might be.
        """
        cell = self.cell_at(row, col)
        if cell is None or cell.marker_source == "ambiguous":
            return []
        by_id = {f.fn_id: f for f in self.footnotes if f.fn_id}
        by_label = {f.label: f for f in self.footnotes if f.label}
        out, seen = [], set()
        for fid in cell.footnote_ids:
            fn = by_id.get(fid)
            if fn is not None and id(fn) not in seen:
                out.append(fn); seen.add(id(fn))
        for lab in cell.footnote_labels:
            fn = by_label.get(lab)
            if fn is not None and id(fn) not in seen:
                out.append(fn); seen.add(id(fn))
        return out

    def candidate_footnotes(self, row: int, col: int) -> list[Footnote]:
        """What an ambiguous cell's marker WOULD resolve to, for a human to rule on."""
        cell = self.cell_at(row, col)
        if cell is None or cell.marker_source != "ambiguous":
            return []
        by_label = {f.label: f for f in self.footnotes if f.label}
        return [by_label[l] for l in cell.footnote_labels if l in by_label]

    def cell_at(self, row: int, col: int) -> Optional[Cell]:
        """The cell at a grid position: the one declared there, else the one spanning it."""
        for c in self.cells:
            if c.row == row and c.col == col:
                return c
        for c in self.cells:
            if c.row <= row < c.row + c.spans[0] and c.covers(col):
                return c
        return None

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_footnotes(wrap) -> list[Footnote]:
    """Every `<fn>` under the table-wrap, wherever it sits.

    Searched over the whole wrap rather than only `<table-wrap-foot>`: publishers also put
    them directly under `<table-wrap>`, and a footnote missed is a value whose meaning is
    silently lost, which is the failure this module exists to stop.
    """
    out = []
    for fn in wrap.iter():
        if _tag(fn) != "fn":
            continue
        label_el = next((c for c in fn if _tag(c) == "label"), None)
        label = _flat(label_el) if label_el is not None else None
        body = " ".join(_flat(c) for c in fn if _tag(c) != "label").strip()
        if not body:                       # label-only or bare-text <fn>
            body = _flat(fn)
            if label and body.startswith(label):
                body = body[len(label):].strip()
        out.append(Footnote(fn_id=fn.get("id"), label=label, text=body))
    return out


def _markers_in(cell_el) -> tuple[list[str], list[str]]:
    """Footnote ids and labels declared by markup inside one cell.

    Markup is authoritative. `<xref ref-type="table-fn">` is the common form; a bare
    `<sup>` holding only a marker is accepted too, because plenty of publishers emit that
    without an xref and the binding is still explicit enough to trust.
    """
    ids, labels = [], []
    for el in cell_el.iter():
        t = _tag(el)
        if t == "xref":
            rid = el.get("rid")
            if rid:
                ids.extend(rid.split())
            txt = _flat(el)
            if txt:
                labels.append(txt)
    return ids, labels


_SYMBOL_MARKERS = set("*†‡§¶#")


def _plausible_typographic(text: str, marker: str) -> bool:
    """Whether a trailing character that HAPPENS to match a footnote label really is one.

    Added after the first real-corpus run, where the label check alone was not nearly enough.
    Every typographic hit in a 70-table sample was a false positive of the same shape: the
    table declared a footnote labelled `a`, and the detector then amputated the last letter of
    any cell ending in one --

        'P. aeruginosa'                    -> 'P. aeruginos'
        'Klebsiella oxytoca'               -> 'Klebsiella oxytoc'
        'gactctagaggatccccagcccgcactaagca' -> ...'aagc'   (a DNA sequence)

    Corrupting a species name and a nucleotide sequence is far worse than missing a footnote,
    so this errs toward leaving text alone. Three rules, in order:

    1. A symbol marker (*, †, ‡, §, ¶, #) never occurs inside a word, so it always binds.
    2. Whitespace before the marker means the writer separated it, so it binds.
    3. Otherwise it binds only if the stem does NOT end in a letter -- `12.4a` binds because
       `12.4` ends in a digit; `aeruginosa` does not because `aeruginos` ends in `s`.

    A bare DIGIT marker with no separator is refused outright: `12.41` is indistinguishable
    from a value, and guessing there silently changes a number.
    """
    stem_raw = text[: -len(marker)]
    stem = stem_raw.rstrip()
    if not stem:
        return False
    if marker in _SYMBOL_MARKERS or all(ch in _SYMBOL_MARKERS for ch in marker):
        return True
    if stem_raw != stem:                       # the writer put a space before it
        return True
    if marker.isdigit():                       # unseparated digit: ambiguous with the value
        return False
    return not stem[-1].isalpha()


_BARE_INTEGER = re.compile(r"^\d{1,2}$")


def _is_ambiguous_label_form(text: str, marker: str) -> bool:
    """Whether `<small integer><letter>` is a VALUE with a marker or a LABEL in its own right.

    `4a`, `7b`, `12a` are a label form used across the sciences -- ring-fusion carbons in NMR,
    compound numbers, figure panels -- and they are indistinguishable, by structure alone, from
    the number 4 carrying footnote `a`. Measured on 663 tables: of 66 typographic hits in ACS
    journals, 40 had a bare-integer stem under headers like `Pos` and `ROESY`, and one read
    `8, 14a` under a ROESY header, which is a correlation list. ASM had 0 of 46 in this shape.

    Deciding it needs to know what the column MEANS, which is domain substance the scaffold is
    forbidden to hold (D-175). So this reports ambiguity rather than resolving it: the value is
    left uncorrupted, the candidate footnote is kept visible, and a human ratifies. Same
    reasoning as refusing an unseparated digit marker -- when structure cannot tell, do not
    guess.
    """
    stem_raw = text[: -len(marker)]
    if stem_raw != stem_raw.rstrip():          # separated -> not this form
        return False
    return bool(marker.isalpha() and _BARE_INTEGER.match(stem_raw.strip()))


def _strip_trailing_markers(text: str, labels: list[str]) -> str:
    """Remove the marker from the token an extractor will parse.

    Only strips a marker that is actually one of this table's footnote labels. A blanket
    "drop a trailing letter" rule would turn the unit off `5 h` and the state off `WT a`,
    which is a worse error than the one being fixed.
    """
    out = text
    for lab in sorted({l for l in labels if l}, key=len, reverse=True):
        if out.endswith(lab):
            out = out[: -len(lab)].strip()
    return out.strip()


def parse_tables(xml) -> list[Table]:
    """Every `<table-wrap>` in a JATS document, as grids with footnote bindings.

    Accepts raw XML or an already-parsed element, so `store.build_from_jats` can hand over
    the tree it has already namespace-stripped rather than paying to parse the document a
    second time.
    """
    if isinstance(xml, (str, bytes)):
        root = ET.fromstring(xml.encode() if isinstance(xml, str) else xml)
    else:
        root = xml
    return [_parse_one(w) for w in root.iter() if _tag(w) == "table-wrap"]


def _parse_one(wrap) -> Table:
    label_el = next((c for c in wrap if _tag(c) == "label"), None)
    caption_el = next((c for c in wrap if _tag(c) == "caption"), None)
    footnotes = _parse_footnotes(wrap)
    known_labels = [f.label for f in footnotes if f.label]

    cells: list[Cell] = []
    occupied: dict[tuple[int, int], bool] = {}
    row_i = -1
    for tr in wrap.iter():
        if _tag(tr) != "tr":
            continue
        row_i += 1
        col_i = 0
        for cell_el in tr:
            t = _tag(cell_el)
            if t not in ("td", "th"):
                continue
            while occupied.get((row_i, col_i)):
                col_i += 1
            try:
                rowspan = max(1, int(cell_el.get("rowspan", 1)))
                colspan = max(1, int(cell_el.get("colspan", 1)))
            except ValueError:            # publishers do emit rowspan="" and rowspan="all"
                rowspan = colspan = 1

            text = _flat(cell_el)
            ids, labels = _markers_in(cell_el)
            source = "markup" if (ids or labels) else "none"
            if not labels and known_labels:
                m = _TRAILING_MARKERS.search(text)
                if (m and m.group(1) in known_labels
                        and _plausible_typographic(text, m.group(1))):
                    labels = [m.group(1)]
                    source = ("ambiguous"
                              if _is_ambiguous_label_form(text, m.group(1))
                              else "typographic")
            value_text = (text if source == "ambiguous"
                          else _strip_trailing_markers(text, labels))

            for dr in range(rowspan):
                for dc in range(colspan):
                    occupied[(row_i + dr, col_i + dc)] = True
            cells.append(Cell(text=text, value_text=value_text, row=row_i, col=col_i,
                              is_header=(t == "th"), footnote_ids=ids,
                              footnote_labels=labels, marker_source=source,
                              spans=(rowspan, colspan)))
            col_i += colspan

    n_rows = (max((c.row for c in cells), default=-1)) + 1
    n_cols = (max((c.col + c.spans[1] for c in cells), default=0))
    return Table(table_id=wrap.get("id"),
                 label=_flat(label_el) if label_el is not None else "",
                 caption=_flat(caption_el) if caption_el is not None else "",
                 cells=cells, footnotes=footnotes, n_rows=n_rows, n_cols=n_cols)


def tables_from_jats(xml) -> list[dict]:
    """Serializable form, for writing `tables.json` beside `full.txt`."""
    return [t.to_dict() for t in parse_tables(xml)]
