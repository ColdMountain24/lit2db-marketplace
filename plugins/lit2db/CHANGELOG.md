# Changelog

## 0.31.0 — 2026-07-27
A stage that never ran may not be recorded as a stage that found nothing.

Four defects that v0.30.0's deadline introduced, fixed in severity order. The first is the one
that matters: **the pipeline committed the exact error class it exists to catch, in its own
bookkeeping, silently.**

- **(a) `scored.json` is never written when a verification stage was SKIPPED.** Measured on
  `PMC10301674`: the paper deadline expired, all 77 judge calls and the hunter returned
  `paper deadline reached before attempt 1` **without being invoked**, and the driver read each
  missing verdict as an absence — scored the paper, wrote `scored.json`, and marked it `done`.
  77 records were recorded as verified-and-denied when the verification had never happened, and
  the paper became unresumable. A paper missing a stage now returns `incomplete`, writes no
  `scored.json`, and is picked up by the next leg — resuming at the *stage*, since its finished
  extraction passes are still on disk.
  - **The blast radius was contained by the design, not by luck:** the hunter's `not_run` fails
    closed, so the gate denied all 77 and **nothing unverified reached the database**. The defect
    was in what the run *claimed*, which is why it could go unnoticed.
  - **The line is drawn at "did the call execute", not "was the reply good".** A reply that
    cannot be parsed IS a result: it fails closed, the raw text is on disk, it is catalogued as a
    question, and it is scored. Retrying it would loop forever on a paper whose replies are
    reproducibly unparseable, and a paper that silently never finishes is worse than a deny a
    human can audit.
- **(b) A measured `tokens` block is never overwritten by an emptier one.** `_recover_done`
  restores a resumed paper's counts but explicitly cannot restore its tokens — yet the manifest
  on disk still *held* them, and every resumed leg overwrote that block with its own. A wave
  resumed for one last paper published the cost of one paper in the field a reader takes for the
  cost of the wave. Earlier legs are preserved in `tokens_prior_legs`, kept beside and
  deliberately **not summed** (a later leg may reuse work an earlier one paid for).
- **(c) A timeout is not retried.** This is the real fix for the hang v0.30.0 responded to. The
  cause was a prompt sending the agent to grep a document that fits in context several times
  over — a *deterministic* hang, so the second attempt buys an identical wait at full price.
  Retries still cover a transient non-zero exit and a usage limit, where trying again is a
  different event.
- **(d) The paper deadline is removed.** A wall-clock stop was the wrong instrument for an
  unbounded retry count, and it caused (a): when it expired, calls were skipped rather than
  cut short. Not retrying a timeout bounds the paper through the term that was actually
  unbounded. Per-call timeouts still scale with document size (`420s + 12s/kB`, capped 1800s).
- Manifests now name `papers_unverified_left_for_retry`.
- 399 → 407 tests.

## 0.30.0 — 2026-07-27
A paper gets a deadline, and the prompts stop telling agents to grep a document that fits.

- **The cause.** All three prompts told the agent to "use Grep to find candidate passages rather
  than re-reading the whole file." Measured across the 921-store corpus: the **largest paper is
  ~37k tokens against a 200k context** — every paper fits several times over. So the advice
  optimised a constraint that does not exist, and it costs both time and tokens, because *turns*
  are the driver: each search is a turn, and each turn re-reads everything accumulated. One
  Sonnet extraction pass hung for 1800s on a 93kB paper. Prompts now say read it once, in full,
  and say why.
- **The symptom.** A flat 1800s per-call timeout applied to a 1.7kB store and a 149kB store
  alike, and with `retries=3` one stuck pass could burn **90 minutes** — silently, because a
  driver waiting is indistinguishable from a driver working. Now: per-call timeout scales with
  document size (`420s + 12s/kB`, capped at 1800s), and the **paper** carries a hard deadline
  (`paper_timeout`, default 2400s) that retries may not cross and that clips any single call.
- Bounding each attempt while leaving the paper unbounded was the wrong shape; the paper is the
  unit a wave schedules, so the paper is the unit that gets the budget.
- 395 → 399 tests.

## 0.29.0 — 2026-07-27
Remove the superseded corpus runner and the cost model that kept being wrong (D-074).

- **Deleted:** `scripts/run_corpus.py`, `tests/test_run_corpus.py`,
  `src/lit2db/yield_projection.py` and its test. `run_wave.py` supersedes the runner and
  `replay.py` covers what it was still being used for.
- **Why it was urgent, not cosmetic.** `project_cost` had been wrong five times by the journal's
  own count, was left in the wrong unit entirely by D-070 — it projects input tokens while every
  measurement now reports first-time content — and stayed importable with tests pinning it.
  Three separate sessions picked it up and computed a wrong budget. A stale formula that still
  runs is not documentation; it is a trap.
