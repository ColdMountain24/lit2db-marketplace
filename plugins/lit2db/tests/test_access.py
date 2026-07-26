"""Tests for Stage-1 legal access resolution and the manual-acquisition queue.

Two properties carry weight here:
  * **OA version gates auto-accept** (D-026). A repository copy is often a submitted (pre-review)
    or accepted (pre-copyedit) manuscript, and numbers move in peer review. Treating a preprint
    value as the version of record is a silent provenance error — the class of failure this
    project exists to catch.
  * **Access resolution fails CLOSED.** A failed lookup means "unknown", never "no OA exists";
    otherwise a network blip quietly reclassifies open papers as unreachable.
"""
import os, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "mcp"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db_mcp import server as S

def _fn(t): return getattr(t, "fn", t)
resolve, rank = _fn(S.resolve_access), _fn(S.rank_manual_queue)


# --- version gating (D-026) ------------------------------------------------------------
@pytest.mark.parametrize("version,ok", [
    ("publishedVersion", True),
    ("acceptedVersion", False),
    ("submittedVersion", False),
    ("", False), (None, False), ("nonsense", False),
])
def test_only_the_version_of_record_may_auto_accept(version, ok):
    assert S.can_auto_accept_version(version) is ok


def test_version_rank_is_ordered():
    r = S.VERSION_RANK
    assert r["publishedVersion"] > r["acceptedVersion"] > r["submittedVersion"]


# --- fail-closed access resolution -----------------------------------------------------
def test_missing_doi_needs_manual():
    r = resolve("")
    assert r["ok"] is False and r["needs_manual"] is True


def test_missing_email_is_explained_not_silently_failed(monkeypatch):
    monkeypatch.setattr(S, "CONTACT_EMAIL", "")
    r = resolve("10.1234/x")
    assert r["ok"] is False and "LIT2DB_CONTACT_EMAIL" in r["error"]
    # and it must not imply the email is a credential
    assert "unlocks no paywalled content" in r["error"]


def test_lookup_failure_is_unknown_not_closed(monkeypatch):
    monkeypatch.setattr(S, "_get_json", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    r = resolve("10.1234/x", email="a@b.edu")
    assert r["ok"] is False and r["needs_manual"] is True
    assert "not 'closed'" in r["error"]


def test_no_oa_location_routes_to_manual(monkeypatch):
    monkeypatch.setattr(S, "_get_json",
                        lambda *a, **k: {"oa_status": "closed", "oa_locations": [], "title": "T"})
    r = resolve("10.1234/x", email="a@b.edu")
    assert r["ok"] is True and r["is_oa"] is False and r["needs_manual"] is True


def test_preprint_copy_is_resolved_but_not_auto_acceptable(monkeypatch):
    monkeypatch.setattr(S, "_get_json", lambda *a, **k: {
        "oa_status": "green", "title": "T",
        "best_oa_location": {"url": "https://repo/x.pdf", "host_type": "repository",
                             "version": "submittedVersion", "license": None},
        "oa_locations": [{"url": "https://repo/x.pdf"}]})
    r = resolve("10.1234/x", email="a@b.edu")
    assert r["is_oa"] is True and r["needs_manual"] is False
    assert r["best"]["auto_acceptable"] is False   # the whole point


def test_published_copy_is_auto_acceptable(monkeypatch):
    monkeypatch.setattr(S, "_get_json", lambda *a, **k: {
        "oa_status": "hybrid", "title": "T",
        "best_oa_location": {"url": "https://pub/x.pdf", "host_type": "publisher",
                             "version": "publishedVersion", "license": "cc-by"},
        "oa_locations": [{"url": "https://pub/x.pdf"}]})
    assert resolve("10.1234/x", email="a@b.edu")["best"]["auto_acceptable"] is True


# --- manual-acquisition ranking --------------------------------------------------------
ITEMS = [
    {"doi": "10.1/a", "title": "A new terpene synthase from Streptomyces", "year": 2025, "cited_by": 3},
    {"doi": "10.1/b", "title": "Unrelated soil survey", "abstract": "no relevant content", "year": 2025, "cited_by": 400},
    {"doi": "10.1/c", "title": "Old work", "abstract": "terpene synthase mentioned once", "year": 2015, "cited_by": 0},
]

def test_term_hits_in_title_outrank_raw_citations():
    """A highly-cited irrelevant paper must not outrank an on-topic one."""
    q = rank(ITEMS, terms=["terpene synthase"])["queue"]
    assert q[0]["doi"] == "10.1/a"


def test_title_hits_outweigh_abstract_hits():
    q = rank(ITEMS, terms=["terpene synthase"])["queue"]
    by = {r["doi"]: r for r in q}
    assert by["10.1/a"]["score"] > by["10.1/c"]["score"]


def test_ranking_is_auditable():
    """Every item explains itself — an unauditable ranking just hides a decision."""
    for r in rank(ITEMS, terms=["terpene synthase"])["queue"]:
        assert set(r["why"]) == {"terms_in_title", "terms_in_abstract", "recency", "citation_component"}


def test_no_terms_falls_back_and_says_so():
    out = rank(ITEMS, terms=[])
    assert out["note"] and out["queue"][0]["doi"] == "10.1/b"   # citations+recency only


def test_top_n_and_empty_input():
    assert len(rank(ITEMS, terms=["terpene"], top_n=2)["queue"]) == 2
    assert rank([], terms=["x"])["n"] == 0
