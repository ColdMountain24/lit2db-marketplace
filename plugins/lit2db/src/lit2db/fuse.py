"""The token fuse — a safety device against runaway loops, NOT a cost manager.

That distinction is load-bearing and borrowed deliberately from RAW, where the same split is
constitutional law. A **fuse** bounds one run so a defect cannot burn an unbounded amount: it
trips, fails hard, and says why. A **cost manager** decides what a user may afford across runs,
reads real spend, and refuses new work. Those are different jobs with different failure modes,
and merging them produces a thing that is bad at both — a budget that silently degrades quality,
or a safety device that can be argued with.

`accounting.py` already MEASURES what a run consumed. Nothing STOPPED it. That gap is not
theoretical here: the terpenoid e2e run measured agents reading each document **7.8× over** per
extraction pass, a figure nobody predicted and nothing would have interrupted. A projection that
was wrong five times is exactly the situation in which you want a hard stop rather than a
forecast.

Three ceilings, each answering a different runaway:

  1. `max_tokens_per_call`  — one enormous prompt. Catches "the whole corpus got concatenated
     into a single call" before it is sent, not after it is billed.
  2. `max_calls`            — unbounded repetition. The classic agent loop: re-read, re-verify,
     re-check, forever. Call count is the only thing that sees this, because each individual
     call looks reasonable.
  3. `max_tokens_total`     — the run in aggregate. Still a fuse, because it is scoped to ONE
     run with a ceiling the caller set for that run. A ceiling spanning runs, or derived from
     what someone has already spent, would be a cost manager and does not belong here.

**Ceilings are raise-only.** Lowering one mid-run could trip work already budgeted and in flight,
so `raise_ceiling` refuses to lower and never grants unlimited. This is RAW's rule and the reason
is the same: a fuse you can quietly widen downward is not a fuse.

The defaults below are **conservative placeholders sized from one measured paper**, not calibrated
values — the same status as the 0.95 auto-accept threshold. Per D-034 a load-bearing constant
should be a ratified setting stated plainly, so a project overrides these from its instantiation
and the corpus runner raises them explicitly for the scope it is about to run. Shipping a fuse
that defaults to unlimited would not be shipping a fuse.

Deliberately STDLIB-ONLY so hooks can import it — the same constraint as `lit2db.gate` and
`lit2db.accounting`.
"""
from __future__ import annotations

import logging
import os

from .accounting import STREAMS, _norm

logger = logging.getLogger("lit2db.fuse")

# --- Placeholder ceilings, sized for ONE source document -------------------------------
# Measured inputs: the largest terpenoid paper is 136k prose tokens; a judge prompt adds
# ~1.2k; one paper at the D-036 configuration is 3 extraction passes + 9 per-record judge
# calls + 1 per-paper hunter = 13 calls and ~197k tokens. These leave real headroom over
# that and are still far below a runaway.
DEFAULT_MAX_TOKENS_PER_CALL = 200_000
DEFAULT_MAX_CALLS = 50
DEFAULT_MAX_TOKENS_TOTAL = 1_000_000


def _env_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d", name, raw, fallback)
        return fallback
    if value <= 0:
        logger.warning("%s=%d is not positive; using %d", name, value, fallback)
        return fallback
    return value


class FuseExceeded(RuntimeError):
    """A ceiling was crossed. Carries which one and by how much.

    A hard failure on purpose. Returning a sentinel, or trimming the input to fit, would let a
    runaway continue in a degraded form — and a silently truncated extraction is precisely the
    defect D-038 exists to make impossible.
    """

    def __init__(self, which: str, limit: int, observed: int, label: str = ""):
        self.which, self.limit, self.observed, self.label = which, limit, observed, label
        where = f" [{label}]" if label else ""
        super().__init__(
            f"lit2db fuse tripped{where}: {which} limit {limit:,}, observed {observed:,}. "
            "This is a safety device against runaway loops, not a budget — if the work "
            "genuinely needs more, raise the ceiling explicitly and record why.")


