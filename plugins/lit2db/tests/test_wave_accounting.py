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


def test_headline_is_first_time_content_however_it_arrived():
    """D-070, amending D-065. The measured shape of a real extraction pass.

    D-065 defined the headline as input + output. Measurement then showed that with caching on
    a source document is NEVER billed as input — it arrives as cache_write — so that headline
    structurally excluded the paper being read. The distinction that matters is first-time
    versus re-read, not input versus cache.
    """
    acc = RunAccount(label="t")
    acc.record(_usage(50, 24_236, 237_749, 47_959), unit="PMC1", stage="extract")

    assert acc.work_tokens() == 72_245, "input + cache_write + output (D-070)"
    assert acc.reread_tokens() == 237_749, "re-reads are reported, never folded into the headline"
    assert sum(acc.totals().values()) == 309_994

    # The old definition would have reported 24,286 — excluding a 47,959-token document read.
    assert acc.work_tokens() > 2 * (acc.totals()["input"] + acc.totals()["output"])


def test_a_document_read_once_is_counted_even_with_no_input_tokens():
    """The exact failure D-070 fixes: input 138 against cache_write 351,862 on a real run.
    An input-only headline says the pipeline read almost nothing."""
    acc = RunAccount(label="t")
    acc.record(_usage(138, 155_132, 2_982_482, 351_862), unit="PMC1", stage="extract")
    assert acc.work_tokens() == 507_132
    assert acc.work_tokens() > 3 * (138 + 155_132)


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
    assert block["headline_work_tokens"] == 72_245
    assert block["reread_tokens"] == 237_749
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


# --- 4. one paper may not kill a wave --------------------------------------------------
def test_a_paper_that_raises_is_recorded_and_the_wave_continues(tmp_path, monkeypatch):
    """Measured: PMC10046388 raised out of merge_passes and the traceback ended the run on
    paper 1 of 2, after paying for three extraction passes. Unlike a fuse trip it leaves no
    resumable state, because the paper never reaches scored.json."""
    monkeypatch.setattr(run_wave, "_do_paper",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("no identity field")))
    r = run_wave.do_paper("PMC1", {}, tmp_path, Fuse(label="w"))
    assert r["status"] == "error"
    assert "no identity field" in r["error"]
    assert r["questions"][0]["kind"] == "paper_failed", "the researcher must see it"
    assert r["n_records"] == 0 and r["n_written"] == 0


def test_a_tripped_fuse_still_stops_the_whole_run(tmp_path, monkeypatch):
    """The fuse is the safety device. Swallowing it per-paper would turn a runaway-loop brake
    into a hiccup, which is strictly worse than having no fuse — it would look like one."""
    from lit2db.fuse import FuseExceeded

    def boom(*a, **k):
        raise FuseExceeded("max_tokens_total", 1, 2, "w")

    monkeypatch.setattr(run_wave, "_do_paper", boom)
    with pytest.raises(FuseExceeded):
        run_wave.do_paper("PMC1", {}, tmp_path, Fuse(label="w"))


def test_an_errored_paper_is_not_counted_as_done(tmp_path):
    err = {"paper": "PMC1", "status": "error", "n_records": 0, "n_written": 0,
           "failures": [], "dropped": [], "questions": []}
    ok = {"paper": "PMC2", "status": "done", "n_records": 3, "n_written": 1,
          "failures": [], "dropped": [], "questions": []}
    run_wave._write_manifest(tmp_path, {}, ["PMC1", "PMC2"], [err, ok], [],
                             Fuse(label="w"), complete=True)
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert m["n_done"] == 1, "a failed paper must not inflate the completion count"
    assert m["n_records"] == 3


# --- 5. one paper may not run forever -------------------------------------------------
def test_per_call_timeout_scales_with_the_document(tmp_path):
    """A flat 1800s was applied to a 1.7kB store and a 149kB store alike."""
    stores = tmp_path / "s"
    for name, size in (("small", 2_000), ("big", 150_000)):
        d = stores / name
        d.mkdir(parents=True)
        (d / "full.txt").write_text("x" * size)
    cfg = {"stores": str(stores)}
    small = run_wave._paper_budget(cfg, "small")
    big = run_wave._paper_budget(cfg, "big")
    assert small < big, "a 2kB paper must not get the same allowance as a 150kB one"
    assert big <= 1800, "and the ceiling still caps it"


