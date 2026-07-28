# Changelog

## Unreleased
Candidates can be confirmed beside the paragraph they came from.

`/lit2db-review` collects the labels this project spent weeks calling a blocker, and it collects
them through `AskUserQuestion` — which can show a reviewer the quote and **nothing around it**.
That is the wrong constraint to put on the answer. A sentence with no paragraph around it is
exactly where a careful reader says "can't tell" for want of CONTEXT rather than for want of
ACCESS, and only one of those two is a fact about the extractor. Every such answer is a row the
calibration set does not get, blamed on the literature instead of on the interface.

`scripts/review_ui.py` serves a two-pane page on `127.0.0.1`: the candidate on the left, its
paper's stored text on the right, the quoted sentence highlighted at its recorded offset. No PDF
and no viewer — `full.txt` is the coordinate system, so a char offset is the whole anchor.

- **The join it needed did not exist.** `review_queue` selects seven columns and `payload_json`
  is not one of them, so the quotes and offsets it took a whole pipeline to produce were
  unreachable through any tool. New `lit2db.review` reads the candidate payload and the store
  together, which is the substance of this change; the browser is the thin part.
- **An offset is a CLAIM, and `anchor()` verifies it rather than assuming it.** Slicing at a
  stale offset returns text — just not the cited text — so the failure is silent and a verdict
  given against the wrong paragraph is indistinguishable from a good one afterwards. Five
  outcomes, each with the sentence a researcher can act on: at its offset · elsewhere in the
  paper · not in the paper · past the end of the text · never stored. **No fuzzy matching**,
  because that would convert the four failing states into the passing one for exactly the
  records most worth being suspicious of.
- **Only the first state permits a verdict.** Anything else offers *can't tell from this* alone,
  and the SERVER re-derives that and returns 409 — a disabled button is a suggestion to whoever
  is at the keyboard, and the rule has to hold for a stale tab too.
- **A verdict still never writes a record**, now across a second surface. A test asserts the new
  files contain no reference to `gate_upsert`/`db_upsert`; a new surface is a new way around a
  rule whose argument is not obvious enough to survive on style.
- **Found in the browser, not by the suite: holding `j` filed a verdict against the wrong
  paper.** Overlapping record fetches had no supersession token, so the last response to resolve
  won the render while a different record stayed in `VIEW` — two keypresses were enough, and it
  looked completely correct on screen. The server could not catch it: it validates the record
  *named in the request*, and that record was itself perfectly adjudicable. Fixed with a request
  token plus a refuse-on-disagreement check, and pinned by a test. Eleven tests had already
  passed over this surface; the defect needed a browser and a keyboard.

- **Also found by driving it, on real pilot data rather than the demo: one record's five fields
  quoted characters 589 through 13,396 of the same 16,255-character paper.** The page opened at
  the first highlight, so a reviewer was being asked to confirm five values while looking at
  one — the show-the-quote rule failing *quietly*, since a quote genuinely was on screen and
  nothing said there were three more. Each value now links to its own sentence and the count is
  stated. The synthetic demo could not have shown this: its records carry one field each.

- **A verdict now records WHICH SURFACE it was given on, because the two do not ask the same
  question.** Both write the same table with the same three verdicts, so the calibration set is
  identical in kind — but the browser mechanically refuses `right`/`wrong` when a record's quote
  could not be shown, while `/lit2db-review` states that rule in prose for an agent to honour.
  Two paths that can produce differently-distributed labels from one corpus, and nothing recorded
  which path a row came from, so the difference was unmeasurable rather than absent. The tag
  rides on `adjudicator` rather than a new column, so **every verdict already collected survives
  and reads as `unknown`** — checked against the 24 real ones, whose pooled counts are unchanged.
  `adjudications()` and `calibration_report` now break the counts down by surface; a large gap in
  the `cant_tell` share between columns means part of what was measured is how the question was
  put. Tagging happens at each path's own boundary — the MCP tool tags `chat` because it *is* the
  conversational surface — so it cannot be forgotten by a caller.

