"""The inversion at its seam: `assemble` with an injected grounder (D-110/D-112).

What must hold end to end, and each of these is a way the change could go wrong quietly:
the default path is untouched, the model is asked only about fields grounding is FOR, a
disagreement cannot produce a passing score, and the offset check stays deterministic.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lit2db.pipeline import assemble          # noqa: E402

# The quote writes "strain"; the stored value writes "sp.". That is D-112's ONLY unstable name
# judgement and the exact shape D-108's lexical rules cannot reach — the tokens are not present
# in the paper's own wording, so no in-order token rule can rescue it. A model reads it fine.
QUOTE = "we isolated xiamycin A from the culture broth of Streptomyces strain HKI0297"
LEXICALLY_UNREACHABLE = "sp. HKI0297"


@pytest.fixture()
def store(tmp_path):
    d = tmp_path / "PMC_test"
    d.mkdir()
    (d / "full.txt").write_text(QUOTE, encoding="utf-8")
    (d / "sections.json").write_text(json.dumps(
        [{"title": "Results", "depth": 1, "start": 0, "end": len(QUOTE)}]), encoding="utf-8")
    return tmp_path


def _cfg(store_root, **over):
    cfg = {"stores": str(store_root), "extract_prompt": __file__,
           "models": ["a", "b", "c"], "producing_process": "test",
           "run_timestamp": "2026-07-28T00:00:00Z", "identity_primary": "compound_name",
           "evidence_grounded_fields": ["evidence_basis"], "review_lane": []}
    cfg.update(over)
    return cfg


def _merged(value="xiamycin A", extra_fields=()):
    fields = [{"field_name": "compound_name", "value": value, "verbatim_quote": QUOTE,
               "agreement": 1.0, "n_passes": 3, "n_agreeing": 3}]
    fields.extend(extra_fields)
    rec = {"record_id": "cpd1", "entity_type": "compound", "fields": fields}
    # `_passes` is the raw per-pass output the quote is re-attached from — a list of passes,
    # each a list of records. Three identical passes stand in for a unanimous ensemble.
    return {"records": [rec], "_passes": [[rec], [rec], [rec]]}


def _ground(out, field):
    for f in out[0]["fields"]:
        if f["field_name"] == field:
            return f["confidence_components"]
    raise AssertionError(f"{field} not in {[f['field_name'] for f in out[0]['fields']]}")


def test_default_is_still_the_deterministic_rule(store):
    """No grounder passed -> nothing changes. An existing wave must not shift under this."""
    out, _ = assemble("PMC_test", _cfg(store), _merged(),
                      {"state_by_record": {}, "contradictions": []})
    cc = _ground(out, "compound_name")
    assert cc["c_grounded"] == 1.0
    assert "_grounding_mode" not in cc


def test_the_model_decides_when_injected(store):
    """A value the LEXICAL rule cannot match, that the model supports, now scores."""
    out, _ = assemble("PMC_test", _cfg(store),
                      _merged(value=LEXICALLY_UNREACHABLE),
                      {"state_by_record": {}, "contradictions": []},
                      grounder=lambda *_: [True, True])
    cc = _ground(out, "compound_name")
    assert cc["c_grounded"] == 1.0
    assert cc["_grounding_mode"] == "llm" and cc["_grounding_state"] == "supported"


def test_the_lexical_rule_really_would_have_failed_that_value(store):
    """Without this, the test above proves nothing — it could be passing for free."""
    out, _ = assemble("PMC_test", _cfg(store), _merged(value=LEXICALLY_UNREACHABLE),
                      {"state_by_record": {}, "contradictions": []})
    assert _ground(out, "compound_name")["c_grounded"] < 1.0


def test_disagreement_cannot_produce_a_passing_score(store):
    out, _ = assemble("PMC_test", _cfg(store), _merged(),
                      {"state_by_record": {}, "contradictions": []},
                      grounder=lambda *_: [True, False])
    cc = _ground(out, "compound_name")
    assert cc["c_grounded"] == 0.0 and cc["_grounding_state"] == "unstable"


def test_a_single_reading_cannot_produce_a_passing_score(store):
    out, _ = assemble("PMC_test", _cfg(store), _merged(),
                      {"state_by_record": {}, "contradictions": []},
                      grounder=lambda *_: [True])
    cc = _ground(out, "compound_name")
    assert cc["c_grounded"] == 0.0 and cc["_grounding_state"] == "not_run"


def test_a_dead_grounder_fails_closed(store):
    out, _ = assemble("PMC_test", _cfg(store), _merged(),
                      {"state_by_record": {}, "contradictions": []},
                      grounder=lambda *_: [None, None])
    assert _ground(out, "compound_name")["c_grounded"] == 0.0


def test_evidence_grounded_fields_are_NOT_asked(store):
    """D-061's exclusion is inherited, not re-litigated. No paper contains the string
    `biosynthesis_demonstrated`, so 'does this quote assert that value' is unanswerable — and
    asking it anyway is where BOTH of D-112's unstable judgements came from."""
    asked = []

    def grounder(rid, field, value, quote):
        asked.append(field)
        return [False, False]          # would zero the field if it were consulted

    out, _ = assemble("PMC_test", _cfg(store), _merged(extra_fields=[
        {"field_name": "evidence_basis", "value": "biosynthesis_demonstrated",
         "verbatim_quote": QUOTE, "agreement": 1.0, "n_passes": 3, "n_agreeing": 3}]),
        {"state_by_record": {}, "contradictions": []}, grounder=grounder)
    assert "evidence_basis" not in asked, asked
    assert "compound_name" in asked
    ev = _ground(out, "evidence_basis")
    assert ev["c_grounded"] == 1.0 and ev.get("_grounding_mode") == "evidence_anchored"


def test_an_unanchorable_quote_is_still_dropped_before_the_model_is_asked(store):
    """The offset check stays HARD and stays FIRST. A model must never be given the chance to
    support a value whose quote is not in the paper — that is a fact, not a judgement."""
    asked = []
    merged = _merged()
    merged["records"][0]["fields"][0]["verbatim_quote"] = "text that is not in this paper at all"
    out, dropped = assemble("PMC_test", _cfg(store), merged,
                            {"state_by_record": {}, "contradictions": []},
                            grounder=lambda *a: asked.append(a) or [True, True])
    assert asked == [], "the model was asked about a quote that does not anchor"
    assert any(d["why"] == "quote not in full.txt" for d in dropped), dropped


def test_the_grounder_receives_the_anchored_quote_and_ids(store):
    seen = {}

    def grounder(rid, field, value, quote):
        seen.update(rid=rid, field=field, value=value, quote=quote)
        return [True, True]

    assemble("PMC_test", _cfg(store), _merged(), {"state_by_record": {}, "contradictions": []},
             grounder=grounder)
    assert seen == {"rid": "cpd1", "field": "compound_name",
                    "value": "xiamycin A", "quote": QUOTE}
