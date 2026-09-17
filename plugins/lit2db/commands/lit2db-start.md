---
description: Start here. One guided intake, then lit2db builds your database.
argument-hint: [what you are collecting, in your own words]
---

The front door. **Assume the researcher knows their field and nothing about this tool.** They
should never see the word "schema", "ledger", "adapter" or "unit of analysis" unless they ask.

## 0 — Clear the environment yourself, before they see anything

**Run this first, every time, before printing anything:**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py --json
```

The plugin's MCP server needs Python 3.10+ with `pydantic` and `mcp`, and a Mac ships 3.9.6. It
starts through `bin/lit2db-python`, which uses uv to fetch its own Python so the researcher never
installs one. If that cannot run, the server never starts and its tools are simply absent — no
error, no traceback, nothing naming the cause. A researcher cannot diagnose that, and should not
have to. **This command is the front door, so it owns the problem.**

Act on `remedy`:

| `remedy` | what you do |
|---|---|
| `none` | say nothing about it. **But check you actually hold the `mcp__plugin_lit2db_lit2db__*` tools.** If the environment is ready and you do not, the server failed at session start (a first-run download interrupted, say): tell them to quit Claude Code, start a new session, and run `/lit2db-start` again, and stop. Otherwise go to step 1. |
| `install_uv` | **offer to run the `fix` command for them**, in one sentence, in plain language: *"One-time setup: I need to install a small tool called uv that gives this plugin its own copy of Python. It won't touch your Python. Shall I?"* Run it on yes, then run `doctor.py --json` again (that download is the slow part, so it happens now rather than at the next start). Then the restart below. |
| `uv_failed` | read them the `fix` text in plain words. The usual cause is no internet on the first run. Stop. |
| `reinstall_plugin` | tell them to re-run `/plugin install lit2db@lit2db-marketplace`, and stop. |

**If `restart_required` is true, say this and stop — do not continue into the interview.**
Installing the packages does not start a server that already failed to boot, and
`/reload-plugins` does not restart it either; it refreshes commands, hooks and agents only.
Tell them to quit Claude Code, start a new session, and run `/lit2db-start` again. Say it is a
one-time cost, because it is.

**Never make the researcher read the diagnosis.** They asked to build a database. Repair what
can be repaired, state plainly what cannot, and never print a checklist at someone who did not
ask for one.

## 1 — Show them where they are

Print this, exactly, before anything else:

```
   ┌────────────────────────────────────────────────┐
   │   l i t 2 d b                                  │
   │   literature  →  a database you can defend     │
   └────────────────────────────────────────────────┘

   Every value will carry the sentence it came from.
   Nothing enters the database that could not be checked.

   Four questions. Then it runs.
```

Then one line naming what they typed in `$1`, so they see they were heard.

## 2 — Ask everything at once, in their language

**Four `AskUserQuestion` calls, batched — not an interview.** A researcher should be done in two
minutes. Each question must read as *their science*, never as configuration. Offer concrete
options derived from what they said, and always let them write their own.

| ask them | you are really resolving |
|---|---|
| "What is one row of your table?" — a compound? an enzyme? a clinical trial? | entity class + unit of analysis |
| "What do you want to know about each one?" — offer 5–8 concrete fields from their domain, multi-select | the field set |
| "Which literature counts?" — years, open access only, a journal set, a list of DOIs they already have | source scope |
| "What would make you throw a paper out?" | inclusion / exclusion |

**Derive everything else yourself and say you did.** The search query, identity chain, negative-data
policy, evidence tiers and provenance granularity are STRUCTURE — propose them, state them in one
plain sentence each, and let the researcher correct. Never ask a researcher to specify a query
string; show them the papers it returns and ask if that looks like their field.

**If they gave you a reference database, a review, or a list of DOIs, use it as the seed** — it is
the best statement of scope you will ever get, and comparing against it afterwards is the strongest
result this tool can produce.

## 3 — Hand off to the full interview, carrying everything forward

**Do not run anything yet.** The four answers above are a warm-up, not a ratification: a
researcher clicking through four questions has approved far less than they think, and the schema
that comes out of this is the thing every later value is checked against.

So invoke **`/lit2db-new-project`** and let the **scope-elicitation-agent** run the real Stage-0.5
interview — with the intake pre-loaded, so it is a confirmation pass rather than a second
questionnaire. Hand it a structured summary:

- **What the researcher said, verbatim.** Their words for the row, the fields, the literature and
  the exclusions. These carry into the ledger as PROPOSED with their own phrasing attached, and
  the interview's job on them is *"you said X — is that right?"*, which is fast.
- **What you derived, flagged as yours.** The search query, identity chain, negative-data policy,
  evidence tiers, provenance granularity. Each gets the full propose-and-ratify treatment,
  because the researcher has not seen these at all.
- **What is still unanswered.** The axes the four questions do not reach. Name them; do not let
  the interview rediscover them.

**The distinction is the whole point.** An item the researcher stated is theirs and needs
confirming; an item you inferred is yours and needs ratifying. Presenting the two the same way is
how a schema ends up containing fields nobody chose — which has happened in this project's own
history, twice, and both times the scaffold was correct and the work was routed around it.

The interview may not skip an axis because the intake "probably covers it". Speed comes from the
researcher confirming a concrete proposal, never from asking them less.

## 4 — Show the corpus before spending anything

Everything from here runs through one tool, in the researcher's current folder:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/bin/lit2db-python" "${CLAUDE_PLUGIN_ROOT}/scripts/project.py" <step> ...
```

