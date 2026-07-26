"""Tests for token accounting — the comparable unit of computational cost.

The load-bearing properties: tokens are reported as tokens (no silent currency
conversion), rates are never hardcoded, and projection from a small sample always
carries the sample size so a projected total can't be mistaken for a measured one.
"""
import json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lit2db.accounting import STREAMS, RunAccount, _norm

HOOK = ROOT / "hooks" / "stop_costcap_checkpoint.py"


# --- normalization ----------------------------------------------------------------
def test_accepts_api_field_names():
    n = _norm({"input_tokens": 100, "output_tokens": 20,
               "cache_read_input_tokens": 5, "cache_creation_input_tokens": 3})
    assert n == {"input": 100, "output": 20, "cache_read": 5, "cache_write": 3}


def test_accepts_short_field_names_and_fills_gaps():
    assert _norm({"input": 7})["input"] == 7
    assert _norm({"input": 7})["output"] == 0


def test_garbage_usage_is_zero_not_an_error():
    for bad in (None, "nope", 42, {"input": "abc"}, {}):
        assert _norm(bad) == {s: 0 for s in STREAMS}


def test_negative_counts_are_clamped():
    assert _norm({"input": -5})["input"] == 0


# --- accumulation -----------------------------------------------------------------
def test_totals_and_two_axes():
    a = RunAccount("run")
    a.record({"input": 100, "output": 10}, unit="paper1", stage="extract")
    a.record({"input": 50, "output": 5}, unit="paper1", stage="judge")
    a.record({"input": 200, "output": 20}, unit="paper2", stage="extract")
    assert a.totals()["input"] == 350
    r = a.report()
    assert r["by_unit"]["paper1"]["input"] == 150     # both stages
    assert r["by_stage"]["extract"]["input"] == 300   # both papers
    assert r["unit"] == "tokens"


def test_per_unit_mean_is_the_number_that_extrapolates():
    a = RunAccount()
    a.record({"input": 100}, unit="p1")
    a.record({"input": 300}, unit="p2")
    assert a.per_unit_mean()["input"] == 200.0


def test_unattributed_does_not_inflate_the_unit_count():
    """Overhead with no owning unit must not drag the per-unit mean down."""
    a = RunAccount()
    a.record({"input": 100}, unit="p1")
    a.record({"input": 999})              # no unit
    assert a.n_units == 1
    assert a.per_unit_mean()["input"] == 1099.0   # totals still counted


def test_empty_account_does_not_divide_by_zero():
    a = RunAccount()
    assert a.per_unit_mean() == {s: 0.0 for s in STREAMS}
    assert a.project(102)["projected"]["input"] == 0


# --- projection -------------------------------------------------------------------
def test_projection_carries_its_sample_size():
    """A projected corpus total must never be mistakable for a measured one."""
    a = RunAccount()
    for i in range(5):
        a.record({"input": 40_000, "output": 2_000}, unit=f"p{i}")
    p = a.project(102)
    assert p["measured_on"] == 5 and p["n_units"] == 102
    assert p["projected"]["input"] == 40_000 * 102


# --- currency is derived, caveated, and never hardcoded ---------------------------
def test_rates_are_supplied_by_the_caller():
    a = RunAccount()
    a.record({"input": 1_000_000, "output": 1_000_000}, unit="p1")
    assert a.api_equivalent_cost({"input": 3.0, "output": 15.0})["usd_equivalent"] == 18.0


def test_no_rates_means_no_cost_not_a_guess():
    """Absent rates must yield zero, never a baked-in default price."""
    a = RunAccount()
    a.record({"input": 1_000_000}, unit="p1")
    assert a.api_equivalent_cost({})["usd_equivalent"] == 0.0


def test_cost_output_is_labelled_as_an_equivalent():
    a = RunAccount()
    a.record({"input": 1000}, unit="p1")
    assert "did not pay this" in a.api_equivalent_cost({"input": 3.0})["caveat"]


def test_report_is_token_denominated_and_carries_no_currency():
    a = RunAccount()
    a.record({"input": 1000}, unit="p1")
    assert "usd" not in json.dumps(a.report()).lower()


# --- the cap hook -----------------------------------------------------------------
def _run_hook(payload, env=None, raw=None):
    stdin = raw if raw is not None else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True,
                          text=True, env={**os.environ, **(env or {})})


def test_hook_reports_the_stream_split():
    out = _run_hook({"usage": {"input_tokens": 1234, "output_tokens": 56}})
    assert "input=1,234" in out.stderr and "total=1,290" in out.stderr


def test_hook_trips_the_breaker_on_token_overrun():
    out = _run_hook({"usage": {"input_tokens": 40_000}},
                    env={"LIT2DB_GOLD_TOKENS": "10000", "LIT2DB_TOKEN_CAP_RATIO": "3.0"})
    assert json.loads(out.stdout)["decision"] == "block"
    assert "tokens exceeds" in json.loads(out.stdout)["reason"]


def test_hook_stays_quiet_under_the_cap():
    out = _run_hook({"usage": {"input_tokens": 20_000}},
                    env={"LIT2DB_GOLD_TOKENS": "10000", "LIT2DB_TOKEN_CAP_RATIO": "3.0"})
    assert out.stdout.strip() == ""


def test_cap_disabled_when_no_gold_baseline():
    out = _run_hook({"usage": {"input_tokens": 999_999_999}},
                    env={"LIT2DB_GOLD_TOKENS": "0"})
    assert out.stdout.strip() == ""


def test_hook_fails_open_on_garbage():
    """A budget guard must never wedge a long run — unlike the write-gate."""
    assert _run_hook(None, raw="not json").stdout.strip() == ""
    assert _run_hook({"no": "usage"}).stdout.strip() == ""
