"""Stage-1 offset-anchored store — the artifact that DEFINES char offsets.

Every extracted value carries a verbatim quote and a char offset, and the offset is
load-bearing: it is what disambiguates two occurrences of the same entity in one document.
But a source has no intrinsic offsets. A PDF has none at all, and an XML file's byte
positions move if anyone re-serializes it. **The store is the coordinate system**, and
`full.txt` is its authority: an offset means "index into this exact file", nothing else.

That makes this module small but load-bearing. If the store is rebuilt differently, every
offset recorded against the old one silently points somewhere else — so the text is assembled
deterministically and normalized BEFORE offsets are taken, never after.

## What goes in, and one deliberate exclusion

Title, abstract, body sections, table cells, and figure captions all go in — tables
especially, because in this literature the measurements live there rather than in prose.

**The reference list is excluded.** It is the one part of a paper that is dense with other
people's claims, so leaving it in lets a value "ground" against a citation in the
bibliography — the quote check would pass against text the paper never asserted. Appendices
and supplementary sections in `<back>` are kept; only `<ref-list>` is dropped.

Deliberately STDLIB-ONLY, same constraint as `lit2db.gate`, `accounting`, and `ensemble`.
Domain-INVARIANT: nothing here knows what a terpene is.

## Three builders, one coordinate contract

`build_from_jats` (full text from XML), `build_from_abstract` (C9: only the abstract could be
had) and `build_from_pdf` (full text from a PDF, read with poppler's `pdftotext`, the one
non-Python dependency, called as a subprocess). Every one returns the same shape and stamps
`meta["source_text_scope"]`, so what a record was verified against is reported by the store.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import statistics
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from functools import lru_cache
from typing import Optional

from .tables import tables_from_jats

# Blocks that are handled explicitly rather than recursed into blindly.
_SKIP = {"ref-list", "back-matter-refs"}
_PARA_GAP = "\n\n"


def _tag(el) -> str:
    """Local tag name, namespace-insensitive."""
    t = el.tag
    return t.rsplit("}", 1)[-1] if isinstance(t, str) and "}" in t else str(t)


def _strip_namespaces(root):
    """Rewrite every tag to its local name, in place, immediately after parsing.

    JATS from Europe PMC is unnamespaced today, but a namespaced variant would make every
    `find("body")` below miss — yielding an EMPTY store rather than an error, which three
    stages later is indistinguishable from a paper that contained nothing. Normalizing once
    here is why the rest of this module can use plain tag names.
    """
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.rsplit("}", 1)[-1]
    return root


def _flat(el) -> str:
    """Inline text of one block, with markup flattened.

    `itertext()` is right for inline content — italics, sub/superscripts, and citation
    markers should read as the running text a human sees. It is WRONG for containers like
    <sec>, which is why the walker below never calls this on one.
    """
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


class _Builder:
    """Accumulates text while recording where each piece landed."""

    def __init__(self):
        self.parts: list[str] = []
        self.pos = 0
        self.sections: list[dict] = []

    def write(self, text: str) -> tuple[int, int]:
        if not text:
            return self.pos, self.pos
        start = self.pos
        chunk = text + _PARA_GAP
        self.parts.append(chunk)
        self.pos += len(chunk)
        return start, start + len(text)      # end excludes the separator

    @property
    def text(self) -> str:
        return "".join(self.parts)


def _emit_table(el, b: _Builder) -> None:
    """Caption plus one line per row, cells tab-joined.

    Kept as text rather than parsed into a grid on purpose: the extractor greps this store,
    and a row that reads as a line is one a quote can be lifted from verbatim. A parsed grid
    would need its own offset scheme, and then there would be two coordinate systems.
    """
    label = _flat(el.find("label")) if el.find("label") is not None else ""
    caption = _flat(el.find("caption")) if el.find("caption") is not None else ""
    head = " ".join(x for x in (label, caption) if x)
    if head:
        b.write(head)
    for row in el.iter("tr"):
        cells = [_flat(c) for c in row if _tag(c) in ("td", "th")]
        line = "\t".join(c for c in cells if c)
        if line:
            b.write(line)


def _emit(el, b: _Builder, depth: int = 0) -> None:
    tag = _tag(el)
    if tag in _SKIP:
        return
    if tag == "sec":
        title_el = el.find("title")
        title = _flat(title_el) if title_el is not None else ""
        start = b.pos
        if title:
            b.write(title)
        for child in el:
            if child is not title_el:
                _emit(child, b, depth + 1)
        b.sections.append({"title": title or "(untitled)", "start": start,
                           "end": b.pos, "depth": depth})
    elif tag == "p":
        b.write(_flat(el))
    elif tag == "table-wrap":
        _emit_table(el, b)
    elif tag in ("fig", "boxed-text", "disp-quote", "disp-formula"):
        b.write(_flat(el))
    elif tag in ("list", "def-list"):
        for item in el:
            b.write(_flat(item))
    else:
        for child in el:
            _emit(child, b, depth)


def build_from_jats(xml: str | bytes, source_id: str, meta: Optional[dict] = None) -> dict:
    """Parse JATS full-text XML into an offset-anchored store.

    Returns `{source_id, full_text, sections, tables, meta, stats}`. Raises on unparseable XML
    rather than returning an empty store — a source that silently yields no text would look,
    three stages later, exactly like a paper that simply contained nothing extractable.

    `tables` is a SIDECAR, not a second coordinate system. `full.txt` remains the sole offset
    authority for quotes; the sidecar carries what the flat stream provably cannot — the grid,
    the footnote definitions, and which cell each marker binds to. Measured 2026-08-30 on a
    table whose footnotes were bound explicitly in markup: the stream fused the marker into
    the value (`12.4a`) and dropped the footnote text entirely, so a censored `0.8` survived
    while "below the limit of detection" did not. See `tables.py`.
    """
    root = _strip_namespaces(ET.fromstring(xml.encode() if isinstance(xml, str) else xml))
    b = _Builder()

    front = root.find(".//front")
    if front is not None:
        title_el = front.find(".//article-title")
        if title_el is not None:
            b.write(_flat(title_el))
        for abst in front.iter("abstract"):
            start = b.pos
            _emit(abst, b, 0)
            b.sections.append({"title": "Abstract", "start": start, "end": b.pos, "depth": 0})

    for part in ("body", "back"):
        el = root.find(f".//{part}")
        if el is not None:
            _emit(el, b, 0)

    if not b.text.strip():
        raise ValueError(f"{source_id}: JATS parsed but produced no text — refusing to "
                         f"emit an empty store, which downstream cannot distinguish from "
                         f"a source that genuinely had nothing to extract")

    b.sections.sort(key=lambda s: (s["start"], s["depth"]))
    return {
        "source_id": source_id,
        "full_text": b.text,
        "sections": b.sections,
        "tables": tables_from_jats(root),
        "meta": {**dict(meta or {}), "source_text_scope": "full_text"},
        "stats": {"chars": len(b.text), "sections": len(b.sections),
                  "est_tokens": round(len(b.text) / 4)},
    }


def build_from_abstract(title: str, abstract: str, source_id: str,
                        meta: Optional[dict] = None) -> dict:
    """Build a store from an abstract record, for sources whose full text cannot be had.

    Same coordinate contract as `build_from_jats`: `full_text` is the authority and offsets
    index into it. The difference is declared, not hidden — `meta["source_text_scope"]` is
    `abstract_only`, and that is what the record's `source_text_scope` field is meant to be
    read from rather than guessed at by the extractor.

    **This is not truncation, and the distinction is the whole point.** D-038 exists because a
    pilot silently read 26% of each paper; the fix was to record how much of a source was read,
    since a value grounded against a fraction is verified against a different document. Here
    the abstract IS the whole document available, so the retained fraction is 1.0 and honest.
    The two questions stay separate: retention asks "did you read all of what you had",
    `source_text_scope` asks "what did you have". A record that conflated them would claim a
    complete read of a paper nobody could open.
    """
    b = _Builder()
    if title and title.strip():
        b.write(title.strip())
    if abstract and abstract.strip():
        # `write` returns bounds that exclude the trailing paragraph gap, so the section ends
        # where the abstract ends -- an offset landing on the separator belongs to no section.
        start, end = b.write(abstract.strip())
        b.sections.append({"title": "Abstract", "start": start, "end": end, "depth": 0})

    if not b.text.strip():
        raise ValueError(f"{source_id}: no title or abstract text — refusing to emit an empty "
                         f"store, which downstream cannot distinguish from a source that "
                         f"genuinely had nothing to extract")

    return {
        "source_id": source_id,
        "full_text": b.text,
        "sections": b.sections,
        "meta": {**dict(meta or {}), "source_text_scope": "abstract_only"},
        "stats": {"chars": len(b.text), "sections": len(b.sections),
                  "est_tokens": round(len(b.text) / 4)},
    }


def store_dirname(source_id: str) -> str:
    """The directory name a store is written under, from its `source_id`.

    Extracted from `write_store` because it is the store's ADDRESSING and a second copy of it
    would be a second answer to "where does this source live". Real ids need it: a DOI arrives
    as `DOI_10.1002/anie.201506541` and the slash must become `_` for both the writer and
    every reader to land in the same place. Same argument as `quote_at` — a reader that
    disagreed with the writer about this would look in a directory that does not exist and
    report the paper missing.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(source_id))


