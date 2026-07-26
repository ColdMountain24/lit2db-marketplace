#!/usr/bin/env python3
"""Stop / SessionEnd hook (blueprint 7.3, 7.4): checkpoint + hard budget cap.

**The cap is expressed in TOKENS, not currency.** A flat-rate subscription user pays the
same whether a run burns 10K tokens or 10M, so a dollar cap neither protects them nor
describes what they spent — what actually runs out is plan capacity, counted in tokens.
Currency appears only where a caller supplies its own rates, and only as an equivalent.

Caps stay expressed as a RATIO to measured gold-set usage so they transfer across domains
(blueprint Stage 0) — that idea was right; only the unit was wrong.

  LIT2DB_TOKEN_CAP_RATIO   multiple of gold-set tokens allowed   (default 3.0)
  LIT2DB_GOLD_TOKENS       tokens the gold-set run consumed      (0 = cap disabled)

Fails OPEN by design: this is a budget guard, not a correctness gate. A hook that cannot
read its own input must not wedge a long agentic run. The write-gate is what protects data
integrity, and that one fails closed.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    from lit2db.accounting import STREAMS, _norm
except Exception:  # accounting is best-effort; never wedge the session over it
    STREAMS, _norm = ("input", "output", "cache_read", "cache_write"), None


def main() -> None:
    cap_ratio = float(os.environ.get("LIT2DB_TOKEN_CAP_RATIO", "3.0") or "3.0")
    gold_tokens = float(os.environ.get("LIT2DB_GOLD_TOKENS", "0") or "0")
    try:
        event = json.load(sys.stdin)
    except Exception:
        return  # fail open

    usage = {}
    for key in ("usage", "session_usage", "total_usage"):
        if isinstance(event.get(key), dict):
            usage = event[key]
            break
    counts = _norm(usage) if _norm else {s: 0 for s in STREAMS}
    spent = sum(counts.values())

    # Report the split, not just the total: when a run overruns, the per-stream breakdown is
    # what tells you whether to trim context, cut the corpus, or lean harder on caching.
    sys.stderr.write("[checkpoint] tokens: "
                     + "  ".join(f"{s}={counts[s]:,}" for s in STREAMS)
                     + f"  total={spent:,}\n")

    if gold_tokens > 0 and spent > cap_ratio * gold_tokens:
        print(json.dumps({"decision": "block",
              "reason": f"circuit breaker: {spent:,} tokens exceeds {cap_ratio}x the "
                        f"gold-set run ({gold_tokens:,.0f}). Halting so a runaway loop "
                        f"cannot quietly consume the operator's plan capacity."}))


if __name__ == "__main__":
    main()