### What commit `77d587f` actually contains

Most of the above shipped inside **`77d587f` — "v0.48.0 — organism resolves to NCBI Taxonomy,
additively (D-106)"**, whose message describes only the other half of it. This work was written to
be staged and not committed; a concurrent session working in the same tree bumped both manifests to
0.48.0 and committed, and its commit swept in whatever was staged. So one commit carries two
unrelated changes:

- organism authority resolution — `src/lit2db/taxonomy.py`, `tests/test_taxonomy.py`, `run_wave.py`
- the browser reviewer — `scripts/review_ui.{py,html}`, `src/lit2db/review.py`,
  `tests/test_review_ui.py`, `commands/lit2db-review-ui.md`, and the `store_dirname` extraction

**The history is deliberately not rewritten.** `77d587f` was already pushed to the public
marketplace repo that `/plugin install` reads, so splitting it would mean force-pushing over
history other people may hold — a real risk taken to fix a cosmetic one. This note is the fix
instead: the commit message stays wrong and the record here is right.

Two things this leaves open, neither introduced by it: the log still has no `0.46.0`, `0.47.0` or
`0.48.0` heading while both manifests read 0.48.0; and `pyproject.toml` still reads `0.5.0`.

## 0.45.0 — 2026-07-28
k=1 no longer awards itself a free agreement score.

v0.42.0 made k=1 runnable by passing `ensemble_k=0`, which stopped `required_agreement` raising.
It did not stop the thing the exception was warning about: `merge_passes` computes agreement over
whatever passes it is given, so a single pass agrees with ITSELF and still emitted
**`c_ensemble = 1.0`** — a full mark on the signal the accept bar leans on hardest, awarded for
nothing.

- `assemble` now drops `c_ensemble` entirely when there are fewer than two passes, so a field is
  routed on the signals actually measured. That is what the contract's "leave c_ensemble unset"
  always meant.
- The guard and the value are **different objects**: `required_agreement` refuses k<2 on the
  BAR, and this is the SIGNAL. Silencing the first without removing the second left the flattery
  in place while looking fixed — and it was found only by reading a written record's components,
  not by any test.

## 0.44.0 — 2026-07-28
`/lit2db-start` hands off to the full interview instead of running on four answers.

The friendly intake and the rigorous ratification are now one flow: `/lit2db-start` collects four
questions in the researcher's language, then invokes `/lit2db-new-project` with everything
carried forward, so Stage-0.5 is a **confirmation pass rather than a second questionnaire**. A
researcher asked the same thing twice in different words concludes the tool was not listening,
and starts clicking through — which is the failure mode this whole design is trying to avoid.

- **Two kinds of item cross the handoff and they are not interchangeable.** What the researcher
  STATED arrives with their own phrasing and is confirmed ("you said X — is that right?"). What
  the agent DERIVED — query, identity chain, negative-data policy, evidence tiers, provenance
  granularity — gets the full propose-and-ratify treatment and is labelled as the agent's
  proposal when shown.
- **Never present a derived item as already agreed.** That is exactly how two fields once froze
  into the BBB schema citing ledger items that did not exist, under a note claiming they were
  researcher-ratified. The scaffold was correct both times; the work was routed around it.
- **No axis is skipped because the intake "probably covers it."** Speed comes from confirming a
  concrete proposal, never from asking less.

## 0.43.0 — 2026-07-28
`/lit2db-start` — a front door for a researcher who knows their field and nothing about this tool.

`/lit2db-new-project` runs the ten-axis Stage-0.5 interview, which asks a chemist about
"provenance granularity" and "evidence-tier dimensions". That is the right depth for freezing a
spec and the wrong first contact for someone who just wants their literature in a table.