def write_store(store: dict, root_dir: str | pathlib.Path) -> dict:
    """Write the store to `<root_dir>/<source_id>/` and return the paths.

    Four files, because the extractor reaches them with Read/Grep/Glob rather than through
    an API: `full.txt` (the offset authority), `sections.json`, `meta.json`, and `tables.json`
    (the structure sidecar, written only when the source carried tables).
    """
    d = pathlib.Path(root_dir) / store_dirname(store["source_id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "full.txt").write_text(store["full_text"], encoding="utf-8")
    (d / "sections.json").write_text(json.dumps(store["sections"], indent=2))
    (d / "meta.json").write_text(json.dumps(
        {**store["meta"], "source_id": store["source_id"], **store["stats"]}, indent=2))
    paths = {"dir": str(d), "full_text": str(d / "full.txt"),
             "sections": str(d / "sections.json"), "meta": str(d / "meta.json")}
    if store.get("tables"):
        (d / "tables.json").write_text(json.dumps(store["tables"], indent=2))
        paths["tables"] = str(d / "tables.json")
    return paths


def quote_at(store_or_text, start: int, end: int) -> str:
    """The exact span at an offset pair — the round-trip a grounding check depends on.

    Provided so nothing downstream has to re-derive slicing rules. If this and the extractor
    ever disagree about what an offset means, every grounded quote in the database is
    suspect, so there is exactly one implementation of it.
    """
    text = store_or_text["full_text"] if isinstance(store_or_text, dict) else store_or_text
    return text[start:end]


def find_spans(store_or_text, needle: str, limit: int = 50) -> list[dict]:
    """Every occurrence of `needle`, as offset pairs into the store.

    Returns ALL matches up to `limit`, never just the first: a repeated entity is exactly the
    case the offset exists to disambiguate, so collapsing them here would defeat the point.
    """
    text = store_or_text["full_text"] if isinstance(store_or_text, dict) else store_or_text
    out, at = [], text.find(needle)
    while at != -1 and len(out) < limit:
        out.append({"start": at, "end": at + len(needle)})
        at = text.find(needle, at + 1)
    return out


def section_of(store: dict, offset: int) -> Optional[str]:
    """The most specific section containing `offset` — provenance needs the section label,
    and the deepest match is the informative one ('2.3 Isolation', not 'Methods')."""
    best = None
    for s in store["sections"]:
        if s["start"] <= offset < s["end"]:
            if best is None or s["depth"] > best["depth"]:
                best = s
    return best["title"] if best else None


# =============================================================================================
# PDF: the fallback when a source has no JATS anywhere
# =============================================================================================
#
# A PDF has no offsets of its own, so the text pdftotext emits is normalised ONCE, here, and
# `full.txt` is what offsets index into, exactly as for JATS. Every normalisation is COUNTED in
# meta: a store that silently rewrote its source would look, to a reviewer comparing a quote with
# the paper, like an extractor that misquoted.
#
# What is changed, and nothing else:
#   - hard-wrapped lines inside a paragraph are joined with one space;
#   - a line-end hyphen is removed only where the next line starts lowercase (and the document
#     never prints the joined word while printing the hyphenated one elsewhere); otherwise kept;
#   - running headers, footers and page numbers that repeat across pages are dropped, and the
#     exact strings dropped are recorded;
#   - the reference list is cut (mirroring the JATS builder's `<ref-list>` exclusion), keeping
#     an appendix that follows it.
# Typography, ligatures and symbols are left exactly as pdftotext emits them, because extractors
# copy quotes verbatim from `full.txt` and a "fixed" character is a quote that no longer matches.

PDFTOTEXT_ENV = "LIT2DB_PDFTOTEXT"        # override the binary (tests use it to simulate absence)

_INSTALL_HINT = ("Install poppler, which provides it: macOS `brew install poppler`, "
                 "Debian/Ubuntu `apt install poppler-utils`, conda "
                 "`conda install -c conda-forge poppler`.")


class PdfToTextMissing(RuntimeError):
    """pdftotext is not installed. Raised rather than returning an empty store."""


def _pdftotext_bin() -> str:
    name = os.environ.get(PDFTOTEXT_ENV) or "pdftotext"
    found = shutil.which(name)
    if not found:
        raise PdfToTextMissing(f"pdftotext was not found (looked for {name!r} on PATH). lit2db "
                               f"reads PDFs with it. {_INSTALL_HINT}")
    return found


@lru_cache(maxsize=8)
def _pdftotext_caps(exe: str) -> dict:
    """Version and the two options this module uses that older poppler lacks."""
    v = subprocess.run([exe, "-v"], capture_output=True, text=True)
    m = re.search(r"version\s+(\S+)", (v.stdout or "") + (v.stderr or ""))
    h = subprocess.run([exe, "-h"], capture_output=True, text=True)
    usage = (h.stdout or "") + (h.stderr or "")
    return {"version": m.group(1) if m else "unknown",
            "remove_hyphens": "-remove-hyphens" in usage, "tsv": "-tsv" in usage}


def _run_pdftotext(exe: str, args: list, path: str, source_id: str) -> str:
    r = subprocess.run([exe, "-enc", "UTF-8", *args, path, "-"], capture_output=True)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip()[:400]
        raise RuntimeError(f"{source_id}: pdftotext failed (exit {r.returncode}): {err}")
    return r.stdout.decode("utf-8", "replace")


# --- page geometry: is the reading order trustworthy? --------------------------------------------

def _page_geometry(tsv: str) -> dict:
    """Per page: its width and its text lines' boxes, in pdftotext's reading order."""
    pages: dict = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        try:
            level, page = int(row["level"]), int(row["page_num"])
            x, y = float(row["left"]), float(row["top"])
            w, h = float(row["width"]), float(row["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if level == 1:
            pages.setdefault(page, {"width": w, "lines": []})["width"] = w
        elif level == 4:
            pages.setdefault(page, {"width": 0.0, "lines": []})["lines"].append(
                (x, y, x + w, y + h))
    return pages


def _column_hops(width: float, lines: list) -> int:
    """Consecutive lines (in reading order) on opposite halves of the page at the same height.

    Read column by column, a two-column page hops sideways at the same height almost never; read
    interleaved, it hops on nearly every line. Lines narrower than a fifth of the page are left
    out, because math fragments and table cells sit side by side legitimately.
    """
    if width <= 0:
        return 0
    mid, wide = width / 2, 0.2 * width
    side = lambda b: "L" if b[2] <= mid else ("R" if b[0] >= mid else "X")   # noqa: E731
    hops = 0
    for a, b in zip(lines, lines[1:]):
        if (a[2] - a[0]) < wide or (b[2] - b[0]) < wide:
            continue
        if {side(a), side(b)} == {"L", "R"} and \
                abs(a[1] - b[1]) < 0.6 * max(a[3] - a[1], b[3] - b[1]):
            hops += 1
    return hops


def _column_returns(width: float, lines: list) -> int:
    """How often the reading order goes back to the left column after reading the right one,
    with no full-width line in between.

    Column by column means every left-column line of a band is read before any right-column
    line of it; a full-width line (a heading across both columns, a wide caption) starts a new
    band. Measured 2026-10-05 on three journal PDFs: pdftotext put the top lines of the right
    column before the left column on pages carrying floats or display equations, and once read
    a figure caption early. The text inside each block was intact, so this is FLAGGED, and joins
    across block boundaries on such a page are withheld, rather than the blocks being re-ordered
    by a guess. A full-width figure with no full-width text can trip it; that costs a flag only.
    """
    if width <= 0:
        return 0
    mid, wide = width / 2, 0.3 * width
    returns, seen_right = 0, False
    for b in lines:
        if (b[2] - b[0]) < wide:
            continue
        if b[0] < mid < b[2]:
            seen_right = False                      # full width: a new band
        elif b[0] >= mid:
            seen_right = True
        elif seen_right:
            returns += 1
            seen_right = False
    return returns


def _overlapping_lines(lines: list) -> int:
    """How many lines share more than half their box with another line.

    Normal text never does; text drawn several times at nearly the same place (some publishers'
    first pages) does on almost every line, and pdftotext then interleaves the copies word by
    word.
    """
    order = sorted(range(len(lines)), key=lambda i: lines[i][1])
    hit = set()
    for ai in range(len(order)):
        a = lines[order[ai]]
        for bi in range(ai + 1, len(order)):
            b = lines[order[bi]]
            if b[1] >= a[3]:
                break
            ix = min(a[2], b[2]) - max(a[0], b[0])
            iy = min(a[3], b[3]) - max(a[1], b[1])
            if ix > 0 and iy > 0:
                small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
                if small > 0 and ix * iy > 0.5 * small:
                    hit.update((order[ai], order[bi]))
    return len(hit)


def _collapse_exact_repeats(lines: list, min_block: int = 3, min_cover: float = 0.5):
    """`(kept_lines, copies)` when a run of lines repeats back to back, verbatim, over at least
    `min_cover` of the page; `(lines, 1)` otherwise. Only an EXACT repeat is collapsed, so
    nothing is lost: the copies removed are character-for-character the copy kept."""
    n = len(lines)
    nxt: dict = {}
    first_after = [None] * n
    for i in range(n - 1, -1, -1):
        first_after[i] = nxt.get(lines[i])
        nxt[lines[i]] = i
    best = None                                  # (lines removed, -start, start, period, copies)
    for s in range(n):
        j = first_after[s]
        if j is None or j - s < min_block:
            continue
        p, k = j - s, 1
        while s + (k + 1) * p <= n and lines[s + k * p: s + (k + 1) * p] == lines[s: s + p]:
            k += 1
        if k >= 2 and k * p >= min_cover * n:
            cand = ((k - 1) * p, -s, s, p, k)
            if best is None or cand > best:
                best = cand
    if best is None:
        return lines, 1
    _, _, s, p, k = best
    return lines[:s] + lines[s: s + p] + lines[s + k * p:], k


# --- running headers, footers, page numbers -------------------------------------------------

_ZONE = 3          # lines at the top and the bottom of a page where running heads sit


def _most_pages(pages_with: set, n_pages: int) -> bool:
    """On most pages, or on most pages of one parity (running heads that alternate between
    left- and right-hand pages, e.g. authors on even pages and short title on odd)."""
    if len(pages_with) < 3:
        return False
    if len(pages_with) > n_pages / 2:
        return True
    for parity in (0, 1):
        same = [p for p in range(1, n_pages + 1) if p % 2 == parity]
        if same and len([p for p in pages_with if p % 2 == parity]) > len(same) / 2:
            return True
    return False


def _drop_running_lines(pages: list) -> list:
    """Remove running heads/footers and page numbers in place. Returns what was dropped.

    Two rules, both requiring repetition across pages, so a line that occurs once is never
    touched:
      - text with at least three letters in the top or bottom `_ZONE` lines whose shape (digits
        folded) recurs on most pages;
      - a line that is only an integer equal to its page index plus ONE offset shared by most
        pages (wherever pdftotext placed it; right-hand page numbers often land mid-page).
    """
    n = len(pages)
    if n < 3:
        return []
    shape = lambda s: re.sub(r"\d+", "#", s.strip())                       # noqa: E731

    def zone(page):
        idx = [i for i, ln in enumerate(page) if ln["text"].strip()]
        return set(idx[:_ZONE] + idx[-_ZONE:])

    seen: dict = {}
    for pno, page in enumerate(pages, 1):
        for i in zone(page):
            key = shape(page[i]["text"])
            if len(re.findall(r"[^\W\d_]", key)) >= 3:
                seen.setdefault(key, set()).add(pno)
    running = {k for k, ps in seen.items() if _most_pages(ps, n)}

    offsets: dict = {}
    for pno, page in enumerate(pages, 1):
        for ln in page:
            s = ln["text"].strip()
            if re.fullmatch(r"\d{1,5}", s):
                offsets.setdefault(int(s) - pno, set()).add(pno)
    offset = None
    if offsets:
        off, ps = max(offsets.items(), key=lambda kv: (len(kv[1]), -abs(kv[0])))
        if _most_pages(ps, n):
            offset = off

    dropped: dict = {}
    for pno, page in enumerate(pages, 1):
        z = zone(page)
        kill = {i for i in z if shape(page[i]["text"]) in running}
        if offset is not None:
            want = str(pno + offset)
            cands = [i for i, ln in enumerate(page) if ln["text"].strip() == want]
            if cands:
                in_zone = [i for i in cands if i in z]
                kill.add(in_zone[0] if in_zone else
                         min(cands, key=lambda i: min(i, len(page) - 1 - i)))
        for i in sorted(kill):
            text = page[i]["text"].strip()
            kind = "page_number" if re.fullmatch(r"\d{1,5}", text) else "running_text"
            dropped.setdefault((text, kind), []).append(pno)
        pages[pno - 1] = [ln for i, ln in enumerate(page) if i not in kill]
    return [{"text": t, "kind": k, "pages": ps} for (t, k), ps in dropped.items()]


# --- headings -------------------------------------------------------------------------------

# Generic scholarly section names: structure every field shares, not knowledge of any field.
_NAMED = {name.replace(" ", "").lower(): name for name in (
    "Abstract", "Summary", "Highlights", "Article info", "Keywords", "Key words", "Index terms",
    "Introduction", "Background", "Methods", "Method", "Materials and methods", "Results",
    "Results and discussion", "Discussion", "Conclusion", "Conclusions", "Concluding remarks",
    "Acknowledgements", "Acknowledgments", "Acknowledgement", "Acknowledgment", "Funding",
    "Data availability", "Author contributions", "Declaration of competing interest",
    "Conflict of interest", "Conflicts of interest", "Nomenclature",
    "Supplementary material", "Supplementary materials", "Supplementary data")}
_REFERENCE_NAMES = {"references", "bibliography", "literaturecited", "workscited",
                    "referencelist", "citedliterature"}
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50}
_NUMBERING = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2}){0,3}\.?|[IVXL]{1,6}\.)\s+")
_NUM_HEAD = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(\S.*)$")
_ROMAN_HEAD = re.compile(r"^([IVXL]{1,6})\.\s+(\S.*)$")
_LETTER_HEAD = re.compile(r"^([A-H])\.\s+(\S.*)$")
_INLINE_LABEL = re.compile(r"^(abstract|keywords|key\s+words|index\s+terms)\s*"
                           r"[:.\u2014\u2013-]\s*\S", re.I)
