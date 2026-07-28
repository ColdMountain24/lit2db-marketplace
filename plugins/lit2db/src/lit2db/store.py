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
"""
from __future__ import annotations

import json
import pathlib
import re
import xml.etree.ElementTree as ET
from typing import Optional

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

    Returns `{source_id, full_text, sections, meta, stats}`. Raises on unparseable XML rather
    than returning an empty store — a source that silently yields no text would look, three
    stages later, exactly like a paper that simply contained nothing extractable.
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

    Three files, because the extractor reaches them with Read/Grep/Glob rather than through
    an API: `full.txt` (the offset authority), `sections.json`, `meta.json`.
    """
    d = pathlib.Path(root_dir) / store_dirname(store["source_id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "full.txt").write_text(store["full_text"], encoding="utf-8")
    (d / "sections.json").write_text(json.dumps(store["sections"], indent=2))
    (d / "meta.json").write_text(json.dumps(
        {**store["meta"], "source_id": store["source_id"], **store["stats"]}, indent=2))
    return {"dir": str(d), "full_text": str(d / "full.txt"),
            "sections": str(d / "sections.json"), "meta": str(d / "meta.json")}


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