- **Kept, after checking rather than assuming:** `screening.py` (T22, used by the corpus build),
  `dedup.py` (T16, caught two correction notices in the 921), `entity.py` (wired into the MCP
  server, three commands and two agents), and `stages/` `adapters/` `tools/` (the intentional
  contract scaffolding M1 builds on). "Not called by the runner" and "dead" are different things.
- 425 → 395 tests, entirely from deleting the tests of deleted code.

## 0.28.0 — 2026-07-27
`scripts/replay.py` — re-run the spine over saved extraction output. Zero model calls.

- **Why.** Every defect found in this project's first week of real runs was a SPINE defect, not
  an extraction defect. Each was found by spending ~20 minutes and millions of tokens
  re-extracting papers that had already been extracted — using the most expensive part of the
  pipeline as a debugger for the cheapest. 320 records across 39 saved passes were already on
  disk, and they are a regression corpus.
- Replays merge → assemble → score → gate through the REAL code path (`run_wave.assemble` is
  imported, not reimplemented, so a parallel copy cannot drift and validate nothing). Gates
  against a throwaway database: these records were gated once already and re-writing them would
  double-count a yield.
- **11 saved paper-runs replay in under a second.**
- **It paid for itself on first run.** A model returned `verbatim_quote` as a LIST — one quote
  per element of a multi-valued field, mirroring the value's shape — and `find_spans` raised,
  killing the whole paper. Under paper isolation (v0.25.0) that is worse than a crash: the
  paper is recorded as failed and silently lost from the wave. Each element now anchors on its
  own. Fixing it recovered a paper and 15 records from artifacts already on disk.
- **What replay CANNOT tell you**, stated in the module docstring so it is not over-trusted:
  whether the extractor obeys a CHANGED prompt (saved passes were produced under the prompt of
  their day), and what a run COSTS (there are no calls). Those need fresh runs. Nothing else does.
- A low "would write" from pre-v0.24.0 artifacts is reported as an artifact of missing judge
  verdicts, not as a gate regression — the runs that produced them did not persist verdicts.
- 419 → 425 tests.

## 0.27.0 — 2026-07-27
A record the ratified criteria exclude is ROUTED to review, not silently denied (D-067).

- **No new gate mechanism was needed.** `gate_reasons` has always checked `record["route"]`
  against the blocking routes, so a routed record is denied at composite 1.000 with its reason
  attached. The decision record claimed this mechanism was missing; it was not, and the claim
  came from reading the per-field lane without checking the record-level path beside it.
- **What was actually missing:** the flag did not survive `merge_passes`, which rebuilds records
  from aligned fields and drops record-level keys — so a flag set by the extractor vanished
  before the gate could ever see it.
- **`review_only` carries on ANY pass's vote, not a majority.** Field values need agreement
  because the ensemble decides what is TRUE; this flag decides only whether a human looks, so
  the errors are asymmetric. Unanimity would let a record through because two passes missed what
  the third caught — the failure the ensemble exists to prevent — while a false flag costs one
  glance at a queue. Every flagging pass's reason is retained.
- The routed record keeps all its fields and full provenance: the reviewer sees what was
  extracted and why it is in front of them. Routing excuses nothing else — a retracted source
  still denies, as its own separate reason.
- 411 → 419 tests.

## 0.26.0 — 2026-07-27
The last resort may not have a prerequisite, and the cost headline counts the document.

- **Identity (D-069).** The chain's ordinal tier was scoped *inside* `genus_species`, so a record
  with no accession, no name-pair and no organism had no identity at all and `merge_passes`
  refused the whole paper. **2 of 3 fresh corpus papers died this way** — both chassis studies
  putting 8 and 23 synthases through one host, the richest papers in the corpus. Ordinal now
  falls back to order within the SOURCE, as a distinct `ordinal_unscoped` tier so a disagreement
  under it reads as a possible mis-pairing rather than as evidence.
- **`ordinal_within` absent stays OFF.** Declaring it is how a researcher ratifies that
  positional alignment is acceptable for an entity type; its absence is a decision, not an
  omission. The first cut of this fix applied ordinal to every spec with a chain, silently
  enabling alignment nobody ratified — caught by an existing test, now pinned by its own.
- **Cost headline (D-070, amends D-065)** is `input + cache_write + output`: first-time content,
  however it arrived. With caching on a source document is never billed as `input` — it arrives
  as `cache_write` — so the previous headline structurally excluded the paper being read.
  Measured: input **138**, cache_write **351,862**; the old definition reported 155,270 where
  the model had processed ~507,132. `cache_read` is re-reads, reported beside it via
  `reread_tokens()`, never folded in.
- 406 → 411 tests.

## 0.25.0 — 2026-07-27
One paper may not kill a wave.