- **Four batched questions, in the researcher's language**: what is one row, what do you want to
  know about each, which literature counts, what would make you throw a paper out. Everything
  else — the query, identity chain, negative-data policy, provenance granularity — is STRUCTURE,
  derived and stated in a plain sentence for correction. A researcher is never asked to write a
  query string; they are shown what it returned and asked whether that looks like their field.
- **The corpus is measured and shown before anything is spent**, and `preflight` runs before the
  first model call (D-095) so a configuration problem is a question, not a dead run.
- **The honest floor, stated worst-case first** (D-096): a screened paper list, then a candidate
  pool where every value already carries its quote and location, then the ML-ready table — which
  will be small at first, and that is said out loud rather than apologised for. `/lit2db-status`
  now reports the corpus alongside both record tiers for the same reason: a report opening with
  "5 records" reads as failure, and one opening with the corpus reads as what happened.

## 0.42.0 — 2026-07-28
A configuration refusal costs a question, not a night.

The architectural rule ratified as D-095: **a refusal that protects the DATABASE stays hard and
silent; a refusal that rejects the OPERATOR'S CONFIGURATION floats and waits.** Of the codebase's
20 hard-failure sites, 12 guard what gets written and 2 guard silent emptiness — those are the
mechanism that made quality trustworthy and they do not move. The other 6 are configuration, and
**both of the validation arm's losses came from that group**: the fuse ceiling cost a whole
overnight run, and `ensemble_k must be >= 2` killed six papers one at a time, each after paying
for its own extraction.

- **`preflight()` runs every contract check the wave needs before the first model call.**
  Ensemble arity, weights profile, prompt files and their placeholders, a store on disk for every
  paper, a non-empty identity chain. It reports **all** problems at once — an operator fixing a
  config wants the list, not one per attempt — and exits without spending anything.
- **`k=1` is now a supported configuration.** The driver passes `ensemble_k=0` rather than 1,
  which is the contract's own documented way to run without the signal: `c_ensemble` is left
  UNSET and an absent signal routes to human review. It does not get set to a free 1.0, which
  would have asserted agreement nobody measured — and would have flattered a k=1 experiment in
  exactly the direction the experiment was testing.
- **Found while building it**: `score_and_route` silently falls back to the `numeric` profile on
  an unknown `weights_key`, so a typo in a ratified profile name would score an entire wave under
  weights nobody chose and say nothing. Preflight now refuses it.

## 0.41.0 — 2026-07-28
Structures get resolved by the pipeline that actually runs — and the resolver could not reach PubChem.

D-084 ratified resolve-never-generate, v0.35.0 built it, and `resolve_structure` was reachable
only from the interactive `extractor-agent`. **The headless driver never called it**, so `smiles`,
`inchikey`, `molecular_formula` and `authority_compound_id` — 4 of the compound schema's 10 fields
— were absent from all 133 records of the first arm run.

- Wired into `run_wave.py` **after scoring, before the gate**. It is a deterministic PubChem
  lookup, not a model call, so nothing about it needed an agent. After scoring because D-083
  ruled an unresolved name costs the record nothing: a lookup must not change whether a record is
  accepted. Per-paper cache, `structures.json` audit trail, never raises.
- **Wiring it exposed a total, silent failure in the resolver itself.** Python builds from
  python.org carry no system trust store, so every HTTPS call failed certificate verification —
  and the fetcher's fail-closed `None` reported that as "authority unreachable or no match".
  Every compound, every time, indistinguishable from PubChem simply not knowing the name.
- **The two are now held apart** (D-094): a transport failure returns `unreachable=True` and says
  "not asked, not answered"; a real 404 says "no match in authority". They are opposite findings
  — one is a run to retry, the other is evidence a compound is genuinely new — and a whole wave
  of the first would otherwise read as the second, which for a novel-compound database is a
  plausible-looking and completely wrong conclusion.
- 605 → 608 tests.

