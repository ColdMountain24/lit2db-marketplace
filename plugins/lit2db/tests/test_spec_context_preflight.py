"""The wave refuses to start when spec-derived context is declared but not delivered (D-111).

Both directions have to fail, and the quieter one is the one that matters. A template holding
`{SPEC_CONTEXT}` with no spec configured is loud — the agent is handed a literal placeholder and
someone notices. A config that NAMES a spec whose template never uses it is silent: the wave
reports it gave the extractor the researcher's ratified scope, and gave it nothing. That is the
same declaration-not-backed-by-the-thing shape the 2026-07-28 audit found in the ladder and
`test_declarations.py` exists to prevent in the code.
"""
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.contracts import (                                            # noqa: E402
    RatificationLedger, LedgerItem, RatificationStatus, FieldSpec, CorpusQuery, SourceScope,
    SchemaReadySpec, MLTask,
)

run_wave = pytest.importorskip("run_wave")

TEMPLATE = "Read {STORE}, write {WRITES} into {OUT_DIR}."
TEMPLATE_WITH_CTX = TEMPLATE + "\n\n{SPEC_CONTEXT}"


def _write_spec(tmp_path: pathlib.Path) -> pathlib.Path:
    spec = SchemaReadySpec(
        research_question="q", ml_task=MLTask.regression, unit_of_analysis="(w, s)",
        fields=[FieldSpec(name="w", type="str", definition="d", provenance_granularity="p",
                          ledger_item_id="F1")],
        negative_data_policy="p",
        source_scope=SourceScope(adapters=["literature"],
                                 queries=[CorpusQuery(corpus="europepmc", query="x",
                                                      ledger_item_id="Q1")]),
        ledger=RatificationLedger(items=[
            LedgerItem(item_id="F1", kind="field", summary="ok",
                       status=RatificationStatus.ACCEPTED),
            LedgerItem(item_id="Q1", kind="source_scope", summary="q",
                       status=RatificationStatus.ACCEPTED)]))
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec.model_dump(mode="json")), encoding="utf-8")
    return p


def _cfg(tmp_path, template: str, spec: pathlib.Path | None):
    for name in ("extract", "judge", "hunter"):
        (tmp_path / f"{name}.md").write_text(
            template if name == "extract" else "x", encoding="utf-8")
    cfg = {"extract_prompt": str(tmp_path / "extract.md"),
           "judge_prompt": str(tmp_path / "judge.md"),
           "hunter_prompt": str(tmp_path / "hunter.md"),
           "stores": str(tmp_path / "stores"),
           "identity_fields": {"primary": "w"},
           "judge_audit_fraction": 0.2,
           "models": ["opus"]}
    if spec is not None:
        cfg["spec"] = str(spec)
    return cfg


def _problems(cfg):
    return "\n".join(run_wave.preflight(cfg, []))


def test_token_without_a_spec_is_refused(tmp_path):
    out = _problems(_cfg(tmp_path, TEMPLATE_WITH_CTX, None))
    assert "{SPEC_CONTEXT}" in out and "no `spec` is configured" in out


def test_spec_without_the_token_is_refused(tmp_path):
    """The quiet direction: the wave would claim to supply scope and supply nothing."""
    out = _problems(_cfg(tmp_path, TEMPLATE, _write_spec(tmp_path)))
    assert "never uses" in out and "give it nothing" in out


def test_matched_pair_raises_no_spec_context_problem(tmp_path):
    out = _problems(_cfg(tmp_path, TEMPLATE_WITH_CTX, _write_spec(tmp_path)))
    assert "SPEC_CONTEXT" not in out


def test_neither_is_fine_so_existing_waves_still_run(tmp_path):
    """Context is opt-in. A wave predating D-111 must not become unrunnable."""
    out = _problems(_cfg(tmp_path, TEMPLATE, None))
    assert "SPEC_CONTEXT" not in out and "spec" not in out.lower().split("stores")[0]


def test_an_unratified_spec_is_refused_at_preflight(tmp_path):
    """Not merely unreadable — a spec whose field is only PROPOSED must stop the wave."""
    spec = _write_spec(tmp_path)
    d = json.loads(spec.read_text())
    d["ledger"]["items"][0]["status"] = RatificationStatus.PROPOSED.value
    spec.write_text(json.dumps(d), encoding="utf-8")
    run_wave._spec_context_for.cache_clear()
    out = _problems(_cfg(tmp_path, TEMPLATE_WITH_CTX, spec))
    assert "did not validate as a ratified SchemaReadySpec" in out


# --- the inversion's configuration (D-110/D-112) -------------------------------------

def _g(tmp_path, **over):
    cfg = _cfg(tmp_path, TEMPLATE, None)
    cfg.update(over)
    return _problems(cfg)


def test_llm_grounding_refuses_a_single_reading(tmp_path):
    """A config asking for one reading must be REFUSED, not silently bumped to two. The floor
    in the grounder is belt-and-braces; a wave doing something its config does not say is the
    quiet failure this project keeps finding."""
    out = _g(tmp_path, grounding_mode="llm", grounding_repeats=1)
    assert "One reading is the condition that bar refused" in out


def test_llm_grounding_accepts_two(tmp_path):
    out = _g(tmp_path, grounding_mode="llm", grounding_repeats=2)
    assert "grounding_repeats" not in out


def test_an_unknown_grounding_mode_is_refused(tmp_path):
    assert "is not 'lexical' or 'llm'" in _g(tmp_path, grounding_mode="magic")


def test_the_default_stays_lexical_and_silent(tmp_path):
    assert "grounding_mode" not in _g(tmp_path)


def test_the_grounder_may_not_be_an_extraction_model(tmp_path):
    """D-041's separation. A model grading its own extraction is not an independent check."""
    out = _g(tmp_path, grounding_mode="llm", grounding_repeats=2,
             grounding_model="opus", models=["opus"])
    assert "marking its own work" in out
