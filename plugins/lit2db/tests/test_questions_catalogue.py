"""The catalogue a researcher reads must survive a resume and must not double-count a re-run.

`QUESTIONS.jsonl` is rewritten whole on every manifest write. A resumed leg rebuilds finished
papers from `scored.json`, which never carried their questions, so the second leg of a wave
truncated away everything the first leg asked: a silent loss of the one artifact that says what
only a researcher can settle. The first BBB wave (2026-09-18) read the other way round, that the
file accumulates across runs, and the fix has to hold both: questions live beside their paper,
a re-scored paper replaces its own, and the catalogue is written distinct.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

run_wave = pytest.importorskip("run_wave")

CFG = {"models": ["sonnet"], "judge_model": "opus", "hunter_model": "sonnet",
       "auto_accept_threshold": 0.95, "judge_audit_fraction": 0.2,
       "producing_process": "test", "identity_primary": "x", "run_timestamp": "t"}


def _paper(out: pathlib.Path, name: str, questions: list, records: int = 1) -> None:
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "scored.json").write_text(json.dumps({"records": [{}] * records, "gate": []}))
    (d / "questions.json").write_text(json.dumps(questions))


def _catalogue(out: pathlib.Path) -> list:
    return [json.loads(line) for line in (out / "QUESTIONS.jsonl").read_text().splitlines()]


def test_a_resumed_leg_keeps_the_questions_the_first_leg_asked(tmp_path):
    q1 = {"paper": "P1", "kind": "scope_disagreement", "detail": "only one reading found it"}
    _paper(tmp_path, "P1", [q1])

    carried = run_wave._recover_done(tmp_path, "P1")
    assert carried["questions"] == [q1], (
        "a finished paper must hand its questions back on resume; rebuilding from scored.json "
        "alone dropped them, and the catalogue is rewritten from what it is handed")

    q2 = {"paper": "P2", "kind": "weak_identity", "detail": "aligned by order"}
    _paper(tmp_path, "P2", [q2])
    run_wave._write_manifest(tmp_path, CFG, ["P1", "P2"],
                             [carried, {"paper": "P2", "status": "done", "n_records": 1,
                                        "n_written": 0, "questions": [q2]}],
                             carried["questions"] + [q2],
                             run_wave.Fuse(label="t"), complete=True)
    assert _catalogue(tmp_path) == [q1, q2]


def test_the_same_question_twice_is_one_open_question(tmp_path):
    q = {"paper": "P1", "kind": "audit_disagreement", "detail": "r1"}
    _paper(tmp_path, "P1", [q])
    run_wave._write_manifest(tmp_path, CFG, ["P1"],
                             [{"paper": "P1", "status": "done", "n_records": 1, "n_written": 0,
                               "questions": [q]}],
                             [q, q], run_wave.Fuse(label="t"), complete=True)
    assert _catalogue(tmp_path) == [q], "a re-scored paper must not stand in the catalogue twice"
    assert json.loads((tmp_path / "manifest.json").read_text())["n_questions"] == 2, (
        "the manifest still records how many were raised this leg; the catalogue is what is open")


def test_two_papers_asking_the_same_kind_are_both_kept(tmp_path):
    a = {"paper": "P1", "kind": "unanchorable_quote", "detail": "q"}
    b = {"paper": "P2", "kind": "unanchorable_quote", "detail": "q"}
    _paper(tmp_path, "P1", [a])
    _paper(tmp_path, "P2", [b])
    run_wave._write_manifest(tmp_path, CFG, ["P1", "P2"],
                             [{"paper": "P1", "status": "done", "n_records": 1, "n_written": 0,
                               "questions": [a]},
                              {"paper": "P2", "status": "done", "n_records": 1, "n_written": 0,
                               "questions": [b]}],
                             [a, b], run_wave.Fuse(label="t"), complete=True)
    assert _catalogue(tmp_path) == [a, b]
