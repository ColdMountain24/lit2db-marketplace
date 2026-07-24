# lit2db — literature & structured data → ML-ready databases

A Claude Code plugin for building **auditable, versioned, ML-ready databases** from
scientific literature and structured data sources — for *any* domain. You supply the research
question, the entities of interest, and the domain substance; the agents handle the structural
and mechanical labor (ingestion, extraction, verification, routing, output).

**One design constraint runs through everything: the agent formalizes and executes; the
researcher originates and ratifies all domain substance.** An agent may propose structure; it
may never introduce domain content the researcher did not name and ratify. This boundary is
enforced *structurally* — the frozen schema is exactly the set of researcher-ratified
ratification-ledger items, and the `SchemaReadySpec` contract refuses to build otherwise.

## Why this exists — verification, not extraction, is the hard problem

High extraction F1 does **not** imply factual accuracy: an extractor at F1 ≈ 0.87 can collapse
to ≈ 47% factual accuracy under source-grounded judging. So the engineering investment goes
into the **verification layer** — citation-grounded checks, a cross-family adversarial judge, a
composite confidence, and a confidence-gated human-review router — not into the extractor. This
plugin makes that layer the centerpiece: a deterministic verify → route → gate spine wraps the
non-deterministic LLM extractor, and nothing reaches the database without clearing it.

## Install

```
/plugin marketplace add ColdMountain24/lit2db-marketplace
/plugin install lit2db@lit2db-marketplace
```

Local development:

```
/plugin marketplace add ./lit2db-marketplace
/plugin install lit2db@lit2db-marketplace
```

## What's in the box

| Component | What it is |
|---|---|
| **6 agents** (`agents/`) | Stage-specialized subagents: scope-elicitation (Opus), ingest, extractor, **verifier-judge (a *different* model family)**, entity-resolver, schema-architect. |
| **3 hooks** (`hooks/`) | The deterministic control spine: a hard PreToolUse **write-gate**, a PostToolUse Pydantic-validate + observability emitter, a Stop/SessionEnd cost-cap + checkpoint. |
| **MCP server** (`mcp/`) | The verify/route/gate spine as callable tools — `validate_record`, `ground_literature`, `validate_mapping`, `score_and_route`, `gate_upsert`, `db_query`. Self-contained (SQLite); no external services. |
| **Skill** (`skills/scope-elicitation/`) | The Stage-0.5 protocol: ten narrowing axes, the ratification ledger, the propose-structure / ratify-substance boundary. |
| **Commands** (`commands/`) | `/lit2db-new-project`, `/lit2db-verify`, `/lit2db-status`. |
| **Contracts** (`src/lit2db/`) | Pydantic formalization of the ledger invariant, provenance record, evidence tier, confidence composite, and routing. **Domain-blind.** |

## The two-layer architecture

- **Domain-invariant scaffold** — stages, verification machinery, provenance model, routing
  discipline, the source-adapter contract. This is the reusable machine; it carries **zero**
  domain content.
- **Per-project instantiation** (`instantiation/<project>/`) — the *only* place domain substance
  lives, produced through the Stage-0.5 elicitation interview and gated by the ratification
  ledger. Two source adapters converge at the verification layer: a **literature adapter**
  (span-entailment grounding) and a **structured-data adapter** (mapping-validation grounding,
  which bypasses extraction).

## Nine stages

Control Plane → Ingest → **Stage 0.5 Scope Elicitation** → Schema Design → Extract → **Verify**
→ Cross-Paper Entity Resolution → Route → Output → (Self-Improve loop).

## Try the demo (offline, no network)

```
python3 scripts/run_demo.py
```

Three records go through the spine:

- **A** — a clean single-condition Km value → grounds, judge supports → **auto-accept, written**.
- **B** — a condition-multiplexed turnover number (`73.6 and 40.8 … at 0.3% and 0.75%`). The
  number *appears* in its quote so **naive grounding passes** — but the adversarial judge flags
  it **AMBIGUOUS**, so it routes to human-review and the gate **denies** it.
- **C** — a value from a **retracted** source → grounds and judge-supports, but the gate
  **denies** on `source_status=retracted`.

Only A lands in the ML-ready view. B and C never silently enter the database. That contrast —
high surface grounding, gated by a stricter judge — is the whole thesis in one run.

## Instantiate for your own domain

```
/lit2db-new-project my-domain
```

Runs the scope-elicitation agent through the ten axes, building a ratification ledger you
approve item by item. Only after every field traces to a ratified item does a schema freeze,
and only then does ingestion begin.

## License

MIT (see plugin manifest). The scaffold is domain-blind; domain substance and any redistributed
source material are governed by your project's own instantiation and licensing choices.
