---
description: Show the current ML-ready database view — auto-accepted, non-retracted records only.
---
**First, verify you are talking to the plugin you think you are.** Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/selfcheck.py"
```

Then do the check the script cannot do for itself: compare the MCP tool names it lists against
the `mcp__plugin_lit2db_lit2db__*` tools **you actually hold in this session**. If you hold
fewer, the plugin did not reload — say so loudly and stop, because every capability claim below
is then untestable and any measurement you take is of the wrong artifact. The fix is
`/plugin marketplace update`, reinstall, then `/reload-plugins`.

This is not ceremony. A stale marketplace clone once left v0.1.0 installed against a v0.9.0
repo: 6 MCP tools instead of 16, an `extractor-agent` holding only `Read`, and no error
anywhere. It cost two sessions and was invisible the whole time.

**Then** call the lit2db MCP `db_query` tool and summarize the ML-ready view: how many records passed
the gate, their entity types and confidence distribution. Remind the researcher that this view
excludes quarantined, human-review-pending, and retracted/superseded records by construction —
that exclusion is what makes the view auditable.
