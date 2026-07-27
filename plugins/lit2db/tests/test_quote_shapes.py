"""A verbatim quote may arrive as a list — one per element of a multi-valued field.

Found by `replay.py` in under a second, on artifacts already on disk, months of runs after the
shape was first produced. `find_spans` got a list and raised, which killed the whole paper —
and under paper isolation (v0.25.0) that is WORSE than a crash, because the paper is recorded
as failed and silently lost from the wave instead of stopping it.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lit2db.store import find_spans        # noqa: E402

FULL = ("The enzyme converted FPP into amorpha-4,11-diene as the major product, "
        "alongside acyclic products and limonene in minor amounts.")


def _anchor(quote):
    """The anchoring rule from run_wave.assemble, exercised on its own."""
    if isinstance(quote, (list, tuple)):
        parts = [q for q in quote if isinstance(q, str) and q.strip()]
        hits = next((h for h in (find_spans(FULL, q) for q in parts) if h), [])
        return hits, (" | ".join(parts) if parts else None)
    return (find_spans(FULL, quote) if quote else []), quote


def test_a_list_of_quotes_anchors_on_its_first_resolvable_element():
    hits, quote = _anchor(["amorpha-4,11-diene", "acyclic products", "limonene"])
    assert hits, "a list of real quotes must anchor, not raise"
    assert quote == "amorpha-4,11-diene | acyclic products | limonene"


def test_a_list_whose_first_element_is_absent_still_anchors_on_a_later_one():
    hits, _ = _anchor(["not in the paper at all", "limonene"])
    assert hits


def test_a_list_with_nothing_resolvable_anchors_nothing():
    """Must behave exactly like an unanchorable scalar — dropped, never invented."""
    hits, quote = _anchor(["absent phrase one", "absent phrase two"])
    assert hits == [] and quote is not None


def test_an_empty_or_junk_list_is_treated_as_no_quote():
    for bad in ([], ["", "   "], [None, 42]):
        hits, quote = _anchor(bad)
        assert hits == [] and quote is None


def test_a_plain_string_quote_is_unchanged():
    hits, quote = _anchor("amorpha-4,11-diene")
    assert hits and quote == "amorpha-4,11-diene"


def test_the_joined_quote_never_claims_an_anchor_it_lacks():
    """The stored quote is the join of every element, but the offset belongs to one of them.
    The join must not imply the whole string appears contiguously in the source."""
    _, quote = _anchor(["amorpha-4,11-diene", "limonene"])
    assert find_spans(FULL, quote) == [], (
        "the joined form is a record of what was cited, not itself a locatable span")
