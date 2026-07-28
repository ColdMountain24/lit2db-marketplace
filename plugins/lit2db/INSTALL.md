# lit2db — INSTALL & Demo Walkthrough

A step-by-step guide to installing the lit2db plugin in Claude Code and running the
verification demo. Written to be followed live (e.g. in front of a PI). Every command below was
verified against the shipped package.

---

## 0. Prerequisites (do this once, before the demo)

The plugin's MCP server and hooks run under whatever `python3` Claude Code finds on your PATH.
That interpreter needs two packages:

```bash
python3 --version          # need 3.10+ (tested on 3.13)
python3 -m pip install "pydantic>=2" "mcp>=1.0"
```

**Why this matters:** the MCP server (`mcp`) and the contracts (`pydantic`) import these at
boot. If they're missing, the server fails to start and the `/lit2db-*` tools won't appear. This
is the single most likely thing to trip a live demo — install them first and confirm:

```bash
python3 -c "import mcp, pydantic; print('deps ok', pydantic.VERSION)"
```

You also need Claude Code itself (Node.js-based; install per the official docs at
https://docs.claude.com/en/docs/claude-code/overview). Confirm plugin support:

```bash
claude --version
```

---

## 1. Get the package onto disk

Unpack the marketplace repo somewhere stable (not /tmp):

```bash
mkdir -p ~/plugins && cd ~/plugins
tar xzf /path/to/lit2db-plugin.tar.gz
ls lit2db-marketplace/.claude-plugin/marketplace.json   # sanity check
```

You now have `~/plugins/lit2db-marketplace/`.

---

## 2. Install in Claude Code

Launch Claude Code, then run these two slash commands in the session:

```
/plugin marketplace add ~/plugins/lit2db-marketplace
/plugin install lit2db@lit2db-marketplace
```

- The first registers the local marketplace (Claude Code reads
  `.claude-plugin/marketplace.json`).
- The second installs the `lit2db` plugin from it — agents, hooks, MCP server, skill, and
  commands are all auto-discovered.

Restart the session if prompted so the MCP server and hooks load.

**Verify the install took:**

```
/help
```

You should see the four lit2db commands listed:

- `/lit2db-new-project` — run the Stage-0.5 scope-elicitation interview
- `/lit2db-extract` — one source end-to-end: store → k passes → merge → verify → route → gate
- `/lit2db-verify` — run the verify/route/gate spine over records
- `/lit2db-status` — selfcheck the loaded plugin, then report the ML-ready view

**Then confirm you got all 20 MCP tools**, not a stale subset:

```
/lit2db-status
```

It runs `scripts/selfcheck.py` against `${CLAUDE_PLUGIN_ROOT}` — the copy the session actually
launched — and stops loudly if the tools it declares aren't the tools you hold. The spine is
`validate_record`, `ground_literature`, `validate_mapping`, `score_and_route`, `gate_upsert`,
`db_query`; Stage 1 adds `build_store`, `locate_spans`; Stage 3 adds `merge_extraction_passes`,
`aggregate_ensemble`; and three reach the network and fail closed — `check_retraction`,
`resolve_access`, `rank_manual_queue`.

**If you see only 6, the plugin did not reload.** That is a real bug we hit: a stale marketplace
clone served v0.1.0 against a v0.9.0 repo for two sessions with no error anywhere. Fix with
`/plugin marketplace update`, reinstall, then `/reload-plugins`.

---

## 3. The 60-second demo — the verification thesis, executable

This is the piece to show live. It runs the deterministic spine over three records, **offline, no
network, no model calls**, and prints exactly what the database accepts and rejects.

```bash
cd ~/plugins/lit2db-marketplace/plugins/lit2db
python3 scripts/run_demo.py
```

**What you'll see, and what to say while it runs:**

| Record | What it is | Naive grounding | Adversarial judge | Gate | Why |
|---|---|---|---|---|---|
| **A** | clean single-condition Km | ✅ pass | ✅ SUPPORTED | **WRITE** | value cleanly supported by its quote |
| **B** | condition-multiplexed kcat ("73.6 and 40.8 … at 0.3% and 0.75%") | ✅ pass | ❌ AMBIGUOUS | **DENY → human-review** | number *appears* in the quote, so surface grounding passes — but it isn't bound to a single condition, and the judge catches that |
| **C** | value from a **retracted** paper | ✅ pass | ✅ SUPPORTED | **DENY → quarantine** | grounds and judge-supports, but the gate refuses a retracted source |

**The one-line takeaway for the room:** *naive grounding passes all three; only record A actually
enters the database. B is exactly the project's thesis — high surface grounding, caught by a
stricter adversarial judge — and C shows the gate enforces provenance rules grounding can't see.*

Expected final line of output:

```
ML-ready view (auto-accepted, active-source only): 1 record(s)
   demoA  enzyme_substrate_pair  conf=0.974
```

---

## 4. Run the tests (optional, for the skeptical reviewer)

```bash
cd ~/plugins/lit2db-marketplace/plugins/lit2db
python3 -m pip install "pytest>=7"
python3 -m pytest -q
```

Expect `330 passed, 1 skipped` (the skip is a network test; `LIT2DB_NETWORK_TESTS=1` runs it).

The two worth naming to a skeptic are `test_smoke.py` — the ratification-ledger invariant, i.e.
an agent provably cannot slip an unratified field into a frozen schema — and `test_spine.py`,
which is the demo thesis above encoded as assertions. If either stops reproducing the
"grounded ≠ accepted" contrast, the thesis is broken, not just a test.

The rest pin the parts that have actually bitten us: `test_write_gate` (both enforcement points,
one predicate), `test_retraction` (fail-closed source status), `test_store` (the offset
contract), `test_ensemble` + `test_merge_passes` (agreement, and cross-pass alignment on the
ratified identity field), `test_retained_source` (a quote may not cite text the extractor never
read), and `test_selfcheck` (the stale-install bug above).

---

## 5. Instantiate for a real domain (what happens after the demo)

```
/lit2db-new-project my-domain
```

This launches the scope-elicitation agent, which walks the ten narrowing axes and builds a
**ratification ledger** you approve item by item. The schema only freezes once every field traces
to something you ratified — the agent proposes structure, you own the substance. From there,
ingestion and extraction begin against your sources, and every extracted value flows through the
same verify → route → gate spine you just watched in the demo.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/lit2db-*` commands don't appear | MCP server failed to boot — almost always missing `mcp`/`pydantic` in the `python3` on PATH. Re-run Step 0, restart the session. |
| `ModuleNotFoundError: lit2db` when running the demo | Run from `plugins/lit2db/` (Step 3 cds there) or set `PYTHONPATH=$PWD/src`. |
| MCP server starts but `db_query` errors | `LIT2DB_DB_PATH` points at `examples/demo.db`; the demo creates its own temp DB, so run `run_demo.py` first or point the var at a writable path. |
| Hooks don't fire | Confirm `${CLAUDE_PLUGIN_ROOT}` resolved — check the plugin installed cleanly with `/plugin list`. |

---

## What's real vs. what's stubbed (be honest with the room)

- **Real today:** install path, all 13 MCP tools, the 3 hooks, the deterministic verify→route→gate
  spine, the offline demo, the 330 tests, the elicitation interview.
- **Supplied by the orchestrator at runtime:** the *adversarial judge* verdict. The
  MCP server ships a naive lexical/numeric grounding baseline so it's self-contained and testable;
  the strict judge that produced the 39%-flagged pilot result runs in Claude Code itself and feeds
  its verdict into the score. In the demo, the judge verdicts are pre-recorded fixtures so the
  thesis is reproducible without model calls.
- **The judge's independence is limited, and we state it (D-041).** By default the judge is a
  different *model* from the extractor but the **same family** (Opus judging Sonnet), because a
  second provider meters every call. Do not describe this as cross-family verification. The bias
  runs one way — shared training means the judge agrees with the extractor more than it should —
  so any F1-vs-factual-accuracy gap measured under this wiring is a **lower bound**. A different
  provider is opt-in per D-025.
