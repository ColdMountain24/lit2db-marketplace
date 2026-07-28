---
description: Confirm candidate records one at a time. Builds your database's calibration set as you go.
---
The researcher checks a handful of candidates; the plugin learns where its accept bar belongs.
**Both halves matter, and only the first is worth their time — so lead with that one.**

Never open by explaining calibration. Open with: *"here are the findings that came closest to
making it into your clean table — do these look right?"* The calibration set is what falls out
of answering; it is not what you ask them for.

## Why this exists (context for you, not a speech for them)

The accept bar was measured on this project's own data and it barely separates right from wrong:
sweeping it moved precision 41% → 54% while recall fell 37% → 13%. No threshold rescues a signal
that weak, and nobody could say where to put the bar because nobody had labels. Commissioning a
gold set was on the roadmap for weeks as a blocker.

It does not have to be commissioned. A researcher looking at candidates and saying "yes, that
one is real" **is** the labelled data — and roughly **30–50 records** is enough to tell a good
configuration from a bad one. That is an afternoon, not a project.

## The loop

1. **`review_queue`** with `unadjudicated_only=true`, best-first. Best-first because near-misses
   are where a minute of attention converts into a row.
2. For each record, show — in the researcher's own language, never the schema's:
   - the **value**, plainly (the compound, the enzyme, the measurement);
   - the **quote from their paper** that the extractor relied on, and which paper it came from;
   - **why it stopped short**, translated: not `composite 0.900 < auto-accept 0.95` but
     *"two of the three readings found this; the third did not"*; not
     `judge_verdict=partial` but *"the checker thought the paper supports part of this but
     not all of it."*
3. Ask with `AskUserQuestion`, batched — up to four records per call, never one at a time.
   Three answers, and the third is not a skip:
   - **Yes, that's right** → `right`
   - **No, that's wrong** → `wrong`
   - **Can't tell from this** → `cant_tell`
4. **`record_adjudication`** for each answer, with the researcher's note if they gave one.

`cant_tell` is load-bearing. Much of the literature is behind a paywall, so "the text I can read
does not settle it" is often the honest answer — and recording it as *wrong* would calibrate the
database against the reach of a library subscription rather than against the extractor. If a
researcher hesitates, that is the option to offer them.

## What you must not do

- **Never write a record because a human confirmed it.** `record_adjudication` cannot reach the
  ML-ready table and must not be worked around. Their verdict is a measurement of whether the
  gate was right; spending it to bypass the gate destroys the only thing it could have told us.
- **Never ask them to adjudicate a record you have not shown the quote for.** A verdict given
  without the evidence is not a label, and it will calibrate the bar just as confidently.
- **Never rephrase their verdict.** If they say "the compound is right but the organism is
  wrong", record `wrong` and put their sentence in the note verbatim. Deciding which half won is
  domain substance, and it is theirs.
- **Never let a run of `cant_tell`s look like progress.** If most answers are `cant_tell`, stop
  and say so: it means the sources are too thin to calibrate against, and the fix is access, not
  more reviewing.

## After a sitting

Report `calibration_report`, and report it honestly:

- how many records they have now adjudicated, and **how many of those were verifiable** —
  `cant_tell` rows are held out of every precision figure and counted beside it;
- precision per bucket with its sample size and 95% interval. **A bucket of three reading 100%
  is not a finding**; say so rather than printing it bare;
- what the table implies for where the bar could sit — as *options with their costs*, never as
  a recommendation the plugin has already acted on.

**Do not change the accept threshold.** Where it sits is a promise to whoever uses this database
about how wrong it is allowed to be. Show them what each setting would buy and let them ratify
it; the number belongs in the project's instantiation, written down, not in a tool call.
