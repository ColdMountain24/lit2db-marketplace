"""The lean run (D-208): a stripped harness, the source pasted in, one read and one check.

Measured 2026-10-06 on the first review wave: a headless call that only says "ok" costs about
50,000 tokens with the full harness and about 12,000 stripped, while a paper is about 15,000.
These tests pin what the lean switches change, and that the fat run (the default) is untouched.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

run_wave = pytest.importorskip("run_wave")
from lit2db.fuse import Fuse  # noqa: E402
from test_wave_accounting import _ready_to_verify, _reply  # noqa: E402


def test_the_fat_run_adds_no_flags(monkeypatch):
    monkeypatch.setattr(run_wave, "_STRIPPED", False)
    assert run_wave.harness_flags("") == []


def test_the_stripped_harness_drops_mcp_settings_and_skills(monkeypatch):
    monkeypatch.setattr(run_wave, "_STRIPPED", True)
    f = run_wave.harness_flags("")
    assert "--strict-mcp-config" in f and "--disable-slash-commands" in f
    assert f[f.index("--setting-sources") + 1] == ""
    assert f[f.index("--tools") + 1] == ""
    assert run_wave.harness_flags(None)[-1] == "Read,Write", "a tool-using call keeps its tools"


def test_the_pasted_source_is_full_txt_verbatim(tmp_path):
    (tmp_path / "full.txt").write_text("Quote me exactly: 12.4 ± 0.3 μM.")
    block = run_wave.source_block(tmp_path)
    assert "Quote me exactly: 12.4 ± 0.3 μM." in block, (
        "full.txt stays the coordinate system: a quote copied from the block must be in full.txt")


def test_an_inline_pass_is_written_from_the_reply(tmp_path, monkeypatch):
    store = tmp_path / "stores" / "PMC1"
    store.mkdir(parents=True)
    (store / "full.txt").write_text("the accession is WP_1")
    (tmp_path / "extract.md").write_text("{SPEC_CONTEXT} read {STORE}, write {OUT_DIR}/{WRITES}")
    seen = {}

    def fake(prompt, model, **k):
        seen.update(k, prompt=prompt)
        return _reply(True, json.dumps({"records": [{"record_id": "r1", "fields": []}]}))
    monkeypatch.setattr(run_wave, "call_agent", fake)
    cfg = {"stores": str(tmp_path / "stores"), "extract_prompt": str(tmp_path / "extract.md"),
           "models": ["sonnet"], "source_inline": True}
    passes, failures, completed = run_wave.run_passes("PMC1", cfg, tmp_path / "run" / "PMC1",
                                                      Fuse(label="t"))
    assert completed == [True] and passes[0][0]["record_id"] == "r1" and not failures
    assert seen["tools"] == "", "a pasted source needs no tool"
    assert "the accession is WP_1" in seen["prompt"]
    assert (tmp_path / "run" / "PMC1" / "pass1" / "pass1.json").exists()


def _check_reply(ids, verdict="SUPPORTED"):
    return json.dumps({"contradiction_search": "clean", "contradictions": [],
                       "verdicts": [{"record_id": i, "verdict": verdict, "reasoning": "stated"}
                                    for i in ids]})


def test_one_check_hunts_and_judges_in_a_single_call(tmp_path, monkeypatch):
    cfg, out = _ready_to_verify(tmp_path, monkeypatch)
    cfg.update(judge_hunts=True, judge_audit_fraction=1.0)
    calls = []

    def fake(prompt, model, **k):
        calls.append(k.get("label"))
        return _reply(True, _check_reply([f"r{i}" for i in range(3)]))
    monkeypatch.setattr(run_wave, "call_agent", fake)
    r = run_wave._do_paper("PMC1", cfg, out, Fuse(label="w"))
    assert r["status"] == "done"
    assert calls == ["PMC1 check"], f"one verification call per paper, got {calls}"
    hunt = json.loads((out / "PMC1" / "hunter.json").read_text())
    assert set(hunt["state_by_record"].values()) == {"clean"}


def test_a_one_check_that_never_ran_leaves_the_paper_unscored(tmp_path, monkeypatch):
    cfg, out = _ready_to_verify(tmp_path, monkeypatch)
    cfg.update(judge_hunts=True, judge_audit_fraction=1.0)
    monkeypatch.setattr(run_wave, "call_agent", lambda *a, **k: _reply(False))
    r = run_wave._do_paper("PMC1", cfg, out, Fuse(label="w"))
    assert r["status"] == "incomplete"
    assert not (out / "PMC1" / "scored.json").exists()
