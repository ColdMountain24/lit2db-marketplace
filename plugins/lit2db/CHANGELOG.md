# Changelog

## 0.22.0 — 2026-07-27
A ratified review-lane field is held, not written — and stops vetoing the row (D-064).

- `score_and_route` and `gate_upsert` take `review_lane`. Those fields are excluded from the
  record composite, **stripped before the write**, and returned as `held_for_review`.
- Measured: a record scored 0.385 because one free-prose field ratified to human review scored
  0.385, while its other nine fields scored 0.923-1.000 with three readings unanimous on eight.
  Auto-accept would have been zero by construction rather than by evidence.
- The exemption is only safe because of the strip: exempting without stripping would write
  unverified prose under a passing gate. Both halves are pinned by one test file.
- A record entirely in the review lane is denied, not written empty. The lane excuses nothing
  else — a retracted source or a contradiction elsewhere still denies.
- 371 → 380 tests.

## 0.21.0 — 2026-07-27
The headless wave driver, plus three defects the first real run exposed.

- **`scripts/run_wave.py`** — spawns every agent itself and runs the spine in-process, so a
  wave survives being left alone overnight. Resumes at the paper boundary, sleeps until the
  stated reset when it hits a usage limit, records a failed pass instead of silently shrinking
  k, and catalogues researcher-only questions to `QUESTIONS.jsonl` between waves.
- **`ground_literature` handles multi-valued fields** — a list was stringified whole, so
  `["(+)-δ-cadinol"]` scored 0.0 where the identical scalar scored 1.0. Every `list[...]` field
  in a frozen schema was unable to auto-accept, silently (D-061).
- **Dash confusables** — U+2010 HYPHEN and friends now fold to ASCII. NFKC does not touch them,
  and one paper's mixed typesetting split a single enzyme into two database rows.
- **Identity may be a fallback chain** — `accession, else (organism + name), else order of
  appearance` — and the alignment records WHICH rule matched, because they are not equally
  trustworthy (D-062).
- **`project_cost` counts agent invocations, not tokens** — across a 2.7× spread in paper
  length, per-pass cost was flat (correlation −0.14). Any token-proportional model is wrong in
  a fixed direction (D-060). `one_read=True` reproduces the historical figures.
- 343 → 371 tests.

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
