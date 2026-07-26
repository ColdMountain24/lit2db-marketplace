"""The fuse must STOP a runaway, not describe one afterwards.

`accounting.py` measures; nothing stopped it. The terpenoid e2e run measured agents reading each
document 7.8x over per extraction pass — a figure nobody predicted and nothing would have
interrupted. These tests pin the three ceilings, the raise-only rule, and the property that
matters most: a tripped fuse RAISES rather than returning a degraded result.
"""
import logging

import pytest

from lit2db.fuse import (
    DEFAULT_MAX_CALLS,
    DEFAULT_MAX_TOKENS_PER_CALL,
    DEFAULT_MAX_TOKENS_TOTAL,
    Fuse,
    FuseExceeded,
)


def test_a_normal_paper_passes_every_ceiling():
    """One paper at the D-036 config: 3 extraction passes + 9 judge calls + 1 hunter."""
    fuse = Fuse(label="PMC12298776")
    for _ in range(13):
        fuse.check(estimated_tokens=15_000)
        fuse.record({"input": 14_000, "output": 1_000})
    assert fuse.calls == 13
    assert fuse.tokens_total == 13 * 15_000
    assert fuse.snapshot()["calls_remaining"] == DEFAULT_MAX_CALLS - 13


def test_fuse_1_refuses_one_enormous_prompt_before_it_is_sent():
    """The whole-corpus-in-one-call defect. Caught by check(), so it is never paid for."""
    fuse = Fuse(max_tokens_per_call=200_000)
    with pytest.raises(FuseExceeded) as exc:
        fuse.check(estimated_tokens=5_000_000)
    assert exc.value.which == "max_tokens_per_call"
    assert fuse.calls == 0, "a refused call must not be counted as having happened"


def test_fuse_2_stops_an_unbounded_loop():
    """The classic agent loop — re-read, re-verify, forever. Each call looks reasonable;
    only the count sees it."""
    fuse = Fuse(max_calls=5, max_tokens_total=10**9)
    for _ in range(5):
        fuse.check(estimated_tokens=10)
        fuse.record({"input": 10})
    with pytest.raises(FuseExceeded) as exc:
        fuse.check(estimated_tokens=10)
    assert exc.value.which == "max_calls"


def test_fuse_3_stops_the_run_in_aggregate():
    fuse = Fuse(max_calls=10**6, max_tokens_total=100_000)
    fuse.check(estimated_tokens=60_000)
    fuse.record({"input": 60_000})
    with pytest.raises(FuseExceeded) as exc:
        fuse.check(estimated_tokens=60_000)
    assert exc.value.which == "max_tokens_total"
    assert exc.value.observed == 120_000


def test_an_optimistic_estimate_cannot_defeat_the_fuse():
    """check() takes an estimate, so record() must re-check on ACTUALS.

    Otherwise any caller that under-estimates walks straight through the ceiling.
    """
    fuse = Fuse(max_tokens_total=50_000)
    fuse.check(estimated_tokens=10)          # claims it will be tiny...
    with pytest.raises(FuseExceeded) as exc:
        fuse.record({"input": 90_000})       # ...and is not
    assert exc.value.which == "max_tokens_total"


def test_a_call_that_happened_is_counted_even_when_it_trips():
    """Counting after the fact would let a run exceed by one call every time."""
    fuse = Fuse(max_tokens_total=1_000)
    with pytest.raises(FuseExceeded):
        fuse.record({"input": 5_000})
    assert fuse.calls == 1
    assert fuse.tokens_total == 5_000


def test_ceilings_raise_but_never_lower():
    fuse = Fuse(max_calls=10, max_tokens_total=1_000)
    fuse.raise_ceiling(max_calls=50, reason="corpus run")
    assert fuse.max_calls == 50
    fuse.raise_ceiling(max_calls=5)
    assert fuse.max_calls == 50, "lowering must be a no-op — in-flight work was already budgeted"
    fuse.raise_ceiling(max_tokens_total=1_000_000)
    assert fuse.max_tokens_total == 1_000_000


def test_raising_a_ceiling_is_logged_so_the_run_records_that_it_needed_more(caplog):
    fuse = Fuse(max_calls=10, label="corpus")
    with caplog.at_level(logging.INFO, logger="lit2db.fuse"):
        fuse.raise_ceiling(max_calls=900, reason="922-paper corpus")
    assert "ceiling raised" in caplog.text
    assert "922-paper corpus" in caplog.text


def test_the_error_says_which_ceiling_and_by_how_much():
    """An operator reading a CI log must not have to guess which fuse blew."""
    fuse = Fuse(max_calls=1, label="PMC999")
    fuse.check(1); fuse.record({"input": 1})
    with pytest.raises(FuseExceeded) as exc:
        fuse.check(1)
    msg = str(exc.value)
    assert "max_calls" in msg and "PMC999" in msg
    assert "not a budget" in msg, "the fuse/cost-manager distinction belongs in the message"


def test_env_overrides_are_read_and_bad_values_fall_back(monkeypatch):
    monkeypatch.setenv("LIT2DB_FUSE_MAX_CALLS", "7")
    assert Fuse().max_calls == 7
    monkeypatch.setenv("LIT2DB_FUSE_MAX_CALLS", "not-a-number")
    assert Fuse().max_calls == DEFAULT_MAX_CALLS
    monkeypatch.setenv("LIT2DB_FUSE_MAX_CALLS", "0")
    assert Fuse().max_calls == DEFAULT_MAX_CALLS, "a zero ceiling would refuse all work"


def test_defaults_are_shipped_finite():
    """A fuse defaulting to unlimited is not a fuse."""
    fuse = Fuse()
    assert 0 < fuse.max_tokens_per_call == DEFAULT_MAX_TOKENS_PER_CALL < 10**9
    assert 0 < fuse.max_calls == DEFAULT_MAX_CALLS < 10**6
    assert 0 < fuse.max_tokens_total == DEFAULT_MAX_TOKENS_TOTAL < 10**9


def test_defaults_clear_one_real_paper_with_headroom():
    """Sized from measurement: largest terpenoid paper 136k prose tokens, 13 calls, ~197k total."""
    assert DEFAULT_MAX_TOKENS_PER_CALL > 136_000
    assert DEFAULT_MAX_CALLS > 13
    assert DEFAULT_MAX_TOKENS_TOTAL > 197_000


def test_it_feeds_the_account_without_becoming_one():
    """The fuse stops; accounting explains. Wiring them must not merge them."""
    from lit2db.accounting import RunAccount
    acct = RunAccount("corpus")
    fuse = Fuse(account=acct, max_tokens_total=10**9)
    fuse.record({"input": 1_000, "output": 200}, unit="PMC1", stage="extract")
    fuse.record({"input": 2_000, "output": 300}, unit="PMC2", stage="extract")
    assert acct.totals()["input"] == 3_000
    assert fuse.tokens_total == 3_500
    assert acct.n_units == 2
