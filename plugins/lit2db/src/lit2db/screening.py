"""Does this source plausibly contain the entity at all? Ask before paying to extract from it.

The gap this closes, measured on a real corpus: the terpenoid wave-1 subset was screened on
SOURCE ORGANISM — 382 papers whose text is unambiguously about bacteria. But the schema is about
**terpene synthase enzymes**, and only **24%** of those papers name terpene-synthase machinery
anywhere in title or abstract. Two papers picked at random from the "clearly bacterial" bucket
turned out to be gut microbiota of hepatitis B patients, and *Zingiber officinale* — ginger.

Extracting the other 76% would have cost ~50M tokens to produce nothing, and the resulting
near-zero yield would have read as a broken pipeline rather than a corpus that never contained
the entity. **That is the failure this module exists to prevent**, and it is distinct from every
screen already in place: organism screening asks *whose* biology this is, relation screening asks
*which direction* the claim runs, and neither asks whether the thing being catalogued is present.

**The terms are RESEARCHER-RATIFIED, never invented here.** Which words indicate that an entity
is present is domain substance and ratifies through the ledger like any inclusion criterion
(D-033's logic: record the executable screen, not a description of it). This module runs terms it
is given and reports what matched and where; it never proposes them, and it never decides scope.

A screen is not a verdict. `uncertain` is a real outcome and the default when nothing matches in
the head but the source was not read in full — because a paper can report an enzyme in its
results without announcing it in the abstract. Callers choose whether uncertain sources are
extracted, and that choice is itself ratifiable.

Deliberately STDLIB-ONLY, like `gate` and `accounting`.
"""
from __future__ import annotations

import re

# How much of a source counts as "the announcement" — title plus abstract, roughly. A paper that
# reports an entity in its results without naming it here is exactly why `uncertain` exists.
DEFAULT_HEAD_CHARS = 3000


def _compile(terms) -> list:
    """Terms are matched case-insensitively, boundary-anchored, and tolerant of a plural.

    Two failures this shape avoids, both real:

    * Substring matching would make a ratified `TPS` hit `ATPSynthase` and `hTPS1`, quietly
      widening a screen the researcher scoped narrowly. The leading boundary prevents it.
    * A strict trailing boundary made `terpene synthase` miss *"twelve terpene **synthases**"* —
      the exact title the screen exists to catch. Scientific terms are pluralised constantly, and
      a screen that misses the plural of its own term is worse than no screen, because it reports
      a confident `absent`.

    So the trailing boundary allows an optional `s`/`es`. That is narrow enough to keep `TPS`
    from reaching `TPSase` (the next character is `a`, not a boundary or a plural).
    """
    out = []
    for t in terms or []:
        t = str(t).strip()
        if not t:
            continue
        pat = re.escape(t)
        if re.match(r"^\w", t):
            pat = r"\b" + pat
        if re.search(r"\w$", t):
            pat = pat + r"(?:e?s)?\b"
        out.append((t, re.compile(pat, re.I)))
    return out


def screen_source(text: str, *, require_any=(), exclude_any=(),
                  head_chars: int = DEFAULT_HEAD_CHARS, full_text: bool = False) -> dict:
    """Verdict on one source. Returns `{verdict, matched, excluded, scope, head_chars}`.

    `verdict` is one of:
      * `present`   — a required term was found
      * `excluded`  — an exclusion term was found (checked FIRST; a disqualifier outranks a hit,
                      so a paper that both names the entity and is disqualified is excluded)
      * `absent`    — nothing matched and the WHOLE source was searched, so this is a real absence
      * `uncertain` — nothing matched in the head, but the source was not read in full

    The `absent`/`uncertain` split is the honest part. Screening on the head is cheap and is what
    makes the screen worth running at all; calling its misses "absent" would silently discard
    papers that name the entity only in their results.
    """
    if not text:
        return {"verdict": "uncertain", "matched": [], "excluded": [],
                "scope": "empty", "head_chars": 0}

    scope_text = text if full_text else text[:head_chars]
    scope = "full_text" if full_text else f"head:{min(head_chars, len(text))}"

    excluded = [{"term": t, "offset": m.start()}
                for t, rx in _compile(exclude_any)
                for m in [rx.search(scope_text)] if m]
    if excluded:
        return {"verdict": "excluded", "matched": [], "excluded": excluded,
                "scope": scope, "head_chars": head_chars}

    matched = [{"term": t, "offset": m.start(), "quote": scope_text[m.start():m.start() + 90]}
               for t, rx in _compile(require_any)
               for m in [rx.search(scope_text)] if m]
    if matched:
        return {"verdict": "present", "matched": matched, "excluded": [],
                "scope": scope, "head_chars": head_chars}

    if not require_any:
        # Nothing to require means nothing to screen on — say so rather than implying a pass.
        return {"verdict": "uncertain", "matched": [], "excluded": [],
                "scope": scope, "head_chars": head_chars}

    return {"verdict": "absent" if full_text else "uncertain", "matched": [], "excluded": [],
            "scope": scope, "head_chars": head_chars}


def screen_corpus(sources, *, require_any=(), exclude_any=(),
                  head_chars: int = DEFAULT_HEAD_CHARS, full_text: bool = False) -> dict:
    """Screen many sources. `sources` is an iterable of `(source_id, text)`.

    Returns per-verdict id lists plus the counts a run manifest should print. Nothing is deleted:
    a screen partitions a corpus, it never shrinks one. Which partitions get extracted is a
    ratified decision (the corpus itself is defined by its query, D-033).
    """
    buckets: dict = {"present": [], "absent": [], "uncertain": [], "excluded": []}
    evidence: dict = {}
    for source_id, text in sources:
        r = screen_source(text, require_any=require_any, exclude_any=exclude_any,
                          head_chars=head_chars, full_text=full_text)
        buckets[r["verdict"]].append(source_id)
        if r["matched"] or r["excluded"]:
            evidence[source_id] = r
    n = sum(len(v) for v in buckets.values())
    return {
        "n_sources": n,
        "counts": {k: len(v) for k, v in buckets.items()},
        "by_verdict": buckets,
        "evidence": evidence,
        "scope": "full_text" if full_text else f"head:{head_chars}",
        "note": ("`uncertain` means the head did not name the entity and the source was not read "
                 "in full — it is not the same as absent, and whether it is extracted is a "
                 "ratified choice, not this module's."),
    }
