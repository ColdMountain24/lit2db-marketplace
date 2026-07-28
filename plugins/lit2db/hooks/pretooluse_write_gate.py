#!/usr/bin/env python3
"""PreToolUse hook — the HARD write-gate at the permission layer (blueprint 7.3).

"deny" wins the Claude Code permission pipeline, so this hook stops a sub-threshold write
*before the tool runs at all*. It fires on the tool the MCP server actually exposes —
which reaches hooks namespaced as `mcp__<server>__gate_upsert` — as well as on the
in-process `db_upsert` stub.

The identical predicate runs again inside `gate_upsert` itself: one implementation
(`lit2db.gate`), two enforcement points. See that module for why both exist.

An `allow` here is deliberate, not an oversight: a record that clears the ratified
auto-accept bar is written mechanically, without asking a human per row — that is what
auto-accept means. Everything else is denied and routed to the human-review / quarantine
queue, with the reasons attached.

Reads the Claude Code hook JSON on stdin; emits a permission decision on stdout.
Threshold precedence: the call's `autoaccept` arg > env LIT2DB_AUTOACCEPT > 0.95.
Fails CLOSED — an unparseable payload, or a gate module that will not import, denies.
"""
import json
import os
import sys
from pathlib import Path


def emit(decision: str, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": f"write-gate: {reason}"}}))


# The hook's own location is the authoritative plugin root: a wrong CLAUDE_PLUGIN_ROOT must
# not be able to disable the gate. A failed import denies rather than falling open — a
# non-blocking exit code would let the write through, which is the failure mode being fixed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    from lit2db.gate import (gate_reasons, is_write_tool, resolve_composite,
                             resolve_min_populated, resolve_threshold)
except Exception as exc:  # pragma: no cover - exercised only on a broken install
    emit("deny", f"gate module unavailable ({exc})")
    sys.exit(0)


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        emit("deny", "unparseable hook payload")
        return
    if not isinstance(event, dict) or not is_write_tool(event.get("tool_name")):
        return  # not a write tool; stay silent and leave the permission flow alone
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        emit("deny", "write call carried no arguments")
        return
    reasons = gate_reasons(
        tool_input.get("record"),
        resolve_composite(tool_input),
        resolve_threshold(tool_input, os.environ),
        review_lane=tool_input.get("review_lane") or (),
        # D-101: ALL of the predicate's conditions are applied here, not four of six. The hook
        # used to pass neither of these two, so it and `gate_upsert` applied different rules —
        # untested, since v0.27.0. `required_fields` denied in the safe direction and
        # `review_lane` in the unsafe one, which is exactly why neither was noticed.
        required_fields=tool_input.get("required_fields") or (),
        min_populated_fields=resolve_min_populated(tool_input, os.environ),
    )
    if reasons:
        emit("deny", "; ".join(reasons))
    else:
        emit("allow", "passes auto-accept")


if __name__ == "__main__":
    main()
