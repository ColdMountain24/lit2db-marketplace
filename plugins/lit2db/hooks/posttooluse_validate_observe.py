#!/usr/bin/env python3
"""PostToolUse hook (blueprint 7.3): two jobs.

1. On extract_record: run Pydantic validation of the returned record; on failure emit
   structured feedback to trigger a validate-and-retry.
2. On EVERY tool: emit an observability event (agent id, tool, latency, tokens, pass/fail)
   to the correctness dashboard sink (env LIT2DB_DASHBOARD_URL; stdout if unset).

Reads Claude Code hook JSON on stdin.
"""
import json, os, sys, urllib.request

def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        return
    tool = event.get("tool_name", "")
    evt = {"agent": event.get("agent_id"), "tool": tool,
           "latency_ms": event.get("duration_ms"), "ok": not event.get("is_error", False)}
    # 1) validate extractor output
    if tool == "extract_record":
        try:
            from lit2db.contracts import ExtractedRecord
            ExtractedRecord.model_validate(event.get("tool_response", {}))
            evt["validation"] = "pass"
        except Exception as e:
            evt["validation"] = f"fail: {type(e).__name__}"
            print(json.dumps({"decision": "block",
                  "reason": f"Pydantic validation failed: {e}. Re-extract and retry."}))
    # 2) observability
    sink = os.environ.get("LIT2DB_DASHBOARD_URL")
    if sink:
        try:
            urllib.request.urlopen(urllib.request.Request(sink,
                data=json.dumps(evt).encode(), headers={"Content-Type": "application/json"}),
                timeout=2)
        except Exception:
            pass  # never let observability break the pipeline
    else:
        sys.stderr.write("[obs] " + json.dumps(evt) + "\n")

if __name__ == "__main__":
    main()
