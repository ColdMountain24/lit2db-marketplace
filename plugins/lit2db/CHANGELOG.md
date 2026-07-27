# Changelog

## 0.20.0 — 2026-07-27
Agent contracts may no longer direct an agent to call a tool it does not hold (D-059).

- **Three contracts were telling agents to call MCP tools they do not have.** No agent in this
  plugin declares any MCP tool — the orchestrator calls the spine and hands back the verdict —
  but `extractor-agent` said "call `locate_spans`", `contradiction-hunter-agent` was required to
  return a `char_offset` with no tool that produces one, and `ingest-agent` was told to call
  `resolve_access`, `check_retraction` and `rank_manual_queue`. An agent directed at a tool it
  lacks does not error; it improvises.
- **`ingest-agent` is the consequential one.** `check_retraction` and `resolve_access` are
  fail-closed gates, so an improvised verdict yields a confident, well-formed record from a
  withdrawn or paywalled source. The contract now says: supply the inputs, stop until the verdict
  arrives, never stamp a status yourself, and never read silence as a pass.
- **`tests/test_agent_contracts.py`** enforces the rule going forward — a directive verb governing
  an undeclared MCP tool fails, unless a negation immediately governs that verb. The wording that
  actually shipped is pinned as a regression fixture, as are the two false positives found while
  writing it. 330 → 343 tests.

> Versions 0.2.0–0.19.0 are not recorded here. Their history lives in the git tags and in the
> workbench ledgers (`dev/DECISIONS.md`, `dev/JOURNAL.md`); this file went unmaintained after
> 0.1.0 and backfilling it from memory would be worse than saying so.

## 0.1.0 — 2026-07-19
Initial Claude Code plugin packaging of the lit2db reference implementation.

- **Plugin + marketplace manifests** (`.claude-plugin/`), installable via
  `/plugin marketplace add` -> `/plugin install lit2db@lit2db-marketplace`.
- **6 stage-specialized agents** (scope-elicitation, ingest, extractor, verifier-judge,
  entity-resolver, schema-architect) — ported unchanged from the scaffold.
- **Deterministic control-spine hooks** (write-gate, validate/observe, cost-cap/checkpoint)
  wired through `hooks/hooks.json` with `${CLAUDE_PLUGIN_ROOT}` paths.
- **lit2db MCP server** exposing the verification/routing/gate spine as callable tools:
  `validate_record`, `ground_literature`, `validate_mapping`, `score_and_route`,
  `gate_upsert`, `db_query`. Self-contained (SQLite), no external services.
- **Stage-0.5 scope-elicitation skill** — the agent-proposes-structure / researcher-ratifies-
  substance protocol, domain-invariant.
- **3 slash commands**: `/lit2db-new-project`, `/lit2db-verify`, `/lit2db-status`.
- **End-to-end offline demo** (`scripts/run_demo.py`) + spine tests reproducing the thesis:
  naive grounding passes a condition-multiplexed value; the adversarial judge flags it; the
  gate denies it — nothing wrong lands silently in the DB.
- The `src/lit2db/` contracts (the ratification-ledger invariant) are unchanged from the
  scaffold. **Net domain content in the scaffold: zero.**
