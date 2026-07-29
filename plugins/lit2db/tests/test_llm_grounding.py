"""The inversion's decision rule (D-110/D-112): the model decides, determinism constrains.

Every test here is about a way the wrapper could QUIETLY LOOSEN the gate, because that is this
change's whole risk surface. Handing a judgement to a model is safe only while a failed call, an
unreadable reply, and a disagreement all remain distinguishable from a yes — and all three arrive
as "no clean verdict", which is exactly how they get collapsed by accident.
"""
import pytest

from lit2db.llm_grounding import GROUNDING_PROMPT, build_prompt, parse_verdict, resolve


# --- the agreement rule ---------------------------------------------------------------

def test_unanimous_yes_is_the_only_way_to_score():
    g = resolve([True, True])
    assert g["state"] == "supported" and g["c_grounded"] == 1.0


def test_unanimous_no_is_a_real_result_not_a_failure():
    g = resolve([False, False])
    assert g["state"] == "unsupported" and g["c_grounded"] == 0.0


def test_disagreement_is_never_broken_by_the_grounder():
    """A tie-break is the judgement the repeat was measuring. It routes; it does not resolve."""
    for v in ([True, False], [False, True], [True, True, False], [False, False, True]):
        g = resolve(v)
        assert g["state"] == "unstable", v
        assert g["c_grounded"] == 0.0, v


def test_one_answer_is_not_enough_even_when_it_is_yes():
    """The pre-registered bar refused a single reading. Falling back to it would silently undo
    the ratified decision while every test still passed."""
    g = resolve([True])
    assert g["state"] == "not_run" and g["c_grounded"] == 0.0


def test_no_answers_fails_closed():
    for v in ([], [None], [None, None], None):
        assert resolve(v)["state"] == "not_run"
        assert resolve(v)["c_grounded"] == 0.0


def test_failed_calls_are_discarded_not_counted_as_dissent():
    """A call that never executed is not the model disagreeing with itself — but it also cannot
    make up the quorum. Two real yeses beside a failure is `supported`; one is not."""
    assert resolve([True, True, None])["state"] == "supported"
    assert resolve([True, None])["state"] == "not_run"
    assert resolve([True, False, None])["state"] == "unstable"


def test_the_four_states_are_distinct():
    """`not_run` != `unsupported` != `unstable`. Three prior instances of this collapse were
    real defects (hunter not_run/clean, judge not_run/supported, adjudication cant_tell/wrong)."""
    seen = {resolve(v)["state"] for v in ([True, True], [False, False], [True, False], [])}
    assert seen == {"supported", "unsupported", "unstable", "not_run"}


def test_every_non_supported_state_scores_zero():
    for v in ([False, False], [True, False], [], [True]):
        assert resolve(v)["c_grounded"] == 0.0


def test_verdicts_are_carried_through_for_audit():
    g = resolve([True, None, False])
    assert g["verdicts"] == [True, None, False] and g["n_answers"] == 2


# --- reading the model's reply --------------------------------------------------------

def test_reads_the_requested_shape():
    assert parse_verdict('[{"i":0,"supports":true}]') is True
    assert parse_verdict('[{"i":0,"supports":false}]') is False


def test_reads_the_bare_object_the_model_actually_returns():
    """Observed on a real record in D-112: asked about ONE pair the model sometimes drops the
    array. Its verdict was stable across all six attempts, so refusing it loses a real answer."""
    assert parse_verdict('{"i": 0, "supports": true}') is True


def test_unreadable_replies_become_None_never_a_guess():
    for txt in ("", "I think it is supported.", "[]", "[{}]", '[{"i":0}]', "not json",
                '[{"i":0,"supports":"yes"}]', '{"supports":null}'):
        assert parse_verdict(txt) is None, txt


def test_two_verdicts_where_one_was_asked_is_unreadable():
    """Alignment, not charity. If the reply does not answer the question that was asked, it is
    not evidence about the pair — taking the first element would be a guess wearing a verdict."""
    assert parse_verdict('[{"i":0,"supports":true},{"i":1,"supports":false}]') is None


def test_a_reply_about_a_different_item_is_unreadable():
    assert parse_verdict('[{"i":3,"supports":true}]') is None


# --- the prompt -----------------------------------------------------------------------

def test_one_pair_per_call():
    """Singleton beat batched on every stratum, and all three cross-arm differences went
    batched=NO -> singleton=YES: sharing a question suppressed support."""
    p = build_prompt("xiamycin A", "we isolated xiamycin A from the broth")
    assert p.count("\n0. value=") == 1
    assert "1. value=" not in p


def test_the_measured_prompt_text_is_unchanged():
    """D-112's 2.78% is a property of THIS wording. Reword it and the number stops describing
    what is running, so a re-run of the stability arm is required first."""
    for line in ("This is a grounding check, not a plausibility check.",
                 "Answer YES only if a careful reader would agree the quote asserts that value",
                 "Answer NO if the quote does not assert it, even if the value seems"):
        assert line in GROUNDING_PROMPT


def test_spec_context_is_prepended_leaving_the_measured_text_intact():
    p = build_prompt("v", "q", spec_context="# What this database is collecting\nWidgets.")
    assert p.startswith("# What this database is collecting")
    assert "This is a grounding check, not a plausibility check." in p
    assert p.index("Widgets.") < p.index("This is a grounding check")


@pytest.mark.parametrize("value,quote", [("a'b", 'q"q'), ("δ-cadinol", "the (+)-δ-cadinol"),
                                         (12.5, "measured 12.5 units")])
def test_awkward_values_survive_into_the_prompt(value, quote):
    p = build_prompt(value, quote)
    assert str(value)[:4] in p and quote[:6] in p
