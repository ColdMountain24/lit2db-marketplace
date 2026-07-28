---
description: Confirm candidates in a browser, each one beside the paragraph it came from.
---
The same loop as `/lit2db-review`, with the paper open next to the finding. Use this one when
the researcher wants to *read*, and the conversational one when they want to answer a few
questions and get on with their day. Both build the same calibration set.

Start it and hand over:

```bash
cd <plugin>/plugins/lit2db && PYTHONPATH=src python3 scripts/review_ui.py \
    --db <their.db> --sources <their stores>
```

With no database it builds a small demo and says so, so the page always opens onto something.
`--port` if 8765 is taken, `--all` to revisit records they have already ruled on.

## What they see

Left: one candidate — the value in their own words, which paper it came from, and why it stopped
short. Right: that paper's stored text, with the quoted sentence highlighted where the record
says it is. Then three answers, the same three as always: **yes that's right** · **no that's
wrong** · **can't tell from this**. `j`/`k` move, `1`/`2`/`3` answer.

Your job while it is open is to stay out of the way. Do not narrate the queue, do not summarize
records they are about to read, and do not ask them anything they are already being asked.

## What you must not do

- **Never write a record because a human confirmed it.** The page cannot reach the ML-ready
  table and neither can you. Their verdict is a measurement of whether the gate was right;
  spending it to bypass the gate destroys the only thing it could have told us.
- **Never talk them past a warning.** When the page says the quoted sentence is not where the
  record claims, it offers only *can't tell* — on purpose. That is not a bug to work around and
  not a record to rule on from memory of a similar one. A verdict given without the evidence is
  not a weak label; it is a wrong one, and afterwards it is indistinguishable from a good one.
- **Never rephrase what they typed in the note box.** It is stored verbatim. If they write "the
  compound is right but the organism is wrong", that is the note, and the verdict is `wrong` —
  deciding which half won is domain substance, and it is theirs.
- **Never let a run of `cant_tell`s look like progress.** If most answers come back that way,
  stop and say so: it means the sources are too thin to calibrate against, and the fix is
  access, not more reviewing. A page full of abstract-only papers is that situation, and the
  page labels each one so you can see it.

## After a sitting

Same as `/lit2db-review`: report `calibration_report` honestly — how many they adjudicated and
how many of those were verifiable, precision per bucket with its sample size and interval, and
what the table implies for where the bar *could* sit, as options with their costs.

Verdicts given here are recorded as coming from the browser, and `calibration_report` breaks the
counts down by surface. **Report that split whenever both surfaces have been used.** The two do
not apply the same conditions — this one refuses "right"/"wrong" when it cannot show the quote,
the conversational loop asks an agent to — so a sharp gap between the columns means part of what
the calibration set measured is how the question was put, not how good the extractor is.

**Do not change the accept threshold.** Where it sits is a promise to whoever uses this database
about how wrong it is allowed to be. That number belongs in the project's instantiation, ratified
and written down — not in a tool call, and not in a browser.
