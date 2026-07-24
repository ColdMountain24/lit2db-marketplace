#!/usr/bin/env python3
"""Stop / SessionEnd hook (blueprint 7.3, 7.4): checkpoint + hard cost cap.

Cost enforcement is per-tool-call in the PostToolUse stream; this hook is the checkpoint
and the last line of defense. Caps are expressed as a RATIO to measured gold-set cost so
they transfer across domains (blueprint Stage 0). Trips a circuit breaker on exceed.
"""
import json, os, sys

CAP_RATIO = float(os.environ.get("LIT2DB_COST_CAP_RATIO", "3.0"))   # x gold-set cost
GOLD_COST = float(os.environ.get("LIT2DB_GOLD_COST", "0") or "0")

def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        return
    spent = float(event.get("session_cost_usd", 0) or 0)
    # checkpoint progress + spend ledger (sink is project memory in the real impl)
    sys.stderr.write(f"[checkpoint] session_cost=${spent:.4f}\n")
    if GOLD_COST > 0 and spent > CAP_RATIO * GOLD_COST:
        print(json.dumps({"decision": "block",
              "reason": f"circuit breaker: ${spent:.2f} exceeds {CAP_RATIO}x gold-set "
                        f"cost (${GOLD_COST:.2f}). Halting."}))

if __name__ == "__main__":
    main()