_APPENDIX = re.compile(r"^(?:appendix|appendices)\b(?:\s+[A-Z0-9]{1,3}\b)?[.:]?(?:\s+\S.*)?$",
                       re.I)
_TERMINAL = (".", "?", "!", ":")


def _roman(s: str) -> Optional[int]:
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN.get(ch)
        if v is None:
            return None
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def _letter_spaced(s: str) -> bool:
    toks = s.split()
    return len(toks) >= 4 and all(len(t) <= 3 for t in toks)


def _name_key(s: str) -> str:
    """A line reduced for comparison with a section name: numbering, letter-spacing, case and
    trailing punctuation removed. `a b s t r a c t` and `7. REFERENCES:` both reduce cleanly."""
    s = _NUMBERING.sub("", s.strip())
    return re.sub(r"\s+", "", s).rstrip(":.").lower()


def _successor(last: Optional[tuple], n: tuple) -> bool:
    """Is `n` a plausible next section number after `last`? Tolerates one skipped number, so a
    heading pdftotext mangled does not orphan every heading after it."""
    if last is None:
        return n[0] <= 3 and all(x <= 2 for x in n[1:])
    if len(n) > len(last) and n[:len(last)] == last:
        return n[len(last)] in (1, 2) and all(x <= 2 for x in n[len(last) + 1:])
    for i in range(min(len(n), len(last))):
        if n[i] != last[i]:
            return 1 <= n[i] - last[i] <= 2 and all(x <= 2 for x in n[i + 1:])
    return False