## 0.40.0 — 2026-07-28
The fuse counts what the cost report counts.

The validation arm stopped at 55 of 288 papers because the fuse tripped at 24.4M tokens. It was
summing **every stream, `cache_read` included** — and that was **82% of the total**. The actual
first-time content was 4.4M. A brake denominated differently from the cost headline is one nobody
can size: it stopped a healthy run and read as a five-fold overrun.

- `max_tokens_total` now counts `input + cache_write + output` — the same figure D-070 made the
  cost headline (D-093). Re-reads are counted and reported as `tokens_all_streams`, never enforced.
- **The safety property survives**: a runaway loop generates `output` on every iteration, so it
  still trips, and `max_calls` remains the primary loop brake. Both pinned by test.
- A regression test replays the arm's measured per-call shape and asserts the old rule would have
  tripped where the new one does not.
- 599 → 605 tests.

## 0.39.0 — 2026-07-28
The record-level review lane had never once worked.

D-067's lane shipped in **v0.27.0** and was exercised for the first time tonight, on the
validation-arm run, when an extractor marked a record `review_only`. The pipeline joined its
reasons into `failure_reason` — a five-value enum — so pydantic raised on **every** review-lane
record, and the exception took down the whole paper along with its four other records.

- `ExtractedRecord.review_reasons: list[str]` carries the words a reviewer needs;
  `failure_reason` stays the enum it always was. The fix is a new carrier, not a loosened
  contract — free text still cannot enter the enum, and a test pins that.
- **The lane keeps its teeth.** `route="human_review"` is what actually blocks
  (`gate.BLOCKING_ROUTES`); the reasons were never doing that work, which is why the bug was
  invisible — the lane looked implemented because the blocking half was.
- It had never fired because no earlier extractor prompt led a model to set `review_only`. A
  declared mechanism whose FIRST real use is a crash is the same shape as the twelve instances
  the v0.33.0 audit catalogued, found the only way this class ever gets found: by running it.
- 594 → 599 tests.

## 0.38.0 — 2026-07-28
A record id is qualified by its source, and replay can finally see the class that needed it.

**The collision stopped being hypothetical.** On the compound pilot's fourth paper,
sulfadixiamycins A and B — composite **1.000**, supported by the adversarial judge — were refused
because `cpd1`/`cpd2` were already held by corvol ethers A and B **from a different paper**.
Extractors number records per paper, so `cpd1` means "the first compound in some paper" and two
papers collide by construction. Under a bare `INSERT OR REPLACE` corvol ether A would have been
silently overwritten: no error, no reason string, nothing in any artifact to show a row had gone.

- **Stored ids are now `<source_id>:<record_id>`** (D-091). The local id stays in the payload and
  is returned as `local_record_id`, because "the first compound in this paper" is a true fact
  about the source. Re-gating the pilot's saved records: **3 written → 5**, which is every
  compound that cleared score and judge.
- **The collision refusal survives**, aimed at what it can still catch: two DIFFERENT records
  under one id from ONE source, which `merge_passes` really does produce (15 records under 11
  ids on PMC10325987). Qualification cannot help there, so the denial is still the answer — and
  its message no longer tells the reader to qualify an id that is already qualified.
- **A record whose fields cite two sources is refused.** A record belongs to one source, so one
  that does not cannot be identified by one.
- **`replay.py` now gates the whole run against ONE database instead of one per paper.** This is
  the more important half. Replay had been run repeatedly over these very artifacts without
  seeing the collision, because each paper got a clean database — an instrument that resets the
  state between two events cannot observe an interaction between them. It is the reason a
  known, documented hazard reached a live run before firing.
- 587 → 594 tests.

## 0.37.0 — 2026-07-28
A compound named as one of a series is named by the sentence that names the series.

