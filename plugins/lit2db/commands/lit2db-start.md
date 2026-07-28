---
description: Start here. One guided intake, then lit2db builds your database.
argument-hint: [what you are collecting, in your own words]
---

The front door. **Assume the researcher knows their field and nothing about this tool.** They
should never see the word "schema", "ledger", "adapter" or "unit of analysis" unless they ask.

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

## 3 — Show the corpus before spending anything

Run the query, then tell them: **how many papers, how many are readable, and how many are not.**
Readable means full text or an abstract you can actually obtain — measure it, do not assume it.
A source that is indexed is not a source you can read.

Then run **`preflight`** (`scripts/run_wave.py --dry-run`). Configuration problems surface here,
before a single model call. Fix them or float them; never start a run that will die on paper six.

## 4 — Set the expectation honestly, in this order

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

## 5 — Run it, and stay with them

Launch the wave. While it runs, report progress in papers, not tokens. When it finishes, hand off
to `/lit2db-status`, which shows both tiers.

**Anything that blocks is a question, not a stop** (D-095). A configuration refusal floats to the
researcher and waits. If they are away, skip that source, record why, and keep going — an
unattended run should use the night and leave a decision list, not halt on paper one.

The one thing that never floats: a record that fails verification is not written. That is not a
setting, and it is what makes the rest of it worth anything.
