---
name: meta
description: Write the post-mortem for a lit2db run — what happened, what broke, what was wasted, and what nearly went wrong silently. Use after a run finishes, after a run dies, or when a run's output looks wrong. Produces a dated markdown meta that appends to a committed log, so each run's lessons survive the run. Reads artifacts only; needs no credential and no network.
---

# meta — the run post-mortem

A lit2db run leaves behind a log, a pile of JSON, a SQLite file and a question catalogue.
This skill turns that into one page a person can read six months later, and appends it to a
log in the repo so the lessons outlive the run.

**The value is in the near-misses.** A run that crashed announces itself. A run that
completed while silently dropping half its records does not, and that is the failure this
document exists to catch. Anything that *would* have been invisible goes at the top.

## LAW: never use an API key

**No step of this skill, and no command it suggests, may use `ANTHROPIC_API_KEY`.**
Subscription authentication only. Where a command spawns `claude`, it must be prefixed:

```bash
env -u ANTHROPIC_API_KEY <command>
```

This is not a preference. `scripts/run_wave.py` calls `subprocess.run(cmd, ...)` with no
`env=`, so every spawned `claude -p` silently inherits whatever credential is in the ambient
environment. On 2026-09-18 that drained an API credit balance and killed a run after 1 of 20
papers with 171 × `"Credit balance is too low"`, while the subscription sat unused.

**Never echo a variable to test whether it is set.** `${VAR:-NO}` expands to the *value* when
the variable is set, which leaks the credential into the transcript. Use:

```bash
[ -n "${ANTHROPIC_API_KEY+x}" ] && echo SET || echo UNSET
```

If a run failed on credit or billing, **check for an inherited API key before concluding
anything about quota.**

## Step 1 — measure, do not skim

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/meta/collect.py" . > /tmp/meta.json
```

`collect.py` parses `lit2db/run.log`, walks `lit2db/run/`, reads `database.db` read-only,
counts `QUESTIONS.jsonl` by kind, and summarises `spec.json` and `run.json`. It needs no
credential, makes no network call, and exits 0 on a broken run — a dead run is the case it
is for, so a missing artifact is data, not an error.

Read the `signals` block first. It is built to answer, in order:

| signal | the question it answers |
|---|---|
| `completion_fraction`, `papers_never_scored` | did the run actually do the work? |
| `error_signatures` | classified causes, not raw `exit 1` counts |
| `recoverable_waste` | papers whose passes were paid for but never merged |
| `passes_reused_free` vs `retry_attempts_burned` | what resume saved vs what retries cost |
| `per_paper_yield` | passes found → merged → selected, per paper |
| `papers_scoring_nothing` | papers that read fine and yielded nothing |
| `passes_disagreed_on_count` | where the three readings saw different numbers of things |
| `everything_routed_to_review` | zero accepted with a non-empty candidate pool |
| `threshold_is_uncalibrated` | whether the accept bar is still the placeholder |
| `questions_by_kind` | what only a researcher can settle |
| `slowest_papers`, `median_paper_seconds` | where the wall clock went |

## Step 2 — reconcile the log against the database

**Do this every time. It is the step that catches silent loss.** The log reports what the
pipeline *computed*; the database holds what *survived*. When they disagree, the difference
is data that no longer exists, and nothing else in the run will tell you.

```bash
# merged vs scored vs stored, per paper
grep -E "records|scored ->|written" lit2db/run.log | grep -v "pass[0-9]/"
python3 -c "import sqlite3;c=sqlite3.connect('file:lit2db/database.db?mode=ro',uri=True);\
print('records',c.execute('select count(*) from records').fetchone()[0]);\
print('candidates',c.execute('select count(*) from candidates').fetchone()[0])"
```

Known ways the two diverge — check each, and state in the meta which you ruled out:

- **`record_id` collisions.** `ensemble.py:601` inherits `record_id` from the seed pass
  record, so merged records are named with the extractor's per-paper ordinals. If a merge
  produces more records than any single pass labelled, ids repeat; `candidates` is
  `PRIMARY KEY (record_id, source_id)` and `records` is `record_id TEXT PRIMARY KEY`, both
  written with `INSERT OR REPLACE`. **Six merged records with three ids store as three rows.**
  A `colliding_record_id` question is the pipeline warning you this happened. Measured on
  PMC13532014 at measurement grain: 6 merged, 6 scored, 3 stored.
- **`QUESTIONS.jsonl` appends across runs.** Re-running a paper duplicates its questions, so
  the catalogue count is not the count of distinct open questions. Deduplicate before
  reporting, and say whether you did.
- **Stale rows from a superseded config.** A re-run under a changed identity or schema can
  leave orphan rows keyed under ids the new config never produces.

## Step 3 — write the meta

Write to `lit2db/meta/<YYYY-MM-DD>-<short-slug>.md`. One run, one file. Use exactly these
sections, in this order, and keep them in this order even when a section is empty — an empty
"silent failures" section is a finding, and a reader needs to know it was looked for.

```markdown
# <date> — <what this run was>

