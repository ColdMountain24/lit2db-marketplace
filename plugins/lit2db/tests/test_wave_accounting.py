"""The wave driver must report what it spent, and a resumed wave must still describe the wave.

Three defects found in the artifacts of a real run (D-065, D-066), each of which made a number
read as something it was not:

  1. The four token streams were collapsed into one integer, so a figure that was 92% cache
     traffic was compared against projections built in input tokens.
  2. Papers finished on an earlier leg vanished from the manifest totals on resume.
  3. Extraction passes were re-run for a paper that died later in the pipeline.

Each is pinned here, because each was invisible from the code alone and only showed up as a
number that looked plausible.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from lit2db.accounting import STREAMS, RunAccount     # noqa: E402
from lit2db.fuse import Fuse                          # noqa: E402

run_wave = pytest.importorskip("run_wave")


# --- 1. the streams stay apart ---------------------------------------------------------
def _usage(inp, out, cr, cw):
    return {"input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}


def test_headline_is_new_content_not_the_collapsed_total():
    """The measured shape of a real pass: output is small, cache_read dwarfs everything."""
    acc = RunAccount(label="t")
    acc.record(_usage(50, 24_236, 237_749, 47_959), unit="PMC1", stage="extract")

    assert acc.work_tokens() == 24_286, "headline must be input + output only (D-065)"
    assert sum(acc.totals().values()) == 309_994

    # The distinction is the whole point: the collapsed number is >12x the headline, and it is
    # the collapsed number that was being compared against input-token projections.
    assert sum(acc.totals().values()) > 12 * acc.work_tokens()


def test_by_stage_says_what_to_cut():
    acc = RunAccount(label="t")
    acc.record(_usage(10, 1_000, 100_000, 20_000), unit="PMC1", stage="extract")
    acc.record(_usage(10, 100, 300_000, 40_000), unit="PMC1", stage="judge")

    by = acc.by_stage()
    assert set(by) == {"extract", "judge"}
    # Judge dominates the collapsed total while contributing almost no new content — exactly
    # the case that a single number cannot express and that decides where batching pays.
    assert sum(by["judge"].values()) > sum(by["extract"].values())
    assert by["judge"]["output"] < by["extract"]["output"]


def test_by_stage_returns_copies_not_the_live_buckets():
    acc = RunAccount(label="t")
    acc.record(_usage(1, 1, 1, 1), unit="u", stage="extract")
    acc.by_stage()["extract"]["input"] = 999_999
    assert acc.totals()["input"] == 1, "a manifest writer must not be able to mutate the account"


def test_manifest_tokens_block_is_present_and_split():
    fuse = Fuse(label="w", account=RunAccount(label="w"))
    fuse.record(_usage(50, 24_236, 237_749, 47_959), unit="PMC1", stage="extract")

    block = run_wave._tokens_block(fuse)
    assert block["instrumented"] is True
    assert block["headline_work_tokens"] == 24_286
    assert block["total_all_streams"] == 309_994
    assert set(block["by_stream"]) == set(STREAMS)
    assert block["per_paper_mean"]["measured_on_papers"] == 1


def test_uninstrumented_fuse_says_so_rather_than_reporting_zero():
    """An un-accounted run must be legible as un-accounted, not as a run that cost nothing."""
    block = run_wave._tokens_block(Fuse(label="w"))
    assert block["instrumented"] is False
    assert "headline_work_tokens" not in block


# --- 2. a resumed wave still describes the wave ----------------------------------------
def _scored(tmp, paper, n_records, n_written):
    d = tmp / paper
    d.mkdir(parents=True, exist_ok=True)
    gate = [{"record_id": f"r{i}", "composite": 0.9,
             "gate": {"written": i < n_written}} for i in range(n_records)]
    (d / "scored.json").write_text(json.dumps(
        {"records": [{"record_id": f"r{i}"} for i in range(n_records)], "gate": gate}))
    return d


def test_recover_done_rebuilds_a_finished_paper_from_disk(tmp_path):
    _scored(tmp_path, "PMC1", n_records=15, n_written=3)
    r = run_wave._recover_done(tmp_path, "PMC1")
    assert r["status"] == "done"
    assert (r["n_records"], r["n_written"]) == (15, 3)
    assert r["carried_from_disk"] is True, "carried work must be distinguishable from fresh work"


def test_recover_done_handles_the_unanimous_empty_result(tmp_path):
    """Zero records is a RESULT (a paper the screen admitted that reports no enzyme), not a
    failure — it must survive resume as a counted paper rather than disappearing."""
    d = tmp_path / "PMC2"
    d.mkdir()
    (d / "scored.json").write_text(json.dumps({"records": [], "gate": []}))
    r = run_wave._recover_done(tmp_path, "PMC2")
    assert r["status"] == "done" and r["n_records"] == 0


def test_recover_done_returns_none_on_unreadable_artifact(tmp_path):
    d = tmp_path / "PMC3"
    d.mkdir()
    (d / "scored.json").write_text("{not json")
    assert run_wave._recover_done(tmp_path, "PMC3") is None
    assert run_wave._recover_done(tmp_path, "PMC-absent") is None


def test_manifest_counts_carried_papers_and_labels_them(tmp_path):
    """The defect exactly as observed: two papers complete on disk, manifest said n_done 1."""
    _scored(tmp_path, "PMC1", 15, 3)
    _scored(tmp_path, "PMC2", 1, 0)
    carried = [run_wave._recover_done(tmp_path, p) for p in ("PMC1", "PMC2")]
    fresh = {"paper": "PMC3", "status": "done", "n_records": 4, "n_written": 1,
             "failures": [], "dropped": [], "questions": []}

    run_wave._write_manifest(tmp_path, {"x": 1}, ["PMC1", "PMC2", "PMC3"],
                             carried + [fresh], [], Fuse(label="w"), complete=True)
    m = json.loads((tmp_path / "manifest.json").read_text())

    assert m["n_done"] == 3, "resumed papers must count toward the wave, not vanish"
    assert m["n_records"] == 20 and m["n_written"] == 4
    assert m["n_done_carried_from_earlier_legs"] == 2, (
        "a resumed wave must not read as a cheap one — tokens cover only the legs that ran")


# --- 3. resume at the stage, not the paper ---------------------------------------------
def test_completed_extraction_passes_are_reused(tmp_path, monkeypatch):
    """A paper that died in judging keeps its three finished extractions.

    Re-running them is not merely wasteful: it REPLACES the ensemble the surviving artifacts
    came from, so the resumed paper is no longer the paper that was partly judged.
    """
    store = tmp_path / "stores" / "PMC1"
    store.mkdir(parents=True)
    (store / "full.txt").write_text("text")
    prompt = tmp_path / "p.md"
    prompt.write_text("{STORE} {OUT_DIR} {WRITES}")
    pdir = tmp_path / "run" / "PMC1"

    for i in (1, 2, 3):
        d = pdir / f"pass{i}"
        d.mkdir(parents=True)
        (d / f"pass{i}.json").write_text(json.dumps({"records": [{"record_id": f"p{i}"}]}))

    called = []
    monkeypatch.setattr(run_wave, "call_agent",
                        lambda *a, **k: called.append(k.get("label")) or
                        {"ok": True, "text": "", "tokens": 0, "work_tokens": 0,
                         "streams": {s: 0 for s in STREAMS}, "attempts": 1})

    cfg = {"stores": str(tmp_path / "stores"), "extract_prompt": str(prompt),
           "models": ["opus", "sonnet", "haiku"]}
    passes, failures, completed = run_wave.run_passes("PMC1", cfg, pdir, Fuse(label="w"))

    assert called == [], "no agent may be invoked when every pass is already on disk"
    assert all(completed) and not failures
    assert [p[0]["record_id"] for p in passes] == ["p1", "p2", "p3"]


def test_unreadable_pass_is_re_run_rather_than_trusted(tmp_path, monkeypatch):
    store = tmp_path / "stores" / "PMC1"
    store.mkdir(parents=True)
    prompt = tmp_path / "p.md"
    prompt.write_text("{STORE} {OUT_DIR} {WRITES}")
    pdir = tmp_path / "run" / "PMC1"
    d = pdir / "pass1"
    d.mkdir(parents=True)
    (d / "pass1.json").write_text("{truncated")

    def fake(*a, **k):
        out = pathlib.Path(str(k["cwd"])) / "pass1" / "pass1.json"
        out.write_text(json.dumps({"records": [{"record_id": "fresh"}]}))
        return {"ok": True, "text": "", "tokens": 0, "work_tokens": 0,
                "streams": {s: 0 for s in STREAMS}, "attempts": 1}

    monkeypatch.setattr(run_wave, "call_agent", fake)
    cfg = {"stores": str(tmp_path / "stores"), "extract_prompt": str(prompt),
           "models": ["opus"]}
    passes, failures, completed = run_wave.run_passes("PMC1", cfg, pdir, Fuse(label="w"))
    assert completed == [True] and passes[0][0]["record_id"] == "fresh"