def test_a_missing_store_still_yields_a_budget(tmp_path):
    """Never crash computing a timeout — a missing store is the ingest stage's problem."""
    assert run_wave._paper_budget({"stores": str(tmp_path)}, "absent") > 0


def test_a_timeout_is_not_retried(tmp_path, monkeypatch):
    """The real fix for the hang. One pass hit the 1800s timeout and retries=3 would have burned
    90 MINUTES before the driver gave up — silently, because a driver waiting is
    indistinguishable from a driver working.

    The cause was a prompt sending the agent grepping a document that fits in context several
    times over: a DETERMINISTIC hang, so the retry buys an identical wait at full price. v0.30.0
    answered this with a wall-clock paper deadline, which then skipped stages without running
    them (see below). Not retrying bounds the paper through the term that was actually unbounded.
    """
    import subprocess as sp
    attempts = []

    def always_timeout(*a, **k):
        attempts.append(k.get("timeout"))
        raise sp.TimeoutExpired(cmd="claude", timeout=k.get("timeout", 1))

    monkeypatch.setattr(run_wave.subprocess, "run", always_timeout)
    monkeypatch.setattr(run_wave.time, "sleep", lambda *_: None)

    r = run_wave.call_agent("p", "haiku", label="t", cwd=tmp_path, fuse=Fuse(label="w"),
                            timeout=600, retries=3)
    assert r["ok"] is False
    assert len(attempts) == 1, "a timeout is deterministic — trying it again just waits again"
    assert "timeout" in r["text"], "and it must say so rather than failing blank"


def test_a_transient_exit_is_still_retried(tmp_path, monkeypatch):
    """The boundary: retries exist for failures where trying again is a DIFFERENT event.
    Removing them for timeouts must not remove them for a non-zero exit."""
    calls = []

    class R:
        returncode, stdout, stderr = 1, "", "transient boom"

    monkeypatch.setattr(run_wave.subprocess, "run", lambda *a, **k: calls.append(1) or R())
    monkeypatch.setattr(run_wave.time, "sleep", lambda *_: None)
    run_wave.call_agent("p", "haiku", label="t", cwd=tmp_path, fuse=Fuse(label="w"),
                        timeout=600, retries=3)
    assert len(calls) == 3, "a transient failure still gets its retries"


# --- 6. a stage that never ran may not be recorded as a stage that found nothing --------
def _ready_to_verify(tmp_path, monkeypatch, n_records=3):
    """A paper with its extraction passes already on disk, poised at the judge stage."""
    store = tmp_path / "stores" / "PMC1"
    store.mkdir(parents=True)
    (store / "full.txt").write_text("some source text")
    (store / "sections.json").write_text("[]")
    for name in ("extract.md", "judge.md", "hunter.md"):
        (tmp_path / name).write_text("{STORE} {OUT_DIR} {WRITES} {CLAIM} {VALUES}")

    out = tmp_path / "run"
    d = out / "PMC1" / "pass1"
    d.mkdir(parents=True)
    (d / "pass1.json").write_text(json.dumps({"records": [{"record_id": "r0"}]}))

    monkeypatch.setattr(run_wave, "merge_passes", lambda passes, identity_fields: {
        "records": [{"record_id": f"r{i}", "entity_type": "enzyme", "fields": []}
                    for i in range(n_records)],
        "identity_tiers": {}, "alignment": [], "ensemble": {}})

    cfg = {"stores": str(tmp_path / "stores"), "extract_prompt": str(tmp_path / "extract.md"),
           "judge_prompt": str(tmp_path / "judge.md"), "hunter_prompt": str(tmp_path / "hunter.md"),
           "models": ["haiku"], "identity_fields": ["accession"], "identity_primary": "accession",
           "judge_model": "haiku", "hunter_model": "haiku", "weights_key": "numeric",
           "db_path": str(tmp_path / "db.sqlite"), "auto_accept_threshold": 0.95,
           "run_timestamp": "2026-07-27T00:00:00Z", "producing_process": "test"}
    return cfg, out


