# 2026-09-18 — first wave, BBB large-molecule database (paused at 6/20)

**Verdict:** The pipeline is working and the corpus is good; the *scoring* is not calibrated for
this project, and three defects — two of them silent — were found and fixed before they reached
the output. No usable accepted table yet, by design rather than by failure. 50 pre-quoted,
judge-audited candidates exist and are the real product of this wave.

Run paused deliberately after 6 papers to write this report. Not a crash: `project.py run` is
resumable and every completed pass is on disk.

---

## Would have been invisible

**1. Half of every paper's records were being dropped by a primary-key collision.**

`ensemble.py` named each merged record after the seed pass's `record_id`, which is a *per-paper
ordinal* (`r1`, `r2`, `r3`). The number of available names is however many records **one pass**
emitted; the number of merged records is however many distinct identities the chain resolved.
Those diverge the moment an entity type is finer-grained than the extractor's numbering — which
is exactly the measurement grain this project ratified.

`records` is `record_id TEXT PRIMARY KEY`, `candidates` is `PRIMARY KEY (record_id, source_id)`,
both written `INSERT OR REPLACE`. Colliding records store as one row, with no error and nothing
in any artifact showing a row had gone.

Measured on PMC13532014: **6 merged → 6 scored → 3 stored.**

Fixed: duplicated ids take a suffix hashed from the *identity*, not a positional counter, so ids
survive resume, replay and merge-order changes. A positional counter would rename records
whenever merge order shifted, and a renamed record silently becomes a different database row.
Unambiguous ids keep their clean `r1`. After the fix, across 6 papers: **50 merged → 50 stored,
0 collisions.**

The pipeline already *reported* this via `colliding_record_id` questions and its own source
comment predicted it — *"It has never fired … which is exactly why it needs saying out loud
before it does."* It fired here. Reporting is not prevention.

**2. The ratified row grain was silently collapsed to the wrong grain.**

`plan --identity molecule_key` looks correct — `molecule_key` is the ratified join key. But
`ensemble.py:460` gives a bare identity with no `ordinal_within` a specific meaning: *"its
absence is a decision … it says this entity type has at most one record per identity."* That is
**molecule** grain. The project ratified **measurement** grain, where one molecule legitimately
carries many rows.

Measured on PMC13532014: same three extraction passes, reused from disk for free —
**3 records before, 6 after** the identity chain was corrected. Half the measurements had been
collapsing into their siblings.

**3. Every model call was billed to an API key instead of the subscription.**

`run_wave.py` calls `subprocess.run(cmd, ...)` with no `env=`, so every spawned `claude -p`
inherits whatever credential is ambient. `ANTHROPIC_API_KEY` was set in the shell, the run drained
the credit balance and died after 1 of 20 papers with **171 × `"Credit balance is too low"`** —
while the subscription sat unused. Verified by direct comparison: with the key present the call
fails; with `env -u ANTHROPIC_API_KEY` it returns normally.

This one announced itself loudly *as a credit error*, which is the wrong diagnosis — it looks
like "top up your account", and topping up would have worked while leaving the real defect
untouched and the subscription still unused.

---

## What broke loudly

- **Nothing else.** After the credential fix: `error_signatures: {}`, `retry_attempts_burned: 0`
  across 6 papers. No timeouts, no unreachable sources, no retracted papers, no parse failures.

---

## What was wasted

- **~171 failed API calls** across 19 papers before the credential cause was found. Recovered
  nothing; cost real credit balance.
- **One paper's judge calls, spent twice.** PMC13532014 was scored under the broken identity
  (3 records, 3 judge calls), then rescored after the fix (6 records, 6 judge calls). Its three
  *extraction* passes — the dominant cost — were reused from disk both times at zero cost, which
  is the resume logic working as designed.
- **3 extraction passes currently unmerged** (PMC12357843), from the deliberate pause. They are on
  disk and will be reused free on resume. Not lost.
- **Not wasted, worth noting:** stopping the run before papers 3–20 ran under the broken
  `record_id` avoided ~19 papers of silent half-loss and the judge calls to produce it.

---

## What only a researcher can settle

62 questions catalogued. `QUESTIONS.jsonl` **appends across runs**, so counts include re-runs of
the same paper unless cleared — it was cleared before this wave, so these are clean.

| kind | n | what it means |
|---|---|---|
| `scope_disagreement` | 30 | a value only one of three readings found |
| `audit_disagreement` | 21 | **turned down, but the judge read the source and found it SUPPORTED** |
| `weak_identity` | 8 | records aligned only by order of appearance |
| `unanchorable_quote` | 2 | a quote that could not be located in the source text |
| `no_consensus_value` | 1 | three readings, three different values |