class Fuse:
    """Three ceilings over one run. Check before each call, record after.

    Typical use, one source document::

        fuse = Fuse(label="PMC12298776")
        fuse.check(estimated_tokens=len(prose) // 4)   # before: fuses 1 and 2
        ...                                            # the call
        fuse.record(usage)                             # after: fuse 3 on actuals

    `check` runs before the call so an oversized prompt is refused rather than paid for.
    `record` re-checks on actuals because an estimate can be low, and the fuse must not be
    defeated by an optimistic guess.
    """

    def __init__(self, *, max_tokens_per_call: int | None = None,
                 max_calls: int | None = None,
                 max_tokens_total: int | None = None,
                 label: str = "",
                 account=None):
        self.max_tokens_per_call = max_tokens_per_call if max_tokens_per_call is not None else \
            _env_int("LIT2DB_FUSE_MAX_TOKENS_PER_CALL", DEFAULT_MAX_TOKENS_PER_CALL)
        self.max_calls = max_calls if max_calls is not None else \
            _env_int("LIT2DB_FUSE_MAX_CALLS", DEFAULT_MAX_CALLS)
        self.max_tokens_total = max_tokens_total if max_tokens_total is not None else \
            _env_int("LIT2DB_FUSE_MAX_TOKENS_TOTAL", DEFAULT_MAX_TOKENS_TOTAL)
        self.label = label
        self.calls = 0
        self.tokens_total = 0          # first-time content — what the ceiling counts
        self.tokens_all_streams = 0    # incl. cache_read, reported never enforced
        # Optional RunAccount: the fuse stops, accounting explains. Keeping them separate
        # means a run can be measured without being bounded, and bounded without being
        # re-instrumented.
        self.account = account

    # --- the two checks ---------------------------------------------------------------
    def check(self, estimated_tokens: int = 0, *, unit: str = "", stage: str = "") -> None:
        """Fuses 1 and 2, before the call. Raises FuseExceeded; never returns a verdict."""
        if estimated_tokens > self.max_tokens_per_call:
            self._trip("max_tokens_per_call", self.max_tokens_per_call, estimated_tokens)
        if self.calls >= self.max_calls:
            self._trip("max_calls", self.max_calls, self.calls + 1)
        projected = self.tokens_total + max(0, estimated_tokens)
        if projected > self.max_tokens_total:
            self._trip("max_tokens_total", self.max_tokens_total, projected)

    def record(self, usage, *, unit: str = "", stage: str = "") -> dict:
        """Count one completed call and re-check fuse 3 on ACTUAL usage.

        Counts before checking: a call that happened has happened, and under-counting it
        would let a run exceed its ceiling by one call every time the estimate was low.
        """
        norm = _norm(usage)
        self.calls += 1
        # THE CEILING COUNTS FIRST-TIME CONTENT (D-093), the same figure D-070 made the cost
        # headline: `input + cache_write + output`. It used to sum every stream, `cache_read`
        # included — and on a real 55-paper run that was 82% of the total, so the fuse tripped
        # at 24.4M while the work itself was 4.4M. A brake denominated differently from the
        # cost report is one nobody can size: it stopped a healthy run and read as an overrun.
        # `max_calls` remains the primary runaway-loop brake, and a genuine loop still trips
        # this one, because a loop generates `output` on every iteration.
        self.tokens_total += norm["input"] + norm["cache_write"] + norm["output"]
        self.tokens_all_streams += sum(norm[s] for s in STREAMS)
        if self.account is not None:
            self.account.record(usage, unit=unit, stage=stage)
        if self.tokens_total > self.max_tokens_total:
            self._trip("max_tokens_total", self.max_tokens_total, self.tokens_total)
        return norm

    # --- raise-only ceilings ----------------------------------------------------------
    def raise_ceiling(self, *, max_calls: int | None = None,
                      max_tokens_total: int | None = None,
                      max_tokens_per_call: int | None = None,
                      reason: str = "") -> None:
        """Widen a ceiling. Never narrows it, never grants unlimited.

        A lower ceiling could trip work already budgeted and in flight, and a fuse that can be
        narrowed mid-run is a scheduling knob rather than a safety device. Each widening logs,
        so a run that needed more says so in its own record.
        """
        for name, new in (("max_calls", max_calls),
                          ("max_tokens_total", max_tokens_total),
                          ("max_tokens_per_call", max_tokens_per_call)):
            if new is None:
                continue
            current = getattr(self, name)
            if new <= current:
                continue
            setattr(self, name, new)
            logger.info("lit2db fuse ceiling raised [%s]: %s %d -> %d%s",
                        self.label or "-", name, current, new,
                        f" ({reason})" if reason else "")

    # --- reporting --------------------------------------------------------------------
    def snapshot(self) -> dict:
        """Current state, for a run manifest. Headroom is the number an operator reads."""
        return {
            "label": self.label,
            "calls": self.calls,
            "max_calls": self.max_calls,
            "tokens_total": self.tokens_total,
            "max_tokens_total": self.max_tokens_total,
            "max_tokens_per_call": self.max_tokens_per_call,
            "calls_remaining": max(0, self.max_calls - self.calls),
            "tokens_remaining": max(0, self.max_tokens_total - self.tokens_total),
            "tokens_all_streams": self.tokens_all_streams,
        }

    def _trip(self, which: str, limit: int, observed: int):
        logger.error("lit2db fuse tripped [%s]: %s limit=%d observed=%d",
                     self.label or "-", which, limit, observed)
        raise FuseExceeded(which, limit, observed, self.label)
