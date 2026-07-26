"""Tests for the retraction / supersession check (blueprint 3, ratified addition D2).

The mapping is pure and tested offline. The one network test is opt-in, because a suite that
silently passes when the network is down would defeat the point of the check.

The load-bearing property here is FAIL-CLOSED: an unreachable Crossref must yield "unknown", never
"active". Reporting an unverified source as active turns the retraction gate into a no-op — the
quiet failure this whole layer exists to prevent.
"""
import os, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "mcp"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.contracts import SourceStatus
from lit2db_mcp import server as S

def _fn(t): return getattr(t, "fn", t)
check = _fn(S.check_retraction)


@pytest.mark.parametrize("relations,expected", [
    ([],                                  "active"),
    (None,                                "active"),
    (["correction"],                      "corrected"),
    (["corrigendum"],                     "corrected"),
    (["erratum"],                         "corrected"),
    (["new_version"],                     "superseded"),
    (["retraction"],                      "retracted"),
    (["withdrawal"],                      "retracted"),
    (["Retraction"],                      "retracted"),   # case-insensitive
    (["correction", "retraction"],        "retracted"),   # retraction absorbs correction
    (["retraction", "correction"],        "retracted"),   # ...in either order
    (["new_version", "correction"],       "superseded"),
])
def test_relation_mapping(relations, expected):
    assert S.status_from_relations(relations) == expected


def test_every_mapped_status_is_a_real_SourceStatus():
    """The mapping must not invent a status the contracts do not know about."""
    for rels in ([], ["correction"], ["new_version"], ["retraction"]):
        SourceStatus(S.status_from_relations(rels))


def test_unknown_relation_does_not_silently_retract_or_clear():
    assert S.status_from_relations(["expression_of_concern"]) == "active"
    assert S.status_from_relations(["correction", "expression_of_concern"]) == "corrected"


def test_missing_doi_fails_closed():
    """No DOI is not 'active' — it is 'we did not check'."""
    for bad in ("", "   ", None):
        r = check(bad)
        assert r["ok"] is False and r["status"] is None


def test_unreachable_lookup_fails_closed(monkeypatch):
    """A network failure must NOT be reported as active."""
    import urllib.request
    def boom(*a, **kw): raise OSError("network down")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    r = check("10.1234/whatever")
    assert r["ok"] is False and r["status"] is None
    assert "human review" in r["evidence"]


@pytest.mark.skipif(not os.environ.get("LIT2DB_NETWORK_TESTS"),
                    reason="set LIT2DB_NETWORK_TESTS=1 to hit Crossref")
def test_live_crossref_detects_a_known_retraction():
    r = check("10.1016/S0140-6736(97)11096-0")
    assert r["ok"] is True and r["status"] == "retracted"
