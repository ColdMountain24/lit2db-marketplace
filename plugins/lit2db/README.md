# lit2db — scientific literature → ML-ready databases

A Claude Code plugin for building **auditable, versioned, ML-ready databases** from
scientific literature — for *any* domain. You supply the research
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
into the **verification layer** — citation-grounded checks, a confidence score over mechanical
signals, an adversarial judge that can veto what the score accepts, and a human-review router —
not into the extractor. This
plugin makes that layer the centerpiece: a deterministic verify → route → gate spine wraps the
non-deterministic LLM extractor, and nothing reaches the database without clearing it.

## Install

```
/plugin marketplace add ColdMountain24/lit2db-marketplace
/plugin install lit2db@lit2db-marketplace
```

**Then quit Claude Code and start a new session.** `/reload-plugins` does *not* restart the
plugin's MCP server — it refreshes skills, commands, agents and hooks only, so a stale server
will keep serving an old version's tools with no error anywhere. Only a fresh session picks up
new MCP tools. `INSTALL.md` has the full walkthrough and a troubleshooting table.

Local development:

```
/plugin marketplace add ./lit2db-marketplace
/plugin install lit2db@lit2db-marketplace
```

## Start here

```
/lit2db-start
```

Four questions in your own language — what you want to collect, what one row should be, which
papers count, and what you would do with the finished table. It hands off automatically to the
Stage-0.5 interview carrying your answers, so you never have to know the stage numbering below.
Everything else in this README is what happens underneath.

## What's in the box

