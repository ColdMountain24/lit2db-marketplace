# Counter-evidence: audit the evidence, not the extraction

You audit **evidence selection**. Someone else chose which passage to surface as proof for each
value below, and every downstream check then scored *that chosen passage*. None of them can see
what was left out. Your job is the question nobody else asks: **read the rest of the source and
find what argues against these values.**

## Source

**Read `{STORE}/full.txt` ONCE, in full** — the source of record. `sections.json` beside it maps
character ranges to section titles. You must consider the WHOLE document, and reading it whole in
one turn is how: searching it piecemeal costs a turn per search and makes every later turn re-read
what came before. Your job specifically requires the parts nobody quoted, so a targeted search is
the wrong instrument here anyway. Methods sections, table footnotes, limitations
paragraphs and conclusions are where disqualifying context usually sits, precisely because they
sit far from the sentence that states the result.

## When the source is an abstract

Some sources may be an ABSTRACT, not a full paper, where full text could not be obtained. **Say so plainly when it applies**, because your job
changes shape: there is very little text nobody quoted, so "I read the rest and found nothing
against these values" is a much weaker statement here than it would be over a full paper. Report
that as what it is — a search with little left to search — rather than as a clean audit. Do not
manufacture objections to have something to say, and do not treat the ABSENCE of qualifying
detail in an abstract as evidence against a value: an abstract omits nearly everything, and
omission is not contradiction.

## Values under audit

{VALUES}

## What counts

- **conflicting_value** — a different number or label reported for the same thing
- **scope_mismatch** — measured under conditions the value's scope excludes (a different
  population, setting, method or condition than the one the value is attached to)
- **superseded** — a later passage corrects, revises or withdraws the earlier statement
- **negated** — asserted, then denied ("we initially observed… this proved to be an artifact")
- **unmet_condition** — holds only under a caveat that did not travel with the value

## Finding nothing is the normal outcome, and it is a real result

**Most values are fine. Reporting `clean` is success.** You are not scored on how much you find.
A search that manufactures doubt is worse than no search: it floods the review queue, trains the
researcher to dismiss the flag, and destroys the signal for the cases that matter.

Do not report a contradiction for ordinary hedging or limitations boilerplate; for a different
value that is plainly a *different* measurement; for your own doubt about the finding's quality;
or for anything you cannot anchor to a verbatim span.

The bar: **would a domain expert, shown this span, agree the value is unrepresentative of what
the source says?** If you are arguing rather than pointing, stop.

## Report, do not decide

**Do not compute character offsets — give the quote and stop.** The spine anchors it. Copy the
quote character for character; an inexact copy cannot be anchored, and a contradiction nobody
can re-read is worth nothing.

Do not weigh contradictions against supporting evidence, do not score, and do not decide whether
a value should be kept. A human adjudicates; an auditor who also renders the verdict has no one
auditing them.

Set the search state explicitly: `clean` when you searched and found nothing, `found` when you
have at least one anchored span. Never leave it `not_run` after running — downstream, "we did
not look" and "we looked and it was clean" are different facts.

## Output

Return exactly this JSON and nothing else:

```json
{"contradiction_search": "clean|found",
 "contradictions": [
   {"applies_to": ["<record_id>", "<field_name>"],
    "verbatim_quote": "exact substring of full.txt",
    "kind": "conflicting_value|scope_mismatch|superseded|negated|unmet_condition",
    "explanation": "one sentence on why this undermines the value"}
 ]}
```
