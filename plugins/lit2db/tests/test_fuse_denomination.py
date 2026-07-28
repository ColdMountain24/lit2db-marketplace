"""The fuse ceiling counts FIRST-TIME CONTENT, the same figure as the cost headline (D-093).

It used to sum every stream, `cache_read` included. On the 55-paper validation arm that was
**82% of the total**: the fuse tripped at 24.4M while the actual work was 4.4M. A brake
denominated differently from the cost report is one nobody can size — it stopped a healthy run
and read as a five-fold overrun.

This changes when every future run stops, so the semantics are pinned here rather than left to
the caller to rediscover.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.fuse import Fuse, FuseExceeded  # noqa: E402


def usage(input=0, output=0, cache_read=0, cache_write=0):
    return {"input_tokens": input, "output_tokens": output,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write}


def test_cache_read_does_not_consume_the_ceiling():
    """The arm's exact shape: a little work, an enormous amount of re-reading."""
    f = Fuse(max_tokens_total=1000, max_calls=100)
    for _ in range(20):
        f.record(usage(input=5, output=5, cache_write=10, cache_read=100_000))
    assert f.tokens_total == 20 * 20        # 400 of first-time content
    assert f.tokens_all_streams > 2_000_000  # re-reads counted, never enforced


def test_first_time_content_still_trips_it():
    f = Fuse(max_tokens_total=100, max_calls=100)
    with pytest.raises(FuseExceeded) as exc:
        for _ in range(10):
            f.record(usage(input=10, cache_write=10, output=10))
    assert exc.value.which == "max_tokens_total"


def test_a_runaway_loop_is_still_caught_because_a_loop_generates_output():
    """The property that had to survive: re-reading is free, but looping is not."""
    f = Fuse(max_tokens_total=10_000, max_calls=10_000)
    with pytest.raises(FuseExceeded):
        for _ in range(500):
            f.record(usage(output=100, cache_read=50_000))


def test_max_calls_is_untouched_and_remains_the_primary_loop_brake():
    f = Fuse(max_calls=3, max_tokens_total=10**9)
    for _ in range(3):
        f.record(usage(output=1))
    with pytest.raises(FuseExceeded) as exc:
        f.check(estimated_tokens=1)
    assert exc.value.which == "max_calls"


def test_the_snapshot_reports_both_numbers():
    """A reader must be able to see the re-read volume without it bounding them."""
    f = Fuse(max_tokens_total=10**6, max_calls=100)
    f.record(usage(input=1, output=2, cache_write=3, cache_read=9_000))
    snap = f.snapshot()
    assert snap["tokens_all_streams"] == 9_006
    assert snap["tokens_remaining"] == 10**6 - 6


def test_the_arm_would_no_longer_have_tripped():
    """Regression against the real event: 4.4M of work under a 24.4M ceiling must not trip,
    no matter how much cache_read rode along with it."""
    f = Fuse(max_tokens_total=24_446_191, max_calls=10**6)
    # 55 papers' measured shape, scaled into 55 calls
    for _ in range(55):
        f.record(usage(input=35, output=13_576, cache_write=66_194, cache_read=371_754))
    assert f.tokens_total < f.max_tokens_total
    assert f.tokens_all_streams > f.max_tokens_total   # the old rule would have tripped