| Component | What it is |
|---|---|
| **7 agents** (`agents/`) | Stage-specialized subagents: scope-elicitation (Opus), ingest, extractor, **verifier-judge** (a different *model* from the extractor — Opus judging Sonnet; a different *provider* is opt-in, see below), entity-resolver, schema-architect, and **contradiction-hunter** (audits the extractor's choice of evidence — see below). |
| **3 hooks** (`hooks/`) | The deterministic control spine: a hard PreToolUse **write-gate**, a PostToolUse Pydantic-validate + observability emitter, a Stop/SessionEnd cost-cap + checkpoint. |
| **22 MCP tools** (`mcp/`) | The deterministic spine as callable tools — `build_store` + `build_abstract_store` + `locate_spans` (Stage 1), `merge_extraction_passes` + `aggregate_ensemble` (Stage 3), `validate_record`, `ground_literature`, `validate_mapping`, `score_and_route`, `gate_upsert`, `db_query`, `resolve_entities`, `resolve_structure`, the review/calibration set (`review_queue`, `record_candidate`, `record_adjudication`, `calibration_report`, `rank_manual_queue`), corpus build (`screen_corpus`, `dedupe_corpus`), plus `check_retraction` (Crossref) and `resolve_access` (Unpaywall). SQLite-backed; the network lookups are the only calls that leave the machine and all fail closed. |
| **Skill** (`skills/scope-elicitation/`) | The Stage-0.5 protocol: ten narrowing axes, the ratification ledger, the propose-structure / ratify-substance boundary. |
| **7 commands** (`commands/`) | **`/lit2db-start`** (start here — one guided intake in plain language), `/lit2db-new-project` (the long-form interview), `/lit2db-extract` (one source end-to-end), `/lit2db-verify`, `/lit2db-status`, `/lit2db-review` (confirm candidates, building your calibration set), `/lit2db-review-ui` (the same loop in a browser, each candidate beside its paragraph). |
| **Contracts** (`src/lit2db/contracts/`, `gate.py`) | Pydantic formalization of the ledger invariant, provenance record, evidence tier, confidence composite, and routing, plus the write-gate predicate the hook and the MCP tool both apply. **Domain-blind.** |

### One artifact: what runs is what ships

Until v0.33.0 three subpackages shipped as typed scaffolding — `stages/`, `adapters/`, `tools/` —
on the argument that the contract *is* the design. **They have been deleted.** The argument was
wrong in a specific and costly way: `stages/` declared itself "the domain-INVARIANT control flow"
while eight of its nine functions had empty bodies and nothing imported the package. The control
flow really lived in `scripts/run_wave.py`, which reached scoring and gating by loading the MCP
*server file* as a module to borrow functions out of it.

So the library was a specification and a script was the system, and the project's first-week
defect stream was almost entirely the gap between them: an agent declaring tools it did not hold,
weights for signals nothing produced, a stage recorded as "found nothing" that never ran. One bug
class, and it recurred because each fix re-described the specification instead of closing the gap.

The pipeline now lives in the library — `lit2db.pipeline`, `lit2db.scoring`, `lit2db.grounding`,
`lit2db.output` — and the MCP server and the headless driver are both thin callers of it. The
structured-data adapter is **not implemented**; where this README once said "literature and
structured data" as a product claim, it now says literature, because that is what exists.

`tests/test_declarations.py` keeps it that way: the plugin may not claim what it does not do.

## The two-layer architecture

- **Domain-invariant scaffold** — stages, verification machinery, provenance model, routing
  discipline, the source-adapter contract. This is the reusable machine; it carries **zero**
  domain content.
- **Per-project instantiation** (`instantiation/<project>/`) — the *only* place domain substance
  lives, produced through the Stage-0.5 elicitation interview and gated by the ratification
  ledger. The verification layer is built for two source kinds and both grounding rules are
  implemented — span grounding for literature (`ground_literature`) and mapping-validation for
  structured records (`validate_mapping`). **Only the literature INGEST path exists today**:
  `lit2db.store` turns Europe PMC full-text XML into an offset-anchored store, and there is no
  equivalent that pulls from a structured database. Building one is real work, not a stub away.

**A corpus is defined by the query that produced it, not by its name.** A literature spec
will not freeze unless it records the **executable query verbatim** — traced to a ratified
`source_scope` ledger item, alongside the counts it returned. "Papers about X since 2020" is
an intent; term forms, field scoping, and date bounds all move the corpus boundary silently,
and a corpus whose query was never written down cannot be reproduced, audited, or refreshed.
This is the same invariant as the schema half: an agent cannot slip in an unratified field,
and a project cannot ship an undefined corpus. Which papers are in scope is researcher
substance, and it ratifies like everything else.

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
  number *appears* in its quote so **naive grounding passes** — but the readings disagree and
  the adversarial judge returns **UNSUPPORTED**, which is a veto. The gate **denies** it and
  prints both reasons.
- **C** — a value from a **retracted** source → grounds, and the judge supports it, but the gate
  **denies** on `source_status=retracted`.

Only A lands in the ML-ready view. B and C never silently enter the database. That contrast —
high surface grounding, still struck out — is the whole thesis in one run.

The judge is a **veto applied after the score**, not a term inside it (D-079). Measured over 165
records: at a 0.95 bar only a unanimous, fully-grounded record can be written, and for such a
record a supported verdict changes nothing — so 84% of judge calls could not have affected any
outcome. Scoring first and judging what survives costs a fraction of the same guarantee, and a
ratified random slice of the *rejected* records is still judged so the veto's reject-side
behaviour stays measurable.

## Calibrate before you trust the gate

The shipped `auto_accept_threshold: 0.95` is a **conservative placeholder, not a
recommendation.** It is set high so an uncalibrated project fails toward auto-accepting too
little — everything queues for human review — rather than toward letting unverified values
into the ML-ready view. Calibrate it against your own gold set; a genuinely calibrated value
can land near 0.7, where 0.95 would auto-accept almost nothing. Set it in your project's
`instantiation/<project>/instantiation.yaml` under `routing.auto_accept_threshold`, or
override it per run with `LIT2DB_AUTOACCEPT`. No domain-calibrated number is baked into the
scaffold — that is the point of the two-layer split.

The labels that calibration needs do not have to be commissioned. A researcher confirming
candidates **is** the labelled data, and 30–50 records is enough to tell a good configuration
from a bad one. Two ways to collect them, same calibration set:

```
/lit2db-review                                   # conversational, a few records per question
python3 scripts/review_ui.py --db p.db --sources ./sources   # in a browser, beside the paper
```

## Reviewing beside the paper

```
python3 scripts/review_ui.py          # with no database, builds a demo and says so
```

A two-pane page on `127.0.0.1`: the candidate on the left — the value, which paper it came from,
and why it stopped short, in the researcher's language rather than the schema's — and on the
right that paper's stored text with the quoted sentence **highlighted where the record says it
is**. No PDF and no viewer: `full.txt` is the coordinate system, so a char offset is all the
anchor a browser needs.

Two properties it holds structurally, not by convention:

- **A verdict never writes a record.** The page's only write is `record_adjudication`, which
  cannot reach the ML-ready table. A confirmation is a measurement of whether the *gate* was
  right, and spending it to bypass the gate would destroy the measurement.
- **No verdict without the evidence.** If the quote is not at its recorded offset — moved,
  absent, past the end of the text, or the paper was never stored — the page says which of those
  happened, in those words, and offers only *can't tell from this*. The server re-checks and
  returns 409, so the rule survives a stale tab as well as a disabled button.

Stdlib only, one HTML file, nothing fetched from a network at review time.

## Instantiate for your own domain

```
/lit2db-new-project my-domain
```

Runs the scope-elicitation agent through the ten axes, building a ratification ledger you
approve item by item. Only after every field traces to a ratified item does a schema freeze,
and only then does ingestion begin.

## License

**AGPL-3.0-or-later** — see [`LICENSE`](LICENSE). Chosen so lit2db stays free: the network clause
means anyone running a hosted service on top of this work has to share their changes back.

**The honest limit.** Releases up to and including `v0.53.0` were published under MIT and cannot
be recalled — anyone may continue to use those under MIT terms, including commercially. The AGPL
covers this release and everything after it.

The scaffold is domain-blind; any domain substance and redistributed source material are governed
by your own project's licensing.

## A note on judge independence

The shipped judge is a different *model* from the extractor (Opus judging Sonnet) but the **same
family**, which reduces self-preference bias without eliminating it. That is the default because it
needs no API keys, and a plugin a researcher cannot install is a plugin nobody validates. Selecting a
genuinely different provider is opt-in. If you publish results from the default configuration, say
"different model, same family" and record residual self-preference as a limitation — do not describe
it as cross-family verification.

## Getting the papers — coverage without credentials

Set a contact email and most of the literature opens up:

```
export LIT2DB_CONTACT_EMAIL=you@university.edu
```

`resolve_access` uses it to find legal open copies via Unpaywall. On one measured corpus
(bacterial terpenoids, 2020–2025, 102 papers) this lifted machine-readable coverage from
**56% → 83%** with no credentials at all. The email is a politeness requirement of those APIs —
it is **not** authentication and unlocks nothing paywalled.

**Version matters, and the gate enforces it.** Most recovered copies live in repositories as
`submittedVersion` (pre-peer-review) or `acceptedVersion` (pre-copyedit). Values move during peer
review, so lit2db stamps the version in provenance and only lets `publishedVersion` auto-accept;
everything earlier is flagged for human review. You get the recall without quietly citing a
preprint number as the published one.

**What it will not do:** reach around a paywall. No proxy cookies, no scraping article pages, no
credential replay — automated bulk download through institutional access is the fastest way to get
a whole university cut off. Sources it cannot obtain legally are pushed back to you via
`rank_manual_queue`, ordered by likely payoff using *your* ratified priority terms, each with a
`why` breakdown. Drop the PDFs you fetch into `sources/manual/` named by DOI and they ingest like
any other source — same provenance, same version stamp, same retraction check.

## Cost accounting — in tokens, not dollars

lit2db reports computational cost in **tokens**. That is deliberate. On a flat-rate
subscription the marginal cost of a run is zero until a limit is hit, so an API-equivalent
dollar figure describes neither what the researcher paid nor what they can afford — and it
cannot compare two operators on different plans. Tokens can: they are what plan limits
count, they don't move when prices do, and they make cross-condition comparisons meaningful.

`src/lit2db/accounting.py` accumulates usage per unit of work and per pipeline stage, and
projects a corpus total from a small calibration sample (always reporting the sample size,
so a projection is never mistaken for a measurement). The Stop/SessionEnd hook enforces a
**token** budget cap expressed as a ratio to your gold-set run, so it transfers across
projects. `api_equivalent_cost()` exists for callers who genuinely pay per token; it takes
its rates as an argument — no price is hardcoded, because published prices go stale and a
baked-in constant produces wrong numbers forever.

## The ensemble: k passes, and agreement decided deterministically

Extraction runs `k` independent passes (default 3) over the same source, and
`aggregate_ensemble` compares them to produce `c_ensemble` and `c_consistency`. **A value may
only auto-accept with the agreement its project ratified** — unanimity by default.

Agreement is computed, never judged. Asking a model "do these agree?" gives a different answer
on different days, and a routing bar built on that means nothing. So the comparison is
deterministic and normalized first: `4.2` == `4.20`, `"4.2 uM"` == `"4.2 µM"`, `12.4 s⁻¹` ==
`12.4 s-1` — but `2-MIB` != `2-methylisoborneol`, and a number embedded in a name is not a
measurement. Without that normalization the ensemble would mostly detect typography, filling
the review queue with correct values; **dissent has to imply substance** for the bar above it
to mean anything. Domain knowledge stays yours: synonym maps come from the ratified
controlled vocabulary, never from the scaffold.

Merging k passes is `merge_extraction_passes`, and **alignment is the hard part, not
comparison**: three passes over one paper may find five compounds, four, and six, in different
orders. Records align on the entity's ratified identity field (Stage-0.5 axis 5) — aligning by
position would compare one entity's measurement against another's and yield a confident,
well-grounded, entirely wrong record, so a type with several records per pass and no ratified
identity field is refused rather than guessed at.

A record a pass did not find becomes a *missing value* for each of its fields, so record-level
and field-level disagreement flow through one mechanism and cannot drift apart. Something only
1 of 3 passes saw scores 1/3 and cannot auto-accept — but it is still emitted. A compound the
other passes missed is the most interesting thing an ensemble produces, not something to drop.

The bar is a ratified setting, stated as integers because the signal is quantized to j/k:

```yaml
routing:
  ensemble_k: 3                 # independent extraction passes (>= 2)
  ensemble_min_agreeing: null   # null -> unanimity, tracking k; or pin an integer 1..k
```

`null` means unanimity and *follows* k, so raising `ensemble_k` to 5 tightens the bar to 5-of-5
rather than silently loosening it to 3-of-5. `k=1` is refused: one pass trivially agrees with
itself, which would convert this gate from a block into a pass. To run without an ensemble,
leave `c_ensemble` unset — an absent signal routes to human review, failing closed.

Note `k` multiplies the largest stage of a run: k passes means k times the extraction tokens.

## Counter-evidence: auditing what the extractor chose to show you

Grounding asks *"is this value in the paper?"* — a question the extractor got to pick the
evidence for. Every downstream signal (lexical grounding, the adversarial judge, the
confidence composite) then scores **that chosen span**. None of them can see what was left
out, so cherry-picking is invisible to all of them.

The `contradiction-hunter` agent asks the question nobody else does: read the rest of the
source and find what argues *against* the value — a conflicting number, conditions the
schema excludes, a later passage that supersedes it, a claim asserted then withdrawn.

**A contradiction blocks the write; it is not a confidence penalty.** Folding it into the
weighted mean would let four confident signals bury one real refutation. It behaves like
`source_status=retracted`: some facts disqualify a value outright, however well it scored.

Finding nothing is the expected outcome and is recorded as a distinct state — `clean` is not
the same as `not_run`, because "we did not look" must never be reportable as rigor. Set
`routing.require_contradiction_search: true` to block unaudited values as well; that flag is
also the control/treatment switch if you want to measure how often counter-evidence changes
an outcome.