def _reply(ok, text=""):
    return {"ok": ok, "text": text, "tokens": 0, "work_tokens": 0,
            "streams": {s: 0 for s in STREAMS}, "attempts": 1}


def test_a_skipped_judge_leaves_the_paper_unscored(tmp_path, monkeypatch):
    """THE DEFECT, EXACTLY AS IT HAPPENED. Every judge and hunter call returned without being
    invoked (the paper deadline had expired); the driver read each missing verdict as an
    absence, scored the paper, and marked it done — 77 records recorded as verified-and-denied
    when the verification had never run, and no resume would ever revisit them."""
    cfg, out = _ready_to_verify(tmp_path, monkeypatch)
    monkeypatch.setattr(run_wave, "call_agent", lambda *a, **k: _reply(ok=False))

    r = run_wave._do_paper("PMC1", cfg, out, Fuse(label="w"))

    assert r["status"] == "incomplete", "a paper missing a stage is not a finished paper"
    assert not (out / "PMC1" / "scored.json").exists(), (
        "absence of scored.json IS the resume mechanism — writing one strands the paper")
    assert any("judge" in s for s in r["skipped_stages"])
    assert r["questions"][0]["kind"] == "verification_skipped", "the researcher must see it"
    assert r["n_written"] == 0


def test_an_incomplete_paper_is_retried_by_the_next_leg(tmp_path, monkeypatch):
    """The guard is only worth anything if resume actually picks the paper back up."""
    cfg, out = _ready_to_verify(tmp_path, monkeypatch)
    monkeypatch.setattr(run_wave, "call_agent", lambda *a, **k: _reply(ok=False))
    run_wave._do_paper("PMC1", cfg, out, Fuse(label="w"))

    todo = [p for p in ["PMC1"] if not (out / p / "scored.json").exists()]
    assert todo == ["PMC1"]
    assert (out / "PMC1" / "pass1" / "pass1.json").exists(), (
        "and it resumes at the STAGE — the finished extraction passes are still on disk")


def test_a_judge_that_answered_unusably_is_scored_not_retried(tmp_path, monkeypatch):
    """The other side of the line, and the reason it is drawn at 'did the call execute'.

    A reply that cannot be parsed IS a result: it fails closed, the raw text is on disk, and it
    is catalogued. Retrying it would loop forever on a paper whose replies are reproducibly
    unparseable — a paper that silently never finishes is worse than an auditable deny.
    """
    cfg, out = _ready_to_verify(tmp_path, monkeypatch)
    monkeypatch.setattr(run_wave, "call_agent", lambda *a, **k: _reply(ok=True, text="I refuse"))

    r = run_wave._do_paper("PMC1", cfg, out, Fuse(label="w"))

    assert r["status"] == "done"
    assert (out / "PMC1" / "scored.json").exists()
    assert (out / "PMC1" / "judge" / "r0.raw.txt").read_text() == "I refuse", (
        "and the unusable reply is preserved so the deny can be audited")
    assert any(q["kind"] == "no_verdict" for q in r["questions"])


def test_a_skipped_hunter_also_blocks_scoring(tmp_path, monkeypatch):
    """The hunter failing wholesale leaves every field 'not_run', which the gate blocks — a
    correct denial for a reason that is a driver failure, not a property of the paper."""
    cfg, out = _ready_to_verify(tmp_path, monkeypatch)

    def only_hunter_fails(*a, **k):
        return _reply(ok=False) if "hunter" in (k.get("label") or "") else _reply(
            ok=True, text=json.dumps({"verdict": "SUPPORTED"}))

    monkeypatch.setattr(run_wave, "call_agent", only_hunter_fails)
    r = run_wave._do_paper("PMC1", cfg, out, Fuse(label="w"))

    assert r["status"] == "incomplete"
    assert any("hunter" in s for s in r["skipped_stages"])
    assert not (out / "PMC1" / "scored.json").exists()


