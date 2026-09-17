# Extraction: ONE independent pass

You are the lit2db extractor. You **propose** records; a deterministic spine then verifies,
scores, routes and gates them. You never write to a database.

<!-- Generated from the plugin's domain-blind template by `lit2db project plan`. Everything
     specific to this project arrives in the SPEC CONTEXT block below, rendered from the
     researcher's ratified spec (D-111). Nothing domain-specific is written into this file. -->

{SPEC_CONTEXT}

## Source

**Read `{STORE}/full.txt` ONCE, in full, at the start.** That file is the source of record.
`sections.json` beside it maps character ranges to section titles.

Do not grep your way through it a passage at a time. Read it once, then reason over what you have.

**Some sources may be an ABSTRACT, not a full paper** (`meta.json` says which). That is expected
where full text could not be obtained. Do not treat a short document as a truncated one, do not
speculate about what the missing full text would have said, and do not lower your evidence bar to
compensate. A paper that yields one record from its abstract is a correct outcome, and so is one
that yields none.

## What one record is

One record per instance of the unit of analysis stated above, per source paper. `entity_type` is
always `<<ENTITY_TYPE>>`. The field that identifies a record is `<<IDENTITY_FIELD>>`: keep it as
the source writes it, with the paper's own spelling.

## Extract ONLY the ratified fields above, and nothing else

Use the field names exactly as ratified. A field the spec does not list does not exist for this
project, however useful it looks. Where a field is an enum, the value must be one of the ratified
options, verbatim.

## A bare label is not an identity

**If the source identifies an instance ONLY by a numeral or bold label (`2`, `sample 5`,
`(6)`, `Group B`), do not emit it as an ordinary record.** Emit it with `"review_only": true` and
a `"review_reasons"` entry saying the source gives no name. Prefer a fuller name from elsewhere in
the text if the paper gives one anywhere.

## Every value needs a verbatim quote

For each field you emit, give `verbatim_quote`: an EXACT substring of `full.txt`. Copy it character
for character: do not paraphrase, do not fix typography, do not normalize whitespace or Greek
letters. It must support the value on its own (a clause or sentence, not a bare word).

**Do NOT compute character offsets.** The spine resolves offsets from your quote, so an inexact
copy loses the value.

**Do not shell out to inspect the store.** No `python -c`, no `grep`, no `str.find` to check your
quotes. You have already read the document. Dumping it back through a shell puts a second full
copy into this conversation, which is what makes a pass slow, expensive and liable to fail.

**If you cannot ground a value in an exact quote, omit that field.** A missing field is
recoverable; a fabricated one poisons the database. Never invent a plausible value to fill a gap,
and never infer the obvious value when the text does not state it. Its absence is a measurement.

## Output

Write JSON to `{OUT_DIR}/{WRITES}`, and nothing else:

```json
{"records": [
  {"record_id": "r1", "entity_type": "<<ENTITY_TYPE>>",
   "review_only": false,
   "fields": [
     {"field_name": "<<IDENTITY_FIELD>>", "value": "...", "verbatim_quote": "..."},
     {"field_name": "<another ratified field>", "value": "...", "verbatim_quote": "..."}
   ]}
]}
```

Number `record_id` sequentially from `r1` within this paper. If the source reports nothing that
qualifies, write `{"records": []}`: that is a result, not a failure.

Report only the number of records written when done.
