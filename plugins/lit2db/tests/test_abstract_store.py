"""Abstract-only stores (C9 / D-087) — a declared evidence standard, not a truncation.

Most of the sources behind the compound-side reference database cannot be had in full: a
census of its DOI column found 82 of 435 with retrievable full text and 206 more reachable
only as abstracts. The ratified rule is to extract from the abstract where that is all there
is, and to record on every record which it was.

The property under test: an abstract-only store keeps the SAME coordinate contract as a
full-text one -- `full_text` is the authority and offsets round-trip -- while declaring that
the document itself is only an abstract. That declaration must come from the store, because
the alternative is an extractor guessing at its own evidence standard.

The distinction these tests defend is the one D-038 was written about. A pilot once read 26%
of each paper and recorded nothing about it, so values were verified against a different
document than the one they cited. Retention answers "did you read all of what you had";
`source_text_scope` answers "what did you have". An abstract-only store read in full is
retained_fraction 1.0 AND abstract_only, and both are true at once.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.store import (  # noqa: E402
    build_from_abstract, build_from_jats, find_spans, quote_at, section_of, write_store,
)

TITLE = "Hapalindole-type alkaloids from Fischerella sp."
ABSTRACT = ("Two new prenylated indole alkaloids were isolated from a cultured "
            "Fischerella sp., and their structures established by NMR.")


def _store(**kw):
    kw.setdefault("title", TITLE)
    kw.setdefault("abstract", ABSTRACT)
    kw.setdefault("source_id", "DOI:10.1021/np50107a021")
    return build_from_abstract(**kw)


# --- the coordinate contract, identical to a full-text store -------------------------------

def test_offsets_round_trip_through_full_text():
    """The whole point of the store: quote_at(offset) returns what was recorded there."""
    s = _store()
    hit = find_spans(s, "prenylated indole alkaloids")[0]
    assert quote_at(s, hit["start"], hit["end"]) == "prenylated indole alkaloids"


def test_full_text_is_the_authority_not_the_inputs():
    s = _store()
    assert s["full_text"].startswith(TITLE)
    assert ABSTRACT in s["full_text"]
    assert s["stats"]["chars"] == len(s["full_text"])


def test_abstract_is_a_locatable_section():
    s = _store()
    hit = find_spans(s, "Fischerella sp., and their")[0]
    assert section_of(s, hit["start"]) == "Abstract"


def test_title_is_included_but_is_not_inside_the_abstract_section():
    """A claim grounded in the title must not be reported as coming from the abstract."""
    s = _store()
    assert section_of(s, 0) is None
    abstract_section = [x for x in s["sections"] if x["title"] == "Abstract"][0]
    assert abstract_section["start"] >= len(TITLE)


def test_section_end_excludes_the_paragraph_separator():
    s = _store()
    section = [x for x in s["sections"] if x["title"] == "Abstract"][0]
    assert s["full_text"][section["end"] - 1] == "."


def test_store_writes_the_same_three_files(tmp_path):
    s = _store()
    paths = write_store(s, tmp_path)
    assert Path(paths["full_text"]).read_text(encoding="utf-8") == s["full_text"]
    assert Path(paths["sections"]).exists() and Path(paths["meta"]).exists()


def test_a_doi_source_id_is_sanitised_into_one_flat_directory(tmp_path):
    """Validation-slice sources are keyed by DOI, and a DOI contains a '/'.

    Unsanitised that would nest each store under a registrant directory; `write_store` maps it
    to a flat name instead. Recorded here because the mapping is many-to-one in principle, so
    two DOIs could in theory share a directory and silently overwrite each other -- the same
    shape as the known record-id collision. Checked against the real slice: all 435 cleaned
    DOIs map to 435 distinct directory names, so the hazard is latent, not live.
    """
    paths = write_store(_store(), tmp_path)
    written = Path(paths["dir"])
    assert written.parent == tmp_path                      # flat, not nested under "10.1021"
    assert "/" not in written.name
    import json as _json
    assert _json.loads(Path(paths["meta"]).read_text())["source_id"] == "DOI:10.1021/np50107a021"


# --- the declaration, which is the part that is new ----------------------------------------

def test_abstract_store_declares_its_scope():
    assert _store()["meta"]["source_text_scope"] == "abstract_only"


def test_jats_store_declares_the_other_scope():
    """Both builders declare, so `source_text_scope` is derivable rather than guessed."""
    jats = f"""<article><front><article-meta>
        <article-title>{TITLE}</article-title>
        <abstract><p>{ABSTRACT}</p></abstract>
      </article-meta></front><body><sec><p>Full body text here.</p></sec></body></article>"""
    assert build_from_jats(jats, "PMC1")["meta"]["source_text_scope"] == "full_text"


def test_declared_scope_survives_caller_supplied_meta():
    """A caller passing meta must not be able to overwrite the scope with a wrong value."""
    s = _store(meta={"doi": "10.1021/np50107a021", "source_text_scope": "full_text"})
    assert s["meta"]["source_text_scope"] == "abstract_only"
    assert s["meta"]["doi"] == "10.1021/np50107a021"


def test_an_abstract_read_whole_is_not_a_truncated_read():
    """D-038's two questions, kept apart.

    The abstract is the entire document available, so the retained fraction over THIS store is
    1.0. That is not a claim to have read the paper -- `source_text_scope` carries that -- and
    the pair is what stops an abstract-grounded value looking like a full-text one.
    """
    s = _store()
    assert s["stats"]["chars"] == len(s["full_text"])          # read all of what we had
    assert s["meta"]["source_text_scope"] == "abstract_only"   # and what we had was an abstract


# --- failing closed ------------------------------------------------------------------------

@pytest.mark.parametrize("title,abstract", [("", ""), ("   ", "\n\t "), ("", None)])
def test_empty_source_raises_rather_than_emitting_an_empty_store(title, abstract):
    """Same rule as the JATS builder: an empty store is indistinguishable, three stages later,
    from a source that genuinely contained nothing extractable."""
    with pytest.raises(ValueError, match="empty store"):
        build_from_abstract(title=title, abstract=abstract, source_id="DOI:10.0/x")


def test_title_only_is_allowed_because_it_is_still_readable_text():
    """Some indexed records carry a title and no abstract. That is thin, but it is not empty,
    and the record's scope still says abstract_only rather than pretending to more."""
    s = build_from_abstract(title=TITLE, abstract="", source_id="DOI:10.0/y")
    assert s["full_text"].startswith(TITLE)
    assert s["sections"] == []
    assert s["meta"]["source_text_scope"] == "abstract_only"
