"""The prompt is part of the provenance, and it cannot be claimed — only computed.

`producing_process` is a NAME ("claude-sonnet-4-5/extractor@0.9.0"). Names drift silently: two
runs whose strings match can have used different instructions, and nothing in the record would
show it. `process_fingerprint` is the executable thing itself — D-033's rule ("a corpus is
defined by its query, not its name") applied one level up.
"""
import hashlib
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from lit2db.contracts.provenance import LiteratureProvenance, process_fingerprint

TEMPLATE = "Extract every terpene synthase. Quote the source verbatim for each value."


def _prov(**kw):
    base = dict(
        source_id="PMC12298776",
        retrieval_timestamp=datetime(2026, 7, 26, tzinfo=timezone.utc),
        producing_process="claude-sonnet-4-5/extractor@0.11.0",
        verbatim_quote="the enzyme converts FPP to pentalenene",
        char_offset=4200,
    )
    base.update(kw)
    return LiteratureProvenance(**base)


def test_the_fingerprint_is_the_sha256_of_the_template():
    assert process_fingerprint(TEMPLATE) == hashlib.sha256(TEMPLATE.encode()).hexdigest()
    assert len(process_fingerprint(TEMPLATE)) == 64


def test_a_prompt_cannot_change_without_the_fingerprint_changing():
    """The entire point. Even one character of drift is visible in the record."""
    before = process_fingerprint(TEMPLATE)
    after = process_fingerprint(TEMPLATE + " Prefer the abstract.")
    assert before != after
    # And a whitespace-only edit still counts — it can change model behaviour.
    assert process_fingerprint(TEMPLATE) != process_fingerprint(TEMPLATE + "\n")


def test_the_same_template_always_fingerprints_the_same():
    assert process_fingerprint(TEMPLATE) == process_fingerprint(TEMPLATE)


def test_provenance_accepts_a_computed_fingerprint():
    p = _prov(process_fingerprint=process_fingerprint(TEMPLATE))
    assert p.process_fingerprint == process_fingerprint(TEMPLATE)


def test_it_stays_optional_so_existing_records_remain_valid():
    assert _prov().process_fingerprint is None


@pytest.mark.parametrize("bad", ["v2", "prompt-1.3", "extractor@0.9.0", "abc123",
                                 hashlib.sha256(b"x").hexdigest()[:32], "Z" * 64])
def test_a_hand_written_label_is_refused(bad):
    """Accepting one would reproduce the exact defect the field removes: a provenance string
    that looks precise, is trusted, and can be bumped without the prompt changing."""
    with pytest.raises(ValidationError, match="SHA256"):
        _prov(process_fingerprint=bad)


def test_an_uppercase_digest_is_normalised_not_rejected():
    digest = process_fingerprint(TEMPLATE)
    assert _prov(process_fingerprint=digest.upper()).process_fingerprint == digest


def test_two_runs_with_the_same_process_name_but_different_prompts_are_distinguishable():
    """The failure this makes impossible."""
    a = _prov(process_fingerprint=process_fingerprint("Extract enzymes."))
    b = _prov(process_fingerprint=process_fingerprint("Extract enzymes and their substrates."))
    assert a.producing_process == b.producing_process
    assert a.process_fingerprint != b.process_fingerprint