From the compound pilot, which wrote **0 records out of 13** while extracting correctly. Five of
the six right answers died on one rule: the paper writes *"we propose the trivial names corvol
ether**s** A and B"* and the record says `corvol ether A`, so the singular never appears and the
lexical check scored **0.0**. `compound_name` grounded at **62%** while every other field
grounded at **100%** — and it is the identity anchor and the string the structure resolver
consumes, so the one field the schema cannot work without was the one the check could not verify.

- `_ground_series` — a value shaped `STEM DESIGNATOR` grounds when the quote contains that stem,
  optionally pluralised, followed by an enumeration including the designator: a list
  (`A and B`), a range (`A-C`, which names B without printing it), or a mix. New modes
  `series_match` / `series_range_match`, so which rule fired is auditable on the record.
- **Domain-BLIND by construction.** The rule is structural — `mutant 3` grounds against
  `mutants 1-5` by the same code. Nothing in it knows what a compound is.
- **Conservative on purpose, because it LOOSENS a check the database depends on**: the stem must
  match on a word boundary and be non-trivial, the enumeration is read only from text
  immediately following the stem, and a range expands only if genuinely ascending. `C-A` is not
  a range; `sulfadixiamycin D` is still absent from `sulfadixiamycins A-C`.
- **A second, older defect found by a test written for the first.** `_ground_scalar` took the
  numeric path for any value merely CONTAINING a digit, so `hapalindole 7` grounded against
  "isolated after 7 days of culture" — a false positive in the write path, live long before this
  release. A measurement LEADS with its number; a name that contains one does not.
  `12.4 s-1` still grounds numerically, `compound 3` now grounds as a string.
- Replayed over the pilot with **no model calls**: the five records stuck at composite 0.30 now
  clear the score bar, and one writes outright. The rest are held by judge verdicts the original
  run never produced, because they were never selected in it — replay cannot invent a verdict.
- 564 → 587 tests.

## 0.36.0 — 2026-07-28
An abstract is a document you read all of, not a paper you read part of.

C9/D-087 ratified extracting from the abstract where full text cannot be had, and nothing
implemented it — `build_from_jats` was the only builder, and abstract-only sources never arrive
as JATS. The census behind D-088 is why it matters: of the 435 usable DOIs in the collaborator's
reference database, **82 give full text and 206 more give only an abstract**, so this is what
makes the comparison arm 287 papers instead of 82.

- `build_from_abstract` + the `build_abstract_store` MCP tool. Same coordinate contract as the
  JATS builder — `full.txt` is the authority, offsets round-trip — so grounding, span location
  and quoting are unchanged downstream.
- **The scope is declared by the STORE, not judged by the extractor.** Both builders now stamp
  `meta["source_text_scope"]`, and a record's own field is read from it. A caller cannot
  override it with a wrong value.
- **Kept apart from truncation, which is the D-038 distinction.** Retention asks "did you read
  all of what you had"; `source_text_scope` asks "what did you have". An abstract read whole is
  retained_fraction 1.0 AND abstract_only, and both are true — whereas the pilot that silently
  read 26% of each paper was verifying values against a different document than they cited.
- Title text is included but sits outside the Abstract section, so a claim grounded in the title
  is not reported as coming from the abstract. Title-only records are allowed — thin, but not
  empty — while a genuinely empty source raises rather than emitting a store nothing downstream
  could distinguish from a paper that contained nothing.
- **Both self-audit guards fired during this change and were right.** `test_declarations`
  refused the new MCP tool while no command or agent referenced it; `test_selfcheck` refused the
  20th tool while INSTALL.md and `/lit2db-status` still said 19. Neither was found by hand.
- 549 → 564 tests. 20 MCP tools.

## 0.35.0 — 2026-07-28
Structures are resolved, never generated.

Built for the compound-side database (D-084). The extractor pulls a compound NAME — in the text,
quotable, grounded like any other string — and `resolve_structure` asks a public authority what
that name is. **A model that never writes a SMILES cannot hallucinate one**, so the hazard is
removed rather than checked for afterwards. It is also, independently, how the collaborator built
his own 1,062-compound database.