The 21 `audit_disagreement` entries are the most valuable output of this wave. They exist only
because the researcher set `judge_audit_fraction: 1.0` rather than the suggested 0.20. At 0.20
roughly four of them would have surfaced and the rest would have gone unexamined, leaving an
empty accepted table with no evidence of *why*.

---

## Numbers

| | |
|---|---|
| papers planned / scored | 20 / 6 (paused) |
| merged records | 50 |
| candidates stored | 50 — **reconciles exactly, no silent loss** |
| accepted records | **0** |
| composite confidence | median **0.10**, max **0.80**, min 0.10 |
| records ≥ 0.95 (the gate) | **0** |
| records ≥ 0.70 (a typical calibrated gate) | **2** |
| judge verdict on the 50 denials | 21 supported · 21 partial · 8 not_run |
| fields populated per record | 9–19 of 23 (most at 13–16) |
| all 4 required fields present | 23 of 50 |
| identity tiers | primary 18 · fallback1 15 · **ordinal 23** |
| wall clock | 1310s for 6 papers, median 128s/paper |
| passes reused free | 3 |

**The single most important number here is the confidence distribution.** The threshold is not
*slightly* too strict — the confidence scale and the gate are in different ranges entirely. The
records are not thin (9–19 fields populated, 23 of 50 complete on all required fields) and the
adversarial judge calls 42% of the denials SUPPORTED, yet the median composite is 0.10 against a
0.95 gate. Lowering the threshold to a "calibrated-looking" 0.70 would still accept only 2 of 50.

So the calibration task is **the confidence weights, not just the threshold**. `DEFAULT_WEIGHTS`
is shipped as illustrative and this project has not replaced it. Setting a threshold against an
uncalibrated weight vector would produce a number with no meaning behind it.

**Timing correction:** paper 1 took ~9 minutes during the failing run and that estimate (≈3 hours
for 20 papers) was repeated to the researcher. It was wrong — inflated by retry storms against a
dead credential. The real median is **128s/paper**, so the remaining 14 papers are roughly
**30 minutes**, not hours.

---

## Still open

**Not fixed, deliberately — each will fire again.**

1. **`project.py plan` cannot express the ratified identity.** `--identity` takes one field name
   and validates it against the schema, so the composite chain had to be hand-written into
   `run.json`. **Re-running `plan` silently restores the collapsing behaviour.** Backup at
   `lit2db/run.json.bak`. This is a scaffold limitation, not a configuration mistake: any
   measurement-grain project hits it.
2. **`QUESTIONS.jsonl` appends across runs.** Re-running a paper duplicates its questions, so the
   catalogue count is not the count of distinct open questions. Deduplicate before reporting.
3. **40% of records align by `ordinal` (23 of 56 keys)** — the tier the ensemble explicitly
   cannot vouch for, where disagreement may be mis-pairing rather than real. The identity ladder
   written for this project leans on its weakest rung because the top composite requires six
   fields (incl. `sampling_time_post_dose_h`) that most papers do not all report. A v2 chain with
   more intermediate tiers would move records up. Not changed mid-run: it would invalidate
   comparison with the 6 papers already scored.
4. **TEER-as-resistance has no metric.** `TEER/permeability` is one ratified assay method covering
   two readouts; spec v1 added apparent permeability (cm/s) but nothing for resistance (ohm·cm²).
   A TEER-only paper still cannot satisfy the required `penetration_metric`. Current position: a
   TEER-only paper has no penetration measurement. Researcher has not contradicted it.
5. **Reference-mining of reviews is ratified but not built.** Reviews are excluded from extraction
   and admitted as finders only; the toolkit discards reference lists from stored papers, so this
   is separate unbuilt work.
6. **Figure-only values need a manual list.** Figures are not stored — only captions. In this
   literature %ID/g time-courses are frequently figure-only, so the table is biased toward papers
   that tabulate until this exists.
7. **Three columns are agent-originated domain substance**, ratified but not researcher-
   originated: `value_qualifier`, `barrier_state`, `sampling_time_post_dose_h`. The spec marks
   them; any datasheet must keep the distinction.

**Recommended next actions, in order:** resume the run (≈30 min); hand-review the 21
`audit_disagreement` records, which are pre-argued both ways and the cheapest possible gold-set
seed; recalibrate weights *then* threshold from that set; only then read the accepted table as
meaningful.
