"""D-038 — the retained-source fraction is recorded, and a quote may not cite unread text.

The BBB pilot sent `text[:12000]` (a measured 26.2% of the mean source) with no record of it
anywhere in the output. These tests pin the two rules that make that impossible to repeat
silently: retention is stated in full or not at all, and `char_offset` must fall inside what
was actually read.
"""
import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from lit2db.contracts.provenance import LiteratureProvenance


def _prov(**kw):
    base = dict(
        source_id="PMC12298776",
        retrieval_timestamp=datetime(2026, 7, 26, tzinfo=timezone.utc),
        producing_process="claude-sonnet-4-5/extractor@0.9.0",
        verbatim_quote="the enzyme converts FPP to pentalenene",
        char_offset=4200,
    )
    base.update(kw)
    return LiteratureProvenance(**base)


def test_retention_is_optional_and_absent_by_default():
    """The scaffold never truncates, so a record may simply not carry the figures."""
    p = _prov()
    assert p.source_chars_total is None
    assert p.source_chars_read is None
    assert p.retained_fraction is None


def test_full_read_reports_fraction_of_one():
    p = _prov(source_chars_total=42882, source_chars_read=42882)
    assert p.retained_fraction == 1.0


def test_truncated_read_reports_the_real_fraction():
    """The BBB case, made visible: 12000 of 42882 chars is 28%, and it says so."""
    p = _prov(source_chars_total=42882, source_chars_read=12000, char_offset=100)
    assert round(p.retained_fraction, 3) == 0.280


def test_a_retained_count_without_its_denominator_is_refused():
    """`read=12000` alone states nothing — 12000 of what?"""
    with pytest.raises(ValidationError, match="recorded together"):
        _prov(source_chars_read=12000, char_offset=100)
    with pytest.raises(ValidationError, match="recorded together"):
        _prov(source_chars_total=42882)


def test_reading_more_than_the_source_contains_is_refused():
    with pytest.raises(ValidationError, match="exceeds"):
        _prov(source_chars_total=1000, source_chars_read=2000, char_offset=10)


def test_a_quote_may_not_cite_text_that_was_never_read():
    """The load-bearing rule: offset 4200 against a 12000-char read is fine...

    ...but against a 4000-char read it means the record cites a span the extractor never
    received. The offset is wrong or the quote was fabricated; either way it must not validate.
    """
    _prov(source_chars_total=42882, source_chars_read=12000)          # ok: 4200 < 12000

    with pytest.raises(ValidationError, match="never saw"):
        _prov(source_chars_total=42882, source_chars_read=4000)       # 4200 >= 4000


def test_offset_at_the_exact_read_boundary_is_refused():
    """`read` is a count, `char_offset` is a 0-based index: offset == read is off the end."""
    with pytest.raises(ValidationError, match="never saw"):
        _prov(source_chars_total=9000, source_chars_read=4200)


def test_negative_counts_are_refused():
    with pytest.raises(ValidationError):
        _prov(source_chars_total=-1, source_chars_read=0, char_offset=0)