- `lit2db.structures` — `resolve_structure` (PubChem, network injected, fails closed) and
  `structure_fields`, which attaches the result as `StructuredProvenance`. The compound name
  carries the literature provenance; the structure carries the lookup. Two auditable links.
- **Ambiguity resolves to nothing.** A name matching several compounds returns no structure and
  reports the candidates: names lose stereochemistry, and for terpenoids that is exactly the
  difference that matters.
- **No formula fallback.** Measured on the collaborator's data: 642 distinct formulas over 1,058
  compounds, and 44 share C15H24. A formula agreement is close to no evidence in this domain.
- **An unresolved name costs the record nothing** (D-083) — attempting a resolution must never be
  worse than skipping it, or the extractor is incentivised never to try. A name matching nothing
  is very likely a NOVEL compound, which is a finding.
- **PubChem only.** The Natural Products Atlas is the better domain source but no client for it
  has ever been run against the live API, and shipping a resolver for an endpoint shape nobody
  verified is the defect `test_declarations.py` exists to prevent. Added after one live session.
- The extractor agent now carries the rule explicitly: never emit a SMILES, and never read a
  structure off a figure.

**Two defects found while wiring it, both by the audit:** the agent frontmatter used
`mcp__lit2db__*`, which is not the namespace this plugin's tools actually carry — an unknown tool
name is silently dropped, which is the documented hazard. And `test_agent_contracts` compared
namespaced declarations against bare prose names, so it had been passing on a declaration it
should have caught; it now normalises with the same rule the PreToolUse hook uses.

540 → 549 tests. 19 MCP tools.

## 0.34.0 — 2026-07-27
Two tiers, and fields are optional unless you lock them.

The product is a large candidate database plus a smaller high-quality one. The goal is to make
the second as large as possible without demanding perfection: **the pipeline accelerates a
researcher's work, it does not replace it.**

- **Every record the pipeline produces is now kept**, in a `candidates` table carrying its score,
  route, gate decision and reasons. A record that missed the bar still has its quote, character
  offset, grounding score, cross-pass agreement and judge verdict attached — most of the work of
  confirming it. A run reporting "5 of 45 written" did not fail 40 times; it produced 5 finished
  rows and 40 a human can adjudicate in seconds each, and discarding those was throwing away the
  bulk of the acceleration.
- **Two TABLES, not one with a status column.** `records` is unchanged and as gated as ever —
  "everything in it cleared the gate" stays literally true, and the PreToolUse hook still
  protects it. Separation rather than a flag, on this project's own evidence: a shipped BBB
  database was found holding 18 rejected-but-present records, which is what a status column
  everyone must remember to filter buys you. `record_candidate` is deliberately not a
  `WRITE_TOOL`, and `db_query` reads only `records`.
- **`FieldSpec.required` defaults to False.** It defaulted to True, was read by nothing, and was
  inherited silently by six of eleven terpenoid fields — required because nobody wrote a value
  down, which is the opposite of ratification. It is now enforced, and only for fields a
  researcher explicitly locked (`gate_reasons(required_fields=...)`, empty by default).
- **`/lit2db-status` reports both tiers** and is told never to report the accepted one alone.
- **The declaration audit generalizes.** A new check catches any contract field that *implies
  behaviour and gets none* — the category `FieldSpec.required` belonged to. Fields that are
  merely content are exempted by model with a stated reason, and the structured-adapter fields
  are exempted as an admitted gap, cross-checked against the README so the two cannot drift.

532 → 540 tests. ML-ready gate decisions verified unchanged on 257 real records.

## 0.33.0 — 2026-07-27
The library was a specification and a script was the system. Now there is one artifact.