**Write that out in full on every call.** Do not store it in a shell variable: a Mac's default
shell is zsh, which does not split an unquoted variable into words, so `$VAR preview` fails with
"no such file". Measured on the first clean run. Below, `project.py <step>` is shorthand for the
full line.

Each step prints JSON and exits nonzero on a refusal. Their data lands in `./lit2db/`, never in
the plugin, which an update replaces.

**Before the query is ratified, show them what it returns.** Nothing is downloaded:

```bash
project.py preview --query '<the query you propose>' --limit 100
```

Tell them, in this order: how many papers match, how many of the first hundred can be read in
full, how many only as an abstract, and ten titles. Readable means Europe PMC holds open full
text; measure it, do not assume it. Expect it to be low: on a vitamin D trials query, 14 of the
first 50. If most of their literature is abstract-only, say so now, and ask whether abstracts are
admissible. That answer is theirs, and it goes in the ledger. A query that pulls in reviews,
corrigenda and conference abstracts is a structure problem you fix by proposing a better query
(`NOT PUB_TYPE:"review"`, say), not by filtering quietly afterwards.

**Once the spec is frozen at `./lit2db/spec.json`, fetch the papers.** `fetch` runs the ratified
query exactly as written and refuses a spec that does not validate:

```bash
project.py fetch --limit 20 --abstracts      # --abstracts ONLY if the ratified scope admits them
```

Start at 20 for a first database. Report the counts it returns: full text, abstract only,
unreachable, retracted. Retracted papers are never read.

**Then write the run configuration.** These are ratified choices, so put each to the researcher
in plain words first and pass what they chose:

```bash
project.py plan --entity-type <slug for one row> --identity <the ratified identifying field> \
    --models sonnet sonnet sonnet --judge-model opus --hunter-model sonnet \
    --audit-fraction <their answer>
```

- `--identity` is the field Stage-0.5 axis 5 ratified as what makes two rows the same thing.
- `--models`: three independent readings, whose agreement is a signal. The default fact-checker
  is a different model (Opus checking Sonnet), and that is the one independence it has.
- `--audit-fraction` has no default, on purpose (D-081). Ask: *"Of the values the pipeline turns
  down, what share should still get a second check, so you can tell how often it wrongly
  rejects something? The first project used 20%."*
- The accept threshold stays at the conservative 0.95 until they have reviewed enough records
  to set it from their own data.

`plan` runs the preflight itself. `preflight_ok` must be true. Configuration problems surface
here, before a single model call: fix them or float them, and never start a run that will die on
paper six.

**Say what it costs before it starts.** It runs on their own Claude plan. Measured on a 183-paper
run: about 62 thousand new tokens per paper, plus several times that in cheap re-reads. If a usage
limit is hit, the run waits for it to reset and carries on by itself.

## 5 — Set the expectation honestly, in this order

**Say what they get, worst case first.** This is the honest floor and it is already useful:

1. **A screened list of papers** — the corpus, with what the screen dropped and why. Even if
   everything downstream disappoints, they have a defensible reading list they did not have.
2. **A candidate pool** — every value the pipeline found, each with its quote, its location in the
   source, and the reason it did not auto-accept. This is the seed: it is most of the work of
   confirming a finding, already done.
3. **An ML-ready table** — only what cleared the bar, and this tier will be SMALL at first.

**Do not promise a perfect database, and do not apologise for an imperfect one.** A researcher who
gets 40 confirmed rows and 300 pre-quoted candidates has been given weeks of their life back. Say
that plainly. The honest framing is "here is what I am confident of, here is what is worth your
eyes, and here is everything I looked at" — all three, always, never the first alone.

## 6 — Run it, and stay with them

```bash
project.py run          # long; start it in the background. Resumable: re-running skips finished papers.
```

Run it in the background and report progress in **papers**, never tokens: count
`lit2db/run/*/scored.json` against the paper count `plan` returned.

**Anything that blocks is a question, not a stop** (D-095). The run writes questions only a
researcher can answer to `lit2db/run/QUESTIONS.jsonl` and keeps going. When it finishes, bring
those questions to them, then:

```bash
project.py export
```

and tell them where the three spreadsheets are, in this order, with the count in each:
`lit2db/results/papers.csv` (every paper, and what happened to it), `candidates.csv` (values found
but not accepted, each with its quote and the reason), `accepted.csv` (what cleared the bar).
Then hand off to `/lit2db-status`.

The one thing that never floats: a record that fails verification is not written. That is not a
setting, and it is what makes the rest of it worth anything.