def _heading_rest_ok(rest: str, max_words: int = 12) -> bool:
    rest = rest.strip()
    if not rest or not rest[0].isupper() or rest.endswith((".", ",", ";", "-")):
        return False
    words = rest.split()
    if len(words) > max_words or not any(len(re.sub(r"[^\w]", "", w)) >= 3 for w in words):
        return False
    letters = sum(c.isalpha() for c in rest)
    return letters >= 0.7 * sum(not c.isspace() for c in rest)


def _styled(s: str) -> bool:
    """A short Title-Case or ALL-CAPS line, the only cue an unnumbered heading leaves in text."""
    s = s.strip()
    words = s.split()
    if not (1 <= len(words) <= 8) or len(s) > 60 or s.endswith((".", ",", ";", ":")):
        return False
    if any(c.isdigit() for c in s) or not s[0].isupper():
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 4:
        return False
    if all(c.isupper() for c in letters):
        return True
    long_words = [w for w in words if len(re.sub(r"[^\w]", "", w)) >= 4]
    return bool(long_words) and all(w[0].isupper() for w in long_words)


# --- the builder ----------------------------------------------------------------------------

def _ends_paragraph_before(prev: Optional[dict], width: float) -> bool:
    """Does the line after `prev` start a paragraph (so it may be a heading)?"""
    if prev is None or prev["gap_after"] in ("flow", "page") or prev.get("heading"):
        return True
    t = prev["text"].rstrip()
    return t.endswith(_TERMINAL) or len(t) < 0.8 * width


