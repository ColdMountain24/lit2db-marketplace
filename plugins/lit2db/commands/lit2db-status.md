---
description: Show both tiers — the ML-ready database, and the candidate pool waiting for a human.
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
repo: 6 MCP tools instead of 20, an `extractor-agent` holding only `Read`, and no error
anywhere. It cost two sessions and was invisible the whole time.

**Then report BOTH tiers**, because the product is both.

1. `db_query` — the **ML-ready** database: how many records cleared the gate, their entity types
   and confidence distribution. This view excludes quarantined, human-review-pending and
   retracted/superseded records by construction; that exclusion is what makes it auditable.
2. `review_queue` — the **candidate pool**: everything the pipeline produced that did not clear
   the bar, best-first, each with the reason it stopped short.

Report them together and never report the first alone. A run that auto-accepted 5 of 45 records
did not fail 40 times — it produced 5 finished rows and 40 candidates that each already carry
their quote, character offset, grounding score, cross-pass agreement and judge verdict. That is
most of the work of confirming one. **The pipeline accelerates curation; it does not replace it,
and a status report that shows only the accepted tier hides the acceleration.**

Say plainly which reasons dominate the queue. "Forty records short of unanimous agreement" and
"forty records contradicted by their own source" are opposite findings and should never look
alike.