def test_incomplete_papers_are_named_in_the_manifest(tmp_path):
    inc = {"paper": "PMC1", "status": "incomplete", "n_records": 0, "n_written": 0,
           "failures": [], "dropped": [], "questions": []}
    ok = {"paper": "PMC2", "status": "done", "n_records": 3, "n_written": 1,
          "failures": [], "dropped": [], "questions": []}
    run_wave._PRIOR_TOKENS.clear()
    run_wave._write_manifest(tmp_path, {}, ["PMC1", "PMC2"], [inc, ok], [],
                             Fuse(label="w"), complete=True)
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert m["n_done"] == 1, "an unverified paper must not inflate the completion count"
    assert m["papers_unverified_left_for_retry"] == ["PMC1"]


# --- 7. a measured token block is never overwritten by an emptier one -------------------
def test_a_resumed_leg_does_not_erase_the_earlier_legs_tokens(tmp_path):
    """`_recover_done` restores a resumed paper's counts but explicitly cannot restore its
    tokens. The manifest on disk still HELD them — and every resumed leg overwrote that block
    with its own, so a wave resumed for one last paper published the cost of one paper in the
    field a reader takes for the cost of the wave."""
    leg1 = Fuse(label="w", account=RunAccount(label="w"))
    leg1.record(_usage(50, 24_236, 237_749, 47_959), unit="PMC1", stage="extract")
    run_wave._PRIOR_TOKENS.clear()
    run_wave._write_manifest(tmp_path, {}, ["PMC1", "PMC2"], [], [], leg1, complete=False)
    assert json.loads((tmp_path / "manifest.json").read_text())["tokens"]["instrumented"]

    # A NEW PROCESS resumes the wave and runs nothing that costs anything.
    run_wave._PRIOR_TOKENS.clear()
    run_wave._write_manifest(tmp_path, {}, ["PMC1", "PMC2"], [], [],
                             Fuse(label="w", account=RunAccount(label="w")), complete=True)
    m = json.loads((tmp_path / "manifest.json").read_text())

    assert len(m["tokens_prior_legs"]) == 1, "the earlier leg's measurement must survive"
    assert m["tokens_prior_legs"][0]["headline_work_tokens"] == 72_245
    assert m["tokens"]["headline_work_tokens"] == 0, "this leg really did spend nothing"
    assert "_tokens_note" in m, "and the manifest must say the two are not summed"


def test_an_uninstrumented_leg_cannot_erase_a_measured_one(tmp_path):
    """The worst shape of the clobber: a block saying `instrumented: false` replacing real
    measurement, so the wave reads as never having been accounted at all."""
    leg1 = Fuse(label="w", account=RunAccount(label="w"))
    leg1.record(_usage(1, 2, 3, 4), unit="PMC1", stage="extract")
    run_wave._PRIOR_TOKENS.clear()
    run_wave._write_manifest(tmp_path, {}, ["PMC1"], [], [], leg1, complete=False)

    run_wave._PRIOR_TOKENS.clear()
    run_wave._write_manifest(tmp_path, {}, ["PMC1"], [], [], Fuse(label="w"), complete=True)
    m = json.loads((tmp_path / "manifest.json").read_text())

    assert m["tokens"]["instrumented"] is False
    assert m["tokens_prior_legs"][0]["by_stream"]["input"] == 1


def test_the_running_leg_does_not_append_itself_once_per_paper(tmp_path):
    """`_write_manifest` runs after EVERY paper. Re-reading the file each time would append
    this leg's own growing block to its own history, inventing legs that never existed."""
    fuse = Fuse(label="w", account=RunAccount(label="w"))
    run_wave._PRIOR_TOKENS.clear()
    for _ in range(4):
        fuse.record(_usage(10, 10, 10, 10), unit="PMC1", stage="extract")
        run_wave._write_manifest(tmp_path, {}, ["PMC1"], [], [], fuse, complete=False)
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert m["tokens_prior_legs"] == [], "one process is one leg, however often it writes"