- `PMC10046388` raised out of `merge_passes` and the traceback ended the run on paper 1 of 2,
  after paying for three extraction passes. In a 137-paper wave left overnight that is the
  whole wave lost to one unusual paper — and unlike a fuse trip it leaves nothing resumable,
  because the paper never reaches `scored.json`.
- A failing paper is now recorded (`status: error`), catalogued as a `paper_failed` question,
  and the wave continues. It does NOT count toward `n_done`.
- `FuseExceeded` is deliberately still fatal: it is the safety device, and swallowing it
  per-paper would turn a runaway-loop brake into a hiccup — worse than no fuse, because it
  would look like one.
- 403 → 406 tests.

## 0.24.0 — 2026-07-27
The adversarial judge becomes auditable, and the question queue keeps its signal.

- **The judge's reasoning is persisted.** Previously a regex scraped `"verdict"` out of free
  text and everything else — `reasoning`, `weakest_supported_claim`, `overreach` — was thrown
  away. No denial anywhere could be audited, in a pipeline whose entire claim is auditability.
  Every raw response now lands in `judge/`, parsed structurally with regex only as a last
  resort. A regression found while testing: a single-claim reply carries no `record_id`, so
  every unbatched judgement had been falling through to the regex path and losing its reasoning.
- **A missing verdict is a FAILURE, not an absence.** 7 of 45 records in the v4 slice got no
  parseable verdict and the driver read that as "nothing to say" — a record silently skipping
  the adversarial check. Now logged, and catalogued as a `no_verdict` question.
- **A guess is labelled a guess.** A batched reply with no ids can only be paired by order;
  that pairing is marked `by_position`, and a count mismatch refuses rather than mis-pairs. A
  mis-attributed verdict is worse than a missing one — it judges a record nobody judged.
- **`judge_batch_size`** — several records per call, the largest reducible cost term (15 calls
  per paper, each re-paying a ~28,400-token harness prefix four times larger than the paper).
  The batched prompt instructs independent judgement per claim; default stays 1.
- **`paper_concurrency`** — papers ran strictly sequentially (~39 hours for 137). Buys
  wall-clock, not tokens. Default stays 1, because every paper in flight multiplies the peak
  rate at which a run hits a usage limit, and surviving one unattended is the driver's point.
- **The gate write is serialized.** With papers concurrent, SQLite writers collide and a
  "database is locked" surfaces as a gate DENIAL — a paper losing records to a plumbing fault
  while every artifact says the pipeline worked.
- **A review-lane field no longer floods the queue.** `function` is prose ratified as
  never-auto-acceptable; it disagrees on every record by construction, and was 31 of 75
  questions, burying the 12 scope disagreements that genuinely needed a human. A queue that
  always fires trains the reader to ignore it.
- 391 → 403 tests.

## 0.23.0 — 2026-07-27
The wave driver reports what it spent, and a resumed wave still describes the wave (D-065, D-066).

- **The four token streams stay apart.** `Fuse.tokens_total` sums input + output + cache_read +
  cache_write; `run_wave.py` attached no `RunAccount`, so the split was computed, used for the
  sum, and discarded — while the comment above the call claimed it was preserved. Measured on
  one instrumented extraction pass: input 50, output 24,236, cache_read 237,749, cache_write
  47,959. **92% of the collapsed total is cache traffic**, and it was being compared against
  projections built in input tokens.
- **The headline is `work` = input + output** — the only figure comparable to a projection.
  Cache streams are reported beside it, never folded in. `total_all_streams` is retained and
  named for what it is, because that is what the fuse trips on. `RunAccount` gains public
  `by_stage()`, `by_unit()` and `work_tokens()`; the manifest carries a `tokens` block with the
  per-stage split, so a run that does not fit says WHICH stage spent it.
- **A resumed paper is still a paper in this wave.** `todo` skips work already on disk, but the
  manifest was built only from the current leg, so finished papers vanished from `n_done`,
  `n_records`, `n_written` and `per_paper`. Observed: two papers complete on disk, manifest
  reported `n_done: 1`. Carried forward from `scored.json` and flagged `carried_from_disk` —
  tokens are NOT recovered, because they were spent in a process whose account died with it.
- **Resume at the stage, not the paper.** A paper that died during judging re-ran its three
  finished extractions. That is not merely wasteful: it replaces the ensemble the surviving
  artifacts came from, so the resumed paper is no longer the paper that was partly judged.
- Found by running the calibration slice, not by reading the code. At the measured rate, wave
  1's 120M ceiling would have halted the run at **roughly paper 16 of 137**.
- Judge batching — the largest reducible term — is deliberately NOT in this release: changing it
  mid-slice would have made paper 1 incomparable to papers 2 and 3.
- 380 → 391 tests.

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
