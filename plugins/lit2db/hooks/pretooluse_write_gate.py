#!/usr/bin/env python3
"""PreToolUse hook on db_upsert: the HARD write-gate (blueprint 7.3).

Deny the write if the record's composite confidence is below the auto-accept threshold,
OR if any field routes to quarantine/human_review, OR if source_status is not active.
"deny" wins in the Claude Code permission pipeline -- this is the deterministic gate that
wraps the non-deterministic extractor.

Reads the Claude Code hook JSON on stdin; emits a permission decision on stdout.
Threshold is read from the active instantiation config (env LIT2DB_AUTOACCEPT, default 0.95).
"""
import json, os, sys

AUTOACCEPT = float(os.environ.get("LIT2DB_AUTOACCEPT", "0.95"))

def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        # fail closed: if we cannot parse, do not allow the write
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
              "permissionDecision": "deny",
              "permissionDecisionReason": "write-gate: unparseable hook payload"}}))
        return
    if event.get("tool_name") != "db_upsert":
        return  # not our tool; stay silent (allow)
    rec = event.get("tool_input", {}).get("record", {})
    reasons = []
    conf = rec.get("confidence")
    if conf is None or conf < AUTOACCEPT:
        reasons.append(f"composite confidence {conf} < auto-accept {AUTOACCEPT}")
    for fv in rec.get("fields", []):
        if fv.get("route") in ("quarantine", "human_review"):
            reasons.append(f"field '{fv.get('field_name')}' routed to {fv.get('route')}")
    for fv in rec.get("fields", []):
        prov = fv.get("provenance", {})
        if prov.get("source_status", "active") != "active":
            reasons.append(f"source_status={prov.get('source_status')} (not active)")
            break
    decision = "deny" if reasons else "allow"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
          "permissionDecision": decision,
          "permissionDecisionReason": "write-gate: " + ("; ".join(reasons) if reasons
                                        else "passes auto-accept")}}))

if __name__ == "__main__":
    main()
