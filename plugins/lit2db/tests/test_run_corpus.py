"""The corpus runner's budget must be right, because nobody re-derives a number a script printed.

`project_cost` is the third instance in two days of ONE mistake living in ONE formula:

  1. Claude Science declared `judge_prompt=1200` and never used it → their 5.74M was 4.15M
  2. we computed D-036 with their formula, inherited `jp=3000` → our 9.68M was 8.09M (D-050)
  3. this function charged per-PAPER prompt overheads once for the whole CORPUS → a 382-paper
     run projected 53.8M when it is 67.0M

Three different people, three different directions, same formula. So it gets tests.
"""
import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "run_corpus", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "run_corpus.py")
run_corpus = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_corpus)
project_cost = run_corpus.project_cost

# Every figure D-036/D-050 published was computed when each agent was charged ONE read of its
# source. Those decisions are historical record, so the tests that pin them say so explicitly
# rather than drifting when the measured multiples change. `ONE_READ` is what "the arithmetic
# behind 8,088,969" means; it is not a claim about what a run costs.
ONE_READ = {"one_read": True}


def test_a_single_paper_reproduces_the_ratified_d036_arithmetic():
    """49 papers at 10,037 prose tokens each is the ratified D-036 case: 8,088,969 (D-050)."""
    c = project_cost(49 * 10_037, 49, k=3, records_per_paper=9,
                     judge_prompt=1200, extract_prompt=3000, **ONE_READ)
    assert c["total"] == 8_088_969


def test_the_jp_3000_defect_reproduces_the_wrong_number():
    """Guards the correction itself: passing the EXTRACTION prompt as the judge prompt is what
    put 9.68M into a ratified decision, a seed, a ladder and a reply."""
    c = project_cost(49 * 10_037, 49, k=3, records_per_paper=9,
                     judge_prompt=3000, extract_prompt=3000, **ONE_READ)
    assert c["total"] == 9_676_569


def test_cost_does_not_track_document_size_because_measurement_says_it_does_not():
    """The finding that replaced the re-read multiple.

    Nine passes over papers spanning 6,374 -> 17,130 prose tokens: per-pass cost was flat at
    70,880 +/- 12,843 and correlation with prose length was -0.14. A 17k paper cost the SAME as
    a 6k one. Any token-proportional model — a fraction of the document (the 215M error) or a
    multiple of it — is wrong in the same direction, so the projection counts invocations.
    """
    args = dict(k=3, records_per_paper=9, judge_prompt=1200, extract_prompt=3000)
    small = project_cost(6_000, 1, **args)["total"]
    large = project_cost(17_000, 1, **args)["total"]
    assert small == large, "projection must not vary with document size in the calibrated range"


def test_it_reproduces_the_calibration_measurement():
    """PMC10509563: 15 records, measured ~598,469 tokens end to end across the full config."""
    c = project_cost(6_374, 1, k=3, records_per_paper=15,
                     judge_prompt=1200, extract_prompt=3000)
    assert 0.9 < c["total"] / 598_469 < 1.1, c["total"]


def test_a_projection_states_which_model_produced_it():
    """A number nobody can attribute is how the same error survived four times."""
    assert project_cost(10_000, 1, k=3, records_per_paper=9, judge_prompt=1200,
                        extract_prompt=3000)["model"] == "per_invocation"
    assert project_cost(10_000, 1, k=3, records_per_paper=9, judge_prompt=1200,
                        extract_prompt=3000, **ONE_READ)["model"] == "one_read_per_token"


def test_overheads_are_charged_per_paper_not_per_corpus():
    """The bug this file exists for. Ten identical papers must cost 10x one paper — if the
    overhead is applied once for the whole corpus, it does not."""
    one = project_cost(10_000, 1, k=3, records_per_paper=9,
                       judge_prompt=1200, extract_prompt=3000, **ONE_READ)
    ten = project_cost(100_000, 10, k=3, records_per_paper=9,
                       judge_prompt=1200, extract_prompt=3000, **ONE_READ)
    assert ten["total"] == 10 * one["total"]
    for part in ("extract", "judge", "hunter", "overhead"):
        assert ten[part] == 10 * one[part], f"{part} does not scale with paper count"


def test_the_real_wave_one_projection():
    """382 papers, 4,134,975 prose tokens — measured from the frozen stores."""
    c = project_cost(4_134_975, 382, k=3, records_per_paper=9,
                     judge_prompt=1200, extract_prompt=3000, **ONE_READ)
    assert c["total"] == 66_971_875
    assert c["total"] != 53_789_275, "that is the undercount the per-corpus overhead produced"


def test_per_paper_mean_is_reported_because_it_is_the_number_that_extrapolates():
    c = project_cost(4_134_975, 382, k=3, records_per_paper=9,
                     judge_prompt=1200, extract_prompt=3000, **ONE_READ)
    assert 150_000 < c["per_paper_mean"] < 200_000


def test_records_per_paper_moves_the_budget_a_lot():
    """D-037: it is a MEASURED per-domain parameter. Terpenoid's 9 rests on n=1, so the run
    manifest must make the sensitivity visible rather than implying the figure is settled."""
    lo = project_cost(4_134_975, 382, k=3, records_per_paper=5,
                      judge_prompt=1200, extract_prompt=3000, **ONE_READ)["total"]
    hi = project_cost(4_134_975, 382, k=3, records_per_paper=20,
                      judge_prompt=1200, extract_prompt=3000, **ONE_READ)["total"]
    assert hi > 2 * lo


def test_zero_papers_does_not_divide_by_zero():
    c = project_cost(0, 0, k=3, records_per_paper=9, judge_prompt=1200, extract_prompt=3000)
    assert c["total"] == 0
    assert c["total"] == 0 and c["per_paper_mean"] == 0


@pytest.mark.parametrize("payload,expected", [
    ({"wave1": {"pmcids": ["A", "B"]}}, ["A", "B"]),
    ({"papers": ["C"]}, ["C"]),
    ({"pmcids": ["D", "E"]}, ["D", "E"]),
    (["F", "G"], ["F", "G"]),
])
def test_paper_lists_load_from_every_shape_we_actually_write(tmp_path, payload, expected):
    import json
    p = tmp_path / "papers.json"
    p.write_text(json.dumps(payload))
    assert run_corpus.load_papers(tmp_path, str(p)) == expected