A root-cause review of the project's first-week defect stream found **one bug class, twelve
instances**: a declaration not backed by the thing it names. An agent declaring tools it did not
hold. Weights for three signals nothing produced. A corpus that was a name with no query. Schema
fields marked researcher-ratified against ledger items that did not exist. A stage recorded as
"found nothing" that had never run. An audit slice reporting three records having judged two.

The structural cause: `src/lit2db/stages/` declared itself "the domain-INVARIANT control flow"
with **eight of nine function bodies empty and nothing importing the package**, while the control
flow really lived in `scripts/run_wave.py` — which reached scoring and gating by loading the MCP
*server file* as a module to borrow functions out of it, in three separate places. Every fix
re-described the specification to match the script, and **the declaring half was the half that
shipped.**

- **The pipeline moved into the library**: `lit2db.pipeline` (assemble, select, judge scheduling,
  question catalogue), `lit2db.scoring`, `lit2db.grounding`, `lit2db.output`. The MCP server and
  the headless driver are both thin callers now. `run_wave.py` 1208 → 777 lines and holds no
  decision about a record; `server.py` 638 → 520.
- **`stages/`, `tools/` and `adapters/` are deleted** — 154 lines and 18 `NotImplementedError`,
  unreachable for the plugin's entire published life while README and CODEMAP presented them as
  the architecture. **Deleting all three broke zero tests**, which is the proof.
- **The weight profile ships two signals**, because two is what the pipeline produces. `c_verbal`,
  `c_consistency` and `c_logprob` carried 0.35 of the declared mass and fired on none of 670
  scored fields; renormalization over present signals meant the composite still looked right,
  which is why it survived. The achievable lattice is unchanged — confirming they were inert.
- **`EvidenceTier` says plainly that the pipeline does not populate it.**
- **A colliding `record_id` is refused, not silently replaced.** `INSERT OR REPLACE` over ids that
  are not unique (15 records under 11 ids on one paper; ids are per-source ordinals) would have
  overwritten a row with no error and nothing in any artifact. Re-writing the same record stays
  idempotent, so a resumed run is unaffected.
- **The manifest no longer promises "structured data".** Structured *grounding* exists
  (`validate_mapping`); structured *ingest* never has.
- **`tests/test_declarations.py` — the plugin may not claim what it does not do.** No unreachable
  module, no unimplemented body, no empty function, no weight for an unproduced signal, no MCP
  tool unreachable from a command or agent, no manifest claim without an implementation, no id
  collision able to replace a row. The write-gate made quality mechanical rather than advisory;
  this does the same for the plugin's self-description.
  - **It found a thirteenth instance on its first run:** `entity.py` — a whole declared pipeline
    stage, with tests and its own `entity-resolver-agent` — which CODEMAP claimed was "wired into
    the MCP server" and which nothing imported. Now exposed as `resolve_entities`, alongside
    `screen_corpus` and `dedupe_corpus`, which were in the same state. 13 → 16 MCP tools.
- **Verified behaviour-preserving on 257 real records** from 16 saved paper-runs, scored and
  gated under four verdict conditions before and after: identical written sets, every write at
  exactly 1.0000.

520 → 532 tests.

## 0.32.0 — 2026-07-27
The adversarial judge stops pretending to be a score.

`c_judge` sat at weight 0.15 inside the confidence mean, beside `c_grounded` at 0.35, which
described the judge as one contributing signal among six. It never behaved like one:

| grounding | agreement | judge | composite (before) |
|---|---|---|---|
| 1.0 | 3/3 | SUPPORTED | **1.0000** |
| 1.0 | 3/3 | *(unjudged)* | **1.0000** |
| 1.0 | 3/3 | PARTIAL | 0.8846 |
| 1.0 | 3/3 | UNSUPPORTED | 0.7692 |
| 1.0 | 2/3 | SUPPORTED | 0.9231 |

