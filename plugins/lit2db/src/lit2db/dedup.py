"""One canonical paper per work — by identifier where one exists, by flag where none does.

Ported from RAW (`backend/app/core/dedup.py`), reimplemented stdlib-only and DB-free so the
plugin keeps its `pip install pydantic mcp` install story.

The priority ladder is RAW's, and the order is the whole design:

    1. DOI        — a true identifier
    2. PMID       — a true identifier
    3. source ids — stable ids for the identifier-less tail (PMCID, arXiv, NCT, S2, OpenAlex)
    4. fuzzy      — title + first author + year, and **flagged, never merged**

Rung 4 is where most dedup implementations quietly go wrong. RAW's rule, kept verbatim in
spirit: *we would rather under-merge than wrongly collapse two distinct works.* A false merge
destroys a record and its provenance with no trace; a false split leaves two rows and a flag a
human can resolve in seconds. Those costs are not symmetric, so the threshold is high (0.92) and
the action is to mark, not to combine. That is also already lit2db's philosophy everywhere else —
`ensemble.py` computes agreement and refuses to *judge* it, and the write-gate denies rather than
repairs.

**Corrections are the case that motivated porting this.** The frozen terpenoid corpus contains
`PMC12302711` — a 1,697-character *"Correction to: Discovery of bifunctional diterpene
cyclases/synthases in bacteria…"* — alongside `PMC11469919`, the 39,562-character paper it
corrects. Both were counted as papers. A correction notice yields either nothing or a spurious
duplicate of a record already extracted from the original, and in PRISMA terms it is a duplicate
to remove, not a paper to screen. RAW had no need for this rung; we do, so it is added here and
marked as ours.

Domain-INVARIANT: this module reads identifiers, titles, authors and years. It knows nothing
about what any paper is *about*, and never decides whether a work belongs in a corpus — that is
inclusion/exclusion, which is researcher substance and ratifies through the ledger (D-033).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# High on purpose. Fuzzy is the last resort and only ever FLAGS, so a miss costs a duplicate row
# while a false positive would silently destroy a distinct work.
FUZZY_TITLE_RATIO = 0.92

# Stable ids that act as dedup keys when DOI and PMID are both absent. Order is lookup
# preference only — it never overrides doi/pmid, which are checked first.
SOURCE_ID_KEYS = ("pmcid", "arxiv", "nct", "s2", "openalex")

# A correction/erratum announces itself in its title. Matching the *relationship* rather than a
# publication-type field is deliberate: the field is frequently absent or wrong in Europe PMC,
# and the title prefix is what actually distinguished the real case we hit.
_CORRECTION_PREFIX = re.compile(
    r"^\s*(correction|corrigendum|erratum|addendum|retraction|publisher correction|"
    r"author correction)\b\s*(to|for|:)?\s*:?\s*", re.I)

_WORD = re.compile(r"[a-z0-9]+")
_TAGS = re.compile(r"<[^>]+>")


def normalize_title(title: str) -> str:
    """Lowercased word bag. Strips the inline markup Europe PMC returns (`<i>`, `&lt;i&gt;`),
    which otherwise makes two renderings of one title look like different works."""
    if not title:
        return ""
    t = title.replace("&lt;", "<").replace("&gt;", ">")
    t = _TAGS.sub(" ", t)
    return " ".join(_WORD.findall(t.lower()))


def strip_correction_prefix(title: str) -> tuple[bool, str]:
    """(is_correction, the title of the work it corrects)."""
    m = _CORRECTION_PREFIX.match(title or "")
    if not m:
        return False, title or ""
    return True, (title or "")[m.end():]


def _first_author(paper: dict) -> str:
    a = paper.get("authors") or paper.get("author") or ""
    if isinstance(a, (list, tuple)):
        a = a[0] if a else ""
    if isinstance(a, dict):
        a = a.get("lastName") or a.get("name") or ""
    return " ".join(_WORD.findall(str(a).lower()))[:40]


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


@dataclass(frozen=True)
class DedupResult:
    """What happened to one paper. `verdict` names WHICH rung of the ladder decided it, so a
    corpus report can say how much of its dedup rested on identifiers versus on a guess."""
    key: str                      # canonical key for this work
    verdict: str                  # new | doi | pmid | source_id | fuzzy_flagged | correction_of
    matched: str | None = None    # the existing key it matched, if any
    reason: str = ""

    @property
    def is_duplicate(self) -> bool:
        return self.verdict in ("doi", "pmid", "source_id")

    @property
    def needs_review(self) -> bool:
        """Flagged, not resolved. Both rows are kept and a human decides."""
        return self.verdict in ("fuzzy_flagged", "correction_of")


@dataclass
class Registry:
    """In-memory canonical-work registry. Feed papers in; get one verdict each."""
    _by_doi: dict = field(default_factory=dict)
    _by_pmid: dict = field(default_factory=dict)
    _by_source: dict = field(default_factory=dict)
    _titles: list = field(default_factory=list)      # (key, title, author, year)
    flagged: list = field(default_factory=list)      # DedupResults needing review

    def __len__(self) -> int:
        return len(self._titles)

    def register(self, paper: dict) -> DedupResult:
        key = (paper.get("pmcid") or paper.get("doi") or paper.get("pmid")
               or paper.get("id") or f"anon{len(self._titles)}")
        title = paper.get("title") or ""
        doi = (paper.get("doi") or "").strip().lower() or None
        pmid = str(paper.get("pmid") or "").strip() or None

        # --- rungs 1 and 2: true identifiers -----------------------------------------
        if doi and doi in self._by_doi:
            return DedupResult(key, "doi", self._by_doi[doi], f"same DOI {doi}")
        if pmid and pmid in self._by_pmid:
            return DedupResult(key, "pmid", self._by_pmid[pmid], f"same PMID {pmid}")

        # --- rung 3: stable source ids ------------------------------------------------
        for k in SOURCE_ID_KEYS:
            v = paper.get(k)
            if v and (k, str(v)) in self._by_source:
                return DedupResult(key, "source_id", self._by_source[(k, str(v))],
                                   f"same {k} {v}")

        # --- correction/erratum (lit2db addition, not in RAW) -------------------------
        is_corr, corrected = strip_correction_prefix(title)
        if is_corr:
            best, ratio = self._closest(corrected)
            if best and ratio >= FUZZY_TITLE_RATIO:
                res = DedupResult(key, "correction_of", best,
                                  f"correction notice for {best} (title match {ratio:.2f})")
                self.flagged.append(res)
                self._remember(key, title, paper, doi, pmid)
                return res
            # A correction whose original is not in the corpus is still not a research paper.
            res = DedupResult(key, "correction_of", None,
                              "correction notice; the work it corrects is not in this corpus")
            self.flagged.append(res)
            self._remember(key, title, paper, doi, pmid)
            return res

        # --- rung 4: fuzzy — FLAG, never merge ----------------------------------------
        best, ratio = self._closest(title, author=_first_author(paper),
                                    year=str(paper.get("year") or ""))
        if best and ratio >= FUZZY_TITLE_RATIO:
            res = DedupResult(key, "fuzzy_flagged", best,
                              f"title {ratio:.2f} similar to {best}; kept BOTH, flagged for review")
            self.flagged.append(res)
            self._remember(key, title, paper, doi, pmid)
            return res

        self._remember(key, title, paper, doi, pmid)
        return DedupResult(key, "new")

    # --- internals --------------------------------------------------------------------
    def _closest(self, title: str, author: str = "", year: str = "") -> tuple[str | None, float]:
        best, best_ratio = None, 0.0
        for key, other_title, other_author, other_year in self._titles:
            r = title_similarity(title, other_title)
            # Author/year are corroborating only — a matching pair nudges a borderline title
            # over the line, but neither can carry a match on its own.
            if r >= FUZZY_TITLE_RATIO - 0.04 and author and other_author and author == other_author:
                r = min(1.0, r + 0.02)
            if r >= FUZZY_TITLE_RATIO - 0.04 and year and other_year and year == other_year:
                r = min(1.0, r + 0.02)
            if r > best_ratio:
                best, best_ratio = key, r
        return best, best_ratio

    def _remember(self, key, title, paper, doi, pmid) -> None:
        if doi:
            self._by_doi.setdefault(doi, key)
        if pmid:
            self._by_pmid.setdefault(pmid, key)
        for k in SOURCE_ID_KEYS:
            v = paper.get(k)
            if v:
                self._by_source.setdefault((k, str(v)), key)
        self._titles.append((key, title, _first_author(paper), str(paper.get("year") or "")))


def dedupe(papers) -> dict:
    """Run a whole corpus through the ladder. Returns the report a corpus build should print.

    `unique` is the count a corpus may honestly claim. Anything in `flagged` is kept on disk and
    surfaced for a human — nothing here deletes a paper.
    """
    reg = Registry()
    results = [reg.register(p) for p in papers]
    by_verdict: dict = {}
    for r in results:
        by_verdict[r.verdict] = by_verdict.get(r.verdict, 0) + 1
    return {
        "n_in": len(results),
        "unique": sum(1 for r in results if r.verdict == "new"),
        "by_verdict": by_verdict,
        "flagged": [{"key": r.key, "verdict": r.verdict, "matched": r.matched,
                     "reason": r.reason} for r in reg.flagged],
        "results": results,
    }
