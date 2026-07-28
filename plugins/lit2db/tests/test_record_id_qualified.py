"""A record id is qualified by its source, so two papers cannot collide (D-091).

Extractors number records per paper, so `cpd1` means "the first compound in SOME paper" and two
papers collide by construction. This stopped being prospective on the compound pilot's fourth
paper: sulfadixiamycins A and B — composite 1.000, supported by the adversarial judge — were
refused because `cpd1`/`cpd2` were already held by corvol ethers A and B from a different paper.

The tests below pin both halves:
  * cross-source ids no longer collide, and both papers' records are written;
  * the collision refusal SURVIVES, aimed at what it can still catch — two different records
    under one id from ONE source, which `merge_passes` genuinely produces.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.output import query, upsert  # noqa: E402


def record(record_id: str, source_id: str, compound: str, quote: str | None = None):
    """A minimal record that clears the gate, so these tests isolate identity."""
    quote = quote or f"we isolated {compound} from the culture broth"
    return {
        "record_id": record_id,
        "entity_type": "bacterial_terpenoid_compound",
        # D-079: an unjudged record is not a supported one, so the fixture must carry a verdict
        # or these tests would be measuring the judge veto instead of record identity.
        "judge_verdict": "supported",
        "fields": [{
            "field_name": "compound_name",
            "value": compound,
            "provenance": {
                "source_id": source_id,
                "retrieval_timestamp": datetime(2026, 7, 28, tzinfo=timezone.utc).isoformat(),
                "producing_process": "test/extractor@0.37.0",
                "verbatim_quote": quote,
                "char_offset": 0,
                "source_status": "active",
                "kind": "literature",
            },
            "confidence": 1.0,
            "route": "auto_accept",
            "contradiction_search": "clean",
        }],
    }


def write(db, rec):
    return upsert(rec, composite_confidence=1.0, db_path=str(db), autoaccept=0.95)


# --- the pilot's collision, which must now succeed -------------------------------------------

def test_two_papers_numbering_from_one_both_write(tmp_path):
    """The exact shape that cost two verified compounds: both papers start at cpd1."""
    db = tmp_path / "t.db"
    a = write(db, record("cpd1", "DOI_corvol", "corvol ether A"))
    b = write(db, record("cpd1", "DOI_sulfa", "sulfadixiamycin A"))
    assert a["written"] and b["written"], (a, b)
    assert a["record_id"] == "DOI_corvol:cpd1"
    assert b["record_id"] == "DOI_sulfa:cpd1"
    assert len(query(db_path=str(db))["records"]) == 2


def test_the_local_id_is_still_reported(tmp_path):
    """"The first compound in this paper" is a true fact about the source, so it is not lost."""
    r = write(tmp_path / "t.db", record("cpd1", "DOI_corvol", "corvol ether A"))
    assert r["local_record_id"] == "cpd1"


def test_five_records_across_two_papers_all_survive(tmp_path):
    """The pilot in miniature: 2 + 3 compounds, ids cpd1..cpd2 and cpd1..cpd3."""
    db = tmp_path / "t.db"
    for i, c in enumerate(["corvol ether A", "corvol ether B"], 1):
        assert write(db, record(f"cpd{i}", "DOI_corvol", c))["written"]
    for i, c in enumerate(["sulfadixiamycin A", "sulfadixiamycin B", "sulfadixiamycin C"], 1):
        assert write(db, record(f"cpd{i}", "DOI_sulfa", c))["written"]
    assert len(query(db_path=str(db))["records"]) == 5


# --- the refusal that must survive ------------------------------------------------------------

def test_two_different_records_under_one_id_from_ONE_source_are_still_refused(tmp_path):
    """Qualification cannot help here — same source, same local id — so the denial stands.
    This is the `merge_passes` hazard: 15 records under 11 ids on a real paper."""
    db = tmp_path / "t.db"
    assert write(db, record("cpd1", "PMC1", "corvol ether A"))["written"]
    second = write(db, record("cpd1", "PMC1", "corvol ether B"))
    assert not second["written"] and second["decision"] == "deny"
    assert "already held by a DIFFERENT record" in second["reasons"][0]
    assert len(query(db_path=str(db))["records"]) == 1


def test_rewriting_the_same_record_is_idempotent(tmp_path):
    """A resumed leg re-writes what it already wrote; that must not read as a collision."""
    db = tmp_path / "t.db"
    rec = record("cpd1", "PMC1", "corvol ether A")
    assert write(db, rec)["written"]
    assert write(db, rec)["written"]
    assert len(query(db_path=str(db))["records"]) == 1


def test_a_record_citing_two_sources_is_refused(tmp_path):
    """A record belongs to one source, so one that does not cannot be identified by one."""
    rec = record("cpd1", "PMC1", "corvol ether A")
    rec["fields"].append(record("cpd1", "PMC2", "corvol ether B")["fields"][0])
    r = write(tmp_path / "t.db", rec)
    assert not r["written"] and "different sources" in r["reasons"][0]


def test_the_collision_reason_names_the_case_it_can_still_catch(tmp_path):
    """The old message told the reader to qualify the id. It is qualified now, so a message
    saying that again would send them to a fix already applied."""
    db = tmp_path / "t.db"
    write(db, record("cpd1", "PMC1", "corvol ether A"))
    reason = write(db, record("cpd1", "PMC1", "corvol ether B"))["reasons"][0]
    assert "WITHIN one source" in reason