**Verdict:** <one sentence: did this run produce usable output, yes or no>

## Would have been invisible
## What broke loudly
## What was wasted
## What only a researcher can settle
## Numbers
## Still open
```

Rules for the writing, all of them load-bearing:

1. **Every claim carries its evidence.** A log line, a `file.py:line`, or a count. A
   post-mortem that cannot be checked is a mood, and the next reader cannot act on a mood.
2. **Name the mechanism, not the symptom.** "Half the candidates vanished" is a symptom.
   "`record_id` is inherited from the seed pass record, and six groups drew from three
   labels" is a mechanism someone can fix.
3. **Record what was NOT fixed, and why.** A known-live defect left in place is the single
   most valuable line in the document. Say what it will cost when it fires.
4. **Distinguish a scaffold limitation from a configuration mistake.** The first needs an
   upstream change; the second needs a different flag. Conflating them sends the next person
   to the wrong file.
5. **Quote the researcher's decisions verbatim** where the run turned on one, and attribute
   agent-originated content as agent-originated. Ratified is not the same as researcher-
   originated, and a meta that blurs that re-creates the problem the ledger exists to prevent.
6. **No speculation dressed as measurement.** If something is a hypothesis, label it. If a
   number was not measured, do not print it — write "not measured" and say what would measure it.
7. **Write the numbers even when they are bad.** Zero accepted records is the headline finding
   of an uncalibrated run, not something to soften.

## Step 4 — append to the log

A researcher's project folder is usually **not** a git repo — `lit2db/` is working data, not
history. The dated meta is written there because it describes that run, and a copy goes to the
**plugin repo**, which is the thing that gets improved and pushed:

```bash
mkdir -p "${CLAUDE_PLUGIN_ROOT}/meta"
cp lit2db/meta/<file> "${CLAUDE_PLUGIN_ROOT}/meta/"
# index entry, newest first
printf -- "- [%s](%s) — %s\n" "<date>" "<file>" "<one-line verdict>" \
  >> "${CLAUDE_PLUGIN_ROOT}/meta/META_LOG.md"
git -C "${CLAUDE_PLUGIN_ROOT}" add meta && git -C "${CLAUDE_PLUGIN_ROOT}" commit -m "meta: <date> <slug>"
```

`META_LOG.md` is an index of one-line pointers, never a place for content — the content lives in
the dated file. Keeping the log in the plugin repo is the point: a lesson from one project is
only worth writing down because the next project inherits it.

Never commit `database.db`, `lit2db/corpus/`, `lit2db/run/` or `lit2db/env.sh` — the last holds a
contact address, and the others are large, regenerable, and in `corpus/`'s case hold licensed
full text that must not be redistributed. **Read the meta before committing it:** run artifacts
can carry verbatim quotes from paywalled sources, and a post-mortem is not a place to republish
them. Quote the pipeline's own log lines, not the papers.

## What this skill must not do

- **Never fix what it finds mid-write.** Diagnosis and repair in one pass produces a document
  describing a system that no longer exists. Record it, then repair as separate, stated work.
- **Never grade the researcher's decisions.** A decision that produced a hard trade-off is
  reported with its cost, not second-guessed. What the run did with a decision is fair game;
  the decision is theirs.
- **Never soften a finding to make a run look better.** The only reason this document exists
  is that the next run inherits what this one learned.
