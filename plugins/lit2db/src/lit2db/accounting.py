"""Token accounting — the comparable unit of computational cost.

**Tokens are primary; currency is a derived figure and is often misleading.** A researcher
on a flat-rate subscription pays the same whether a run consumes 10K tokens or 10M, so an
API-equivalent dollar figure describes neither what they paid nor what they can afford. It
also cannot compare two operators on different plans. Tokens can: they are what plan limits
count, they don't move when prices do, and they are the only unit that makes a
just-Claude / Claude-Code / Claude-Code-plus-plugin comparison (D-016) mean anything.

So this module reports tokens. `api_equivalent_cost` exists for callers who genuinely pay
per token, takes its rates as an argument (prices go stale; the scaffold must not hardcode
them), and is labelled as an equivalent — never as "what this cost you".

Deliberately STDLIB-ONLY so hooks can import it, same constraint as `lit2db.gate`.
Domain-INVARIANT: units of work are opaque labels supplied by the caller.
"""
from __future__ import annotations

# Cache reads and writes are not priced like fresh input, so a single "total tokens" number
# understates or overstates depending on the mix. Keep the four streams separate and let the
# caller decide which sum it wants.
STREAMS = ("input", "output", "cache_read", "cache_write")


def _norm(usage) -> dict:
    """Accept a usage mapping from any source (hook payload, API response, hand-entered
    /usage delta) and normalize to the four streams. Unknown keys are ignored; missing
    streams are zero. Accepts both `cache_read` and the API's `cache_read_input_tokens`."""
    if not isinstance(usage, dict):
        return {s: 0 for s in STREAMS}
    alias = {
        "input": ("input", "input_tokens"),
        "output": ("output", "output_tokens"),
        "cache_read": ("cache_read", "cache_read_input_tokens"),
        "cache_write": ("cache_write", "cache_creation_input_tokens"),
    }
    out = {}
    for stream, keys in alias.items():
        val = 0
        for k in keys:
            if k in usage:
                try:
                    val = max(0, int(usage[k]))
                except (TypeError, ValueError):
                    val = 0
                break
        out[stream] = val
    return out


class RunAccount:
    """Accumulates token usage per unit of work and per pipeline stage.

    A "unit" is whatever the caller is costing — a source document, a record, a study arm.
    A "stage" is where the tokens went (ingest / extract / judge / resolve). Keeping both
    axes is the point: a per-corpus total tells you whether a run fits in a plan window,
    while the per-stage split tells you *what to cut* when it doesn't. In practice the
    extractor dominates, because it is the only stage that reads full source text.
    """

    def __init__(self, label: str = ""):
        self.label = label
        self._by_unit: dict = {}
        self._by_stage: dict = {}

    def record(self, usage, unit: str = "", stage: str = "") -> dict:
        """Add one observation. Returns the normalized usage that was recorded."""
        norm = _norm(usage)
        for key, bucket in ((unit or "(unattributed)", self._by_unit),
                            (stage or "(unattributed)", self._by_stage)):
            acc = bucket.setdefault(key, {s: 0 for s in STREAMS})
            for s in STREAMS:
                acc[s] += norm[s]
        return norm

    def totals(self) -> dict:
        t = {s: 0 for s in STREAMS}
        for acc in self._by_unit.values():
            for s in STREAMS:
                t[s] += acc[s]
        return t

    @property
    def n_units(self) -> int:
        return len([u for u in self._by_unit if u != "(unattributed)"])

    def per_unit_mean(self) -> dict:
        """Mean tokens per unit — the number that extrapolates. Measure on a handful of
        units, multiply by the corpus size, compare against a plan window."""
        n = self.n_units
        if not n:
            return {s: 0.0 for s in STREAMS}
        t = self.totals()
        return {s: t[s] / n for s in STREAMS}

    def project(self, n_units: int) -> dict:
        """Project totals for a corpus of `n_units`, from the per-unit mean measured so far.
        This is the calibration path: run a small, size-representative sample, project, and
        report the projection WITH the sample size — never present it as a measured total."""
        mean = self.per_unit_mean()
        return {"n_units": n_units, "measured_on": self.n_units,
                "projected": {s: round(mean[s] * n_units) for s in STREAMS}}

    def api_equivalent_cost(self, rates: dict) -> dict:
        """API-equivalent spend, for callers who actually pay per token.

        `rates` is per-million-token pricing, supplied by the caller: e.g.
        {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}.
        Rates are NOT hardcoded here — published prices change, and a stale constant baked
        into a scaffold silently produces wrong numbers forever.

        **This is not what a subscription user paid.** On a flat-rate plan the marginal cost
        of a run is zero until a limit is hit; report it as an equivalent or not at all.
        """
        t = self.totals()
        by = {s: (t[s] / 1_000_000) * float(rates.get(s, 0) or 0) for s in STREAMS}
        return {"usd_equivalent": round(sum(by.values()), 4),
                "by_stream": {s: round(v, 4) for s, v in by.items()},
                "caveat": "API-equivalent only; a flat-rate subscription user did not pay this"}

    def report(self) -> dict:
        """Full accounting. Tokens only — attach currency separately if a caller wants it."""
        return {"label": self.label, "unit": "tokens", "n_units": self.n_units,
                "totals": self.totals(),
                "per_unit_mean": {s: round(v, 1) for s, v in self.per_unit_mean().items()},
                "by_unit": dict(self._by_unit), "by_stage": dict(self._by_stage)}