def build_from_pdf(pdf, source_id: str, meta: Optional[dict] = None) -> dict:
    """Read a PDF into an offset-anchored store, the same shape `build_from_jats` returns.

    `pdf` is the file's bytes or a path. Text comes from poppler's `pdftotext` in its default
    reading order (not `-layout`), which reads a two-column page column by column; the page
    geometry is checked for pages where that failed and they are FLAGGED in meta, not repaired
    by guesswork. The one repair made is provably lossless: a page whose text was drawn several
    times over (pdftotext then interleaves the copies word by word) is re-read in content-stream
    order and, only if the copies are character-for-character identical, kept once.

    `meta["pages"]` holds a `[start, end]` range into `full.txt` per page (`page_of` reads it),
    and every normalisation is counted in meta so it is reported rather than hidden.

    Raises `PdfToTextMissing` if pdftotext is not installed and `ValueError` if the PDF has no
    text layer (a scanned image). Never returns an empty store.
    """
    exe = _pdftotext_bin()
    caps = _pdftotext_caps(exe)
    with tempfile.TemporaryDirectory(prefix="lit2db-pdf-") as tmp:
        if isinstance(pdf, (bytes, bytearray)):
            data = bytes(pdf)
            path = str(pathlib.Path(tmp) / "source.pdf")
            pathlib.Path(path).write_bytes(data)
        else:
            path = str(pathlib.Path(pdf))
            data = pathlib.Path(path).read_bytes()
        text_args = ["-remove-hyphens", "none"] if caps["remove_hyphens"] else []
        raw = _run_pdftotext(exe, text_args, path, source_id)
        geometry = _page_geometry(_run_pdftotext(exe, ["-tsv"], path, source_id)) \
            if caps["tsv"] else None

        raw_pages = raw.split("\f")
        if raw_pages and raw_pages[-1] == "":
            raw_pages.pop()                         # pdftotext ends every page with \f
        pages, at = [], 0
        for page_text in raw_pages:
            lines, off = [], at
            for ln in page_text.split("\n"):
                lines.append({"text": ln, "raw": off})
                off += len(ln) + 1
            pages.append(lines)
            at += len(page_text) + 1

        interleaved, out_of_order, overprinted = [], [], []
        untrusted = set()                       # pages whose block order is not to be relied on
        if geometry is not None:
            for pno in range(1, len(pages) + 1):
                g = geometry.get(pno)
                if not g or not g["lines"]:
                    continue
                over = _overlapping_lines(g["lines"])
                if over >= 20 and over >= 0.5 * len(g["lines"]):
                    entry = {"page": pno, "overlapping_lines": over,
                             "lines_on_page": len(g["lines"])}
                    stream = _run_pdftotext(exe, ["-raw", "-f", str(pno), "-l", str(pno)],
                                            path, source_id).rstrip("\f").split("\n")
                    kept, copies = _collapse_exact_repeats([s for s in stream if s.strip()])
                    if copies >= 2:
                        pages[pno - 1] = [{"text": s, "raw": None} for s in kept]
                        entry.update(recovered=True, identical_copies=copies,
                                     method="re-read in content-stream order (-raw); the "
                                            "identical copies collapsed to one")
                        overprinted.append(entry)
                        continue                # the geometry below describes the copies
                    entry.update(recovered=False,
                                 why="the copies are not identical, so collapsing them would "
                                     "be a guess; the page is left as pdftotext emitted it")
                    overprinted.append(entry)
                    untrusted.add(pno)
                hops = _column_hops(g["width"], g["lines"])
                if hops >= 3:
                    interleaved.append({"page": pno, "same_height_column_hops": hops})
                    untrusted.add(pno)
                returns = _column_returns(g["width"], g["lines"])
                if returns:
                    out_of_order.append({"page": pno, "column_returns": returns})
                    untrusted.add(pno)

    sha = hashlib.sha256(data).hexdigest()
    if not any(ln["text"].strip() for page in pages for ln in page):
        raise ValueError(f"{source_id}: pdftotext found no text in this PDF (sha256 {sha[:12]}). "
                         f"It is probably scanned images with no text layer; OCR it first. "
                         f"Refusing to emit an empty store.")

    dropped = _drop_running_lines(pages)
    refs = _cut_references(pages)
    full_text, page_ranges, headings, counts = _assemble(pages, untrusted)
    if not full_text.strip():
        raise ValueError(f"{source_id}: nothing left after normalisation; refusing to emit an "
                         f"empty store")

    sections = []
    for i, h in enumerate(headings):
        end = len(full_text)
        for later in headings[i + 1:]:
            if later["depth"] <= h["depth"]:
                end = later["start"]
                break
        sections.append({"title": h["title"], "start": h["start"], "end": end,
                         "depth": h["depth"]})
    sections.sort(key=lambda s: (s["start"], s["depth"]))
    rules: dict = {}
    for h in headings:
        rules[h["rule"]] = rules.get(h["rule"], 0) + 1

    stamped = {
        **dict(meta or {}),
        "source_text_scope": "full_text_pdf",
        "pdf_sha256": sha,
        "extractor": f"pdftotext {caps['version']}",
        "extractor_args": ["-enc", "UTF-8", *text_args],
        "reading_order": "pdftotext default (column by column), except pages listed as "
                         "recovered in overprinted_pages, which are in content-stream order",
        "page_count": len(pages),
        "pages": page_ranges,
        "sections_method": "heuristic",
        "heading_rules": rules,
        "normalisation": {**counts,
                          "hyphen_rule": ("lit2db: removed only before a lowercase letter"
                                          if caps["remove_hyphens"] else
                                          "pdftotext removed every line-end hyphen; this "
                                          "version has no -remove-hyphens option")},
        "headers_footers_dropped": dropped,
        "headers_footers_dropped_lines": sum(len(d["pages"]) for d in dropped),
        "references_cut": refs,
        "layout_check": ({"ran": True, "pages_checked": len(geometry)} if geometry is not None
                         else {"ran": False, "why": "this pdftotext has no -tsv option, so "
                               "reading order was NOT checked; not the same as clean"}),
        "interleaved_pages": interleaved,
        "column_order_suspect_pages": out_of_order,
        "overprinted_pages": overprinted,
        "nonprinting_chars_kept": sum(1 for c in full_text
                                      if (ord(c) < 32 and c not in "\n\t")
                                      or 0xE000 <= ord(c) <= 0xF8FF),
    }
    return {
        "source_id": source_id,
        "full_text": full_text,
        "sections": sections,
        "tables": [],
        "meta": stamped,
        "stats": {"chars": len(full_text), "sections": len(sections),
                  "est_tokens": round(len(full_text) / 4)},
    }