Against a 0.95 bar only a unanimous, fully-grounded record could ever be written, and for such a
record the verdict changed nothing — it could only lower. **It was already a veto**, and
**139 of 165 judge calls could not have changed any outcome.** Because it lived inside the mean,
every one of them was paid for before anything knew which records mattered.

- **`c_judge` leaves the composite** (`DEFAULT_WEIGHTS`), and `ConfidenceComponents.composite()`
  now **raises** if a weight vector contains it. A project overrides these weights from its
  instantiation, so "do not put the judge back in the mean" is enforced rather than documented.
  The surviving weights are deliberately not re-normalized: `composite()` renormalizes over
  present signals, so deleting a key preserves every remaining ratio, and re-weighting them
  would be calibration — the researcher's to ratify, not the scaffold's to invent.
- **The verdict is a typed record-level state.** `ExtractedRecord.judge_verdict` (`not_run` /
  `unparseable` / `supported` / `partial` / `unsupported`) plus a `judge_note` for the reviewer.
  It replaces a float copied onto every field — a record-level fact (D-036) wearing a
  field-level shape.
- **The veto is a gate condition.** `gate_reasons` is now `selection_reasons` +
  `judge_veto_reasons`, composed. Only `supported` clears; `partial` blocks (it scored 0.885
  against a 0.95 bar before, so tolerating it would have quietly *loosened* the gate while the
  change was described as behaviour-preserving); and absence blocks, for the same reason
  `contradiction_search` blocks on `not_run` — a record nobody challenged has not passed its
  challenge. Not configurable, and applied at **both** enforcement points.
- **The driver judges after selection.** `run_wave.py` reorders to hunter → assemble → score →
  select → judge → gate, so an adversarial read is spent on records a verdict can actually
  decide. Nothing is written before every verdict is in hand.
- **A mandatory audit slice keeps the reject side measurable.** A ratified fraction of the
  records turned down *on evidence* is still judged — deterministically sampled from
  `blake2b(wave|paper|record_id)` so a resumed leg re-draws the same rows, and `ceil`-sized so a
  non-zero fraction never silently audits nothing. Rows turned down for a retracted source, a
  ratified review-only rule, or an incomplete counter-evidence search are excluded: no verdict
  can overturn those, so judging them would measure nothing. **A saving that erased the
  measurement justifying the pipeline would not be a saving.**
  - `judge_audit_fraction` has **no default and the driver refuses to start without it** — the
    same rule as D-038's forbidden truncation default. `scored.json` gains a `judge_scope` block
    reporting calls made against calls the old order would have made.
  - Two question kinds the old scheme could not express: **`judge_veto`** (the score would have
    written this and the judge struck it out) and **`audit_disagreement`** (the score turned this
    down and the judge found it supported — evidence the bar is too strict). Plus
    `verification_unusable`, so a paper denied wholesale by an unreadable hunter reply does not
    read like a paper with nothing in it.
- **Verified behaviour-preserving, on real records.** 212 records from 13 saved paper-runs,
  scored and gated under each verdict condition before and after: **identical written sets**
  (12 under SUPPORTED, 0 under PARTIAL / UNSUPPORTED / absent), every written record at exactly
  1.0000. `tests/test_judge_veto.py` re-proves it as an 80-case truth table against a frozen
  replica of the old arithmetic, so the claim survives the code it was made about.
- **Known and accepted:** with one signal fewer the score lattice coarsens from steps of 1/13 to
  steps of 1/10, so more records tie at the threshold. `achievable_composites()` pins this so a
  later weight change cannot make the top rung unreachable unnoticed — and documents the caveat
  that partial grounding scores land between the rungs, because a lattice is a floor on
  coarseness, not a promise about every score.
- **Surfaced, not decided:** the shipped profile now declares five weights of which **two**
  materialize on real fields (`c_verbal`, `c_consistency`, `c_logprob` fired on none of 670
  scored fields). Producing those signals or declaring a two-signal profile is a researcher call.

407 → 518 tests.

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
