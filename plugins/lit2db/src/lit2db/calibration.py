"""Turn adjudicated candidates into a precision table. Domain-INVARIANT: nothing here knows
what a compound is, and nothing here picks a threshold.

WHAT THIS IS FOR. `gate.py` takes an accept threshold as a number. Nothing in this project has
ever been able to say where that number should sit, because saying so requires knowing how often
records at each setting are actually right — which requires labels. `output.record_adjudication`
collects the labels as a by-product of a researcher confirming candidates they were going to
confirm anyway; this module reads them back as a table.

THREE THINGS IT REFUSES TO DO, each one a mistake this project has already made once:

  1. **It does not pick a threshold.** It reports precision at each setting with the sample size
     and an interval, and stops. Choosing the operating point is a promise to the user of the
     database about how wrong it may be, and that is the researcher's to ratify.
  2. **It does not treat `cant_tell` as `wrong`.** Unverifiable records are dropped from the
     denominator and COUNTED SEPARATELY, because a bar calibrated on "we could not check" is
     calibrated on the reach of a library subscription. The count is reported so a table resting
     on ten verifiable records out of a hundred cannot look like a table resting on a hundred.
  3. **It does not hide small n.** Every row carries its n and a Wilson interval. A bucket of
     three records reading 100% is the shape of the finding this project has had to retract
     three times (`f_extract=0.5`, the 8.0x multiple, the 215M figure).
"""
from __future__ import annotations

import math

# Verdicts that count toward precision, and the one that does not. Imported from `output` would
# be circular, so the vocabulary is stated once here and pinned by a test in both places.
CORRECT = "right"
INCORRECT = "wrong"
UNVERIFIABLE = "cant_tell"


def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    """95% Wilson score interval for k successes in n trials.

    Wilson rather than the normal approximation because calibration lives at the ends: 20 of 20
    prints +/- 0.0 under the normal approximation and reads as certainty from twenty
    observations, which is exactly the misreading that would make a bar look calibrated when it
    is not. Returns (0.0, 1.0) at n=0 — no observations means no information, not 0% precision.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def precision(rows: list) -> dict:
    """Precision over adjudicated rows, with `cant_tell` held OUT of the denominator.

    Returns the point estimate, the interval, the verifiable denominator, and the number set
    aside — all four, because the first on its own is what makes a 3-of-3 look like a result.
    """
    right = sum(1 for r in rows if r.get("verdict") == CORRECT)
    wrong = sum(1 for r in rows if r.get("verdict") == INCORRECT)
    unverifiable = sum(1 for r in rows if r.get("verdict") == UNVERIFIABLE)
    n = right + wrong
    lo, hi = wilson(right, n)
    return {"n_verifiable": n, "n_right": right, "n_wrong": wrong,
            "n_unverifiable": unverifiable,
            "precision": (right / n) if n else None,
            "ci95": [lo, hi]}


def table(rows: list, key) -> list:
    """Precision per bucket, sorted by bucket. `key` is a callable over one adjudicated row.

    The bucket is the caller's choice on purpose: a project may calibrate on the composite, on
    record completeness, on the judge's verdict, or on a tuple of them. What signals a database
    should be calibrated against is a per-project question, and hard-coding one here would be
    this scaffold inventing calibration policy — the boundary the whole plugin is built around.
    """
    buckets: dict = {}
    for r in rows:
        buckets.setdefault(key(r), []).append(r)
    out = []
    for bucket in sorted(buckets, key=lambda b: (str(type(b)), b)):
        stats = precision(buckets[bucket])
        stats["bucket"] = bucket
        stats["n_total"] = len(buckets[bucket])
        out.append(stats)
    return out


def frontier(rows: list, score) -> list:
    """Precision and yield at every threshold the observed scores can actually take.

    Sweeping a continuous grid over a QUANTIZED score invents settings that cannot differ: the
    composite lands on a short lattice, so two thresholds between adjacent rungs are one
    threshold. This enumerates the distinct observed values instead, which is the only set of
    settings a calibration can distinguish between.
    """
    values = sorted({s for s in (score(r) for r in rows) if s is not None})
    out = []
    for bar in values:
        kept = [r for r in rows if (score(r) is not None and score(r) >= bar)]
        stats = precision(kept)
        stats["bar"] = bar
        stats["n_accepted"] = len(kept)
        # Yield against the ADJUDICATED pool, not against the literature. This is "how much of
        # what you looked at would be written", never a recall claim — recall needs a
        # denominator of what exists, which no database has about itself.
        stats["kept_fraction"] = len(kept) / len(rows) if rows else 0.0
        out.append(stats)
    return out


def render(table_rows: list, label: str = "bucket") -> str:
    """One fixed-width block, for a command that has to show this to a human."""
    head = (f"  {label:<28} {'right':>6} {'wrong':>6} {'prec':>6} {'95% CI':>13} "
            f"{'unverifiable':>13}")
    lines = [head, "  " + "-" * (len(head) - 2)]
    for r in table_rows:
        p = "  n/a " if r["precision"] is None else f"{r['precision'] * 100:>5.0f}%"
        ci = (f"[{r['ci95'][0] * 100:>3.0f},{r['ci95'][1] * 100:>3.0f}]"
              if r["n_verifiable"] else "     —      ")
        key = r.get("bucket", r.get("bar"))
        lines.append(f"  {str(key):<28} {r['n_right']:>6} {r['n_wrong']:>6} {p:>6} "
                     f"{ci:>13} {r['n_unverifiable']:>13}")
    return "\n".join(lines)