def _cut_references(pages: list) -> dict:
    """Cut from the LAST reference-list heading onward, keeping an appendix that follows it.

    Mirrors `build_from_jats`, which drops `<ref-list>` and keeps appendices: a reference list
    is dense with other papers' claims, and a value must not ground against the bibliography.
    """
    flat = [(p, i) for p, page in enumerate(pages) for i in range(len(page))]
    at = None
    for k, (p, i) in enumerate(flat):
        s = pages[p][i]["text"].strip()
        if s and len(s) <= 40 and _name_key(s) in _REFERENCE_NAMES:
            at = k
    if at is None:
        return {"cut": False, "why": "no References, Bibliography, Literature cited or Works "
                                     "cited heading was found, so nothing was cut"}
    resume = None
    for k in range(at + 1, len(flat)):
        p, i = flat[k]
        s = pages[p][i]["text"].strip()
        if s and len(s) <= 100 and _APPENDIX.match(s):
            resume = k
            break
    hp, hi = flat[at]
    heading = pages[hp][hi]
    stop = resume if resume is not None else len(flat)
    doomed = set(flat[at:stop])
    chars = sum(len(pages[p][i]["text"]) + 1 for p, i in doomed)
    out = {"cut": True, "heading": heading["text"].strip(), "page": hp + 1,
           "raw_offset": heading["raw"], "lines_cut": len(doomed), "chars_cut": chars,
           "appendix_kept": None}
    if resume is not None:
        ap, ai = flat[resume]
        out["appendix_kept"] = {"heading": pages[ap][ai]["text"].strip(), "page": ap + 1,
                                "raw_offset": pages[ap][ai]["raw"]}
    for p in range(len(pages)):
        pages[p] = [ln for i, ln in enumerate(pages[p]) if (p, i) not in doomed]
    return out


