# Changelog

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