def _width(lines: list) -> Optional[float]:
    lens = [len(t) for t in lines if len(t) >= 20]
    return statistics.median(lens) if len(lens) >= 5 else None


def _assemble(pages: list, untrusted: frozenset = frozenset()):
    """Lines to text: join wraps, apply the hyphen rule, find headings, record page ranges."""
    seq = []                                         # non-blank lines with the gap before them
    for pno, page in enumerate(pages, 1):
        gap = "page" if seq else None
        for ln in page:
            t = ln["text"].rstrip()
            if not t.strip():
                if seq and gap is None:
                    gap = "flow"
                continue
            if seq:
                seq[-1]["gap_after"] = gap or "line"
            seq.append({"text": t, "page": pno, "gap_after": None})
            gap = None
    if not seq:
        return "", [[0, 0] for _ in pages], [], {}

    doc_w = _width([s["text"] for s in seq]) or max(len(s["text"]) for s in seq)
    page_w = {}
    for pno in range(1, len(pages) + 1):
        page_w[pno] = _width([s["text"] for s in seq if s["page"] == pno]) or doc_w

    # The document's own spelling decides a line-end hyphen it can decide: a word it prints
    # hyphenated mid-line, and never joined, keeps its hyphen when broken at a line end.
    words = re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", " ".join(s["text"] for s in seq))
    hyphenated = {w.lower() for w in words if "-" in w}
    plain = {w.lower() for w in words if "-" not in w}

    # Headings, in document order (numbering is checked for sequence).
    last_num, last_roman, last_letter = None, None, None
    numbered = 0
    for k, s in enumerate(seq):
        t = s["text"].strip()
        # A label run into its paragraph ("Abstract: We ...", "Index Terms-...") is cue enough on
        # its own; the front-matter lines before it are too ragged to judge by width.
        m_inline = _INLINE_LABEL.match(t)
        if m_inline:
            label = re.sub(r"\s+", " ", m_inline.group(1))
            s["heading"] = {"title": label[0].upper() + label[1:].lower(), "depth": 0,
                            "rule": "inline_label", "inline": True}
            continue
        if not _ends_paragraph_before(seq[k - 1] if k else None, page_w[s["page"]]):
            continue
        m = _NUM_HEAD.match(t)
        if m and len(t) <= 100 and _heading_rest_ok(m.group(2)):
            n = tuple(int(x) for x in m.group(1).split("."))
            if _successor(last_num, n):
                s["heading"] = {"title": t, "depth": len(n) - 1, "rule": "numbered"}
                last_num, numbered = n, numbered + 1
                continue
        m = _ROMAN_HEAD.match(t)
        if m and len(t) <= 100 and _heading_rest_ok(m.group(2)):
            r = _roman(m.group(1))
            if r and ((last_roman is None and r <= 2) or
                      (last_roman is not None and 1 <= r - last_roman <= 2)):
                s["heading"] = {"title": t, "depth": 0, "rule": "numbered"}
                last_roman, last_letter, numbered = r, None, numbered + 1
                continue
        m = _LETTER_HEAD.match(t)
        if m and last_roman is not None and len(t) <= 80 and _heading_rest_ok(m.group(2), 10):
            v = ord(m.group(1)) - ord("A") + 1
            if (last_letter is None and v == 1) or \
                    (last_letter is not None and 1 <= v - last_letter <= 2):
                s["heading"] = {"title": t, "depth": 1, "rule": "numbered"}
                last_letter = v
                continue
        if len(t) <= 60:
            key = _name_key(t)
            if key in _NAMED:
                title = _NAMED[key] if _letter_spaced(_NUMBERING.sub("", t)) else t
                s["heading"] = {"title": title, "depth": 0, "rule": "named"}
                continue
        if len(t) <= 100 and _APPENDIX.match(t):
            s["heading"] = {"title": t, "depth": 0, "rule": "named"}
    if numbered < 2:
        # No usable numbering: fall back to typography, but only after the front matter (the
        # first recognised heading), where titles, authors and affiliations are Title Case too.
        started = False
        for k, s in enumerate(seq):
            if s.get("heading"):
                started = True
                continue
            if not started or not _styled(s["text"]):
                continue
            nxt = seq[k + 1] if k + 1 < len(seq) else None
            if nxt is None or _styled(nxt["text"]):
                continue
            if _ends_paragraph_before(seq[k - 1] if k else None, page_w[s["page"]]):
                s["heading"] = {"title": s["text"].strip(), "depth": 0, "rule": "styled"}

    counts = {"lines_joined": 0, "hyphens_removed": 0, "hyphens_kept_before_non_lowercase": 0,
              "hyphens_kept_compound": 0, "joined_across_columns": 0, "joined_across_pages": 0,
              "joins_withheld_order_suspect": 0}
    joins = []                                      # joiner placed BEFORE seq[k]
    for k in range(1, len(seq)):
        a, b = seq[k - 1], seq[k]
        gap = a["gap_after"]
        para = "\n" if gap == "line" else "\n\n"
        if (a.get("heading") and not a["heading"].get("inline")) or b.get("heading"):
            joins.append("\n\n")
            continue
        w = page_w[a["page"]]
        at, bt = a["text"].rstrip(), b["text"].lstrip()
        joined, key = None, None
        if re.search(r"[^\W\d_]-$", at) and len(at) >= 0.5 * w:
            if bt[:1].islower():
                left = re.sub(r"^\W+", "", at.split()[-1][:-1])
                right = re.match(r"[^\W\d_]*", bt).group(0)
                if (left + "-" + right).lower() in hyphenated and \
                        (left + right).lower() not in plain:
                    joined, key = "-", "hyphens_kept_compound"
                else:
                    joined, key = "", "hyphens_removed"
            elif gap == "line":
                joined, key = "-", "hyphens_kept_before_non_lowercase"
        elif len(at) >= 0.8 * w and (
                gap == "line" or (not at.endswith(_TERMINAL) and bt[:1].islower())):
            joined, key = " ", "lines_joined"
        if joined is not None and gap != "line" and \
                (a["page"] in untrusted or b["page"] in untrusted):
            # Across a block or page boundary the join is only as good as the order of the
            # blocks, and on this page that order is suspect: a wrong join would splice two
            # unrelated sentences into one that no author wrote.
            counts["joins_withheld_order_suspect"] += 1
            joined = None
        if joined is None:
            joins.append(para)
            continue
        counts[key] += 1
        if gap == "flow":
            counts["joined_across_columns"] += 1
        elif gap == "page":
            counts["joined_across_pages"] += 1
        joins.append(("JOIN", joined))

    parts, pos = [], 0
    first, last = {}, {}
    headings = []
    for k, s in enumerate(seq):
        text = s["text"].rstrip() if k else s["text"].strip()
        if k:
            j = joins[k - 1]
            if isinstance(j, tuple):
                # The previous line's trailing hyphen is replaced by the joiner: "" removes it,
                # "-" keeps it, " " was an ordinary wrap (its last char was not a hyphen).
                if j[1] == " ":
                    parts.append(" ")
                    pos += 1
                else:
                    parts[-1] = parts[-1][:-1] + j[1]
                    pos += len(j[1]) - 1
                    last[seq[k - 1]["page"]] = pos
            else:
                parts.append(j)
                pos += len(j)
        first.setdefault(s["page"], pos)
        if s.get("heading"):
            headings.append({**s["heading"], "start": pos})
        parts.append(text)
        pos += len(text)
        last[s["page"]] = pos
    full = "".join(parts)

    ranges, cursor = [], 0
    for pno in range(1, len(pages) + 1):
        if pno in first:
            ranges.append([first[pno], last[pno]])
            cursor = last[pno]
        else:
            ranges.append([cursor, cursor])          # nothing kept from this page
    return full, ranges, headings, counts


def page_of(store: dict, offset: int) -> Optional[int]:
    """The 1-based page an offset into `full.txt` falls on, for a store built from a PDF.

    None for a store with no page map (JATS, abstract) and for an offset on the separator
    between two pages, which belongs to neither, the same convention `section_of` uses.
    """
    for i, (start, end) in enumerate((store.get("meta") or {}).get("pages") or [], 1):
        if start <= offset < end:
            return i
    return None
