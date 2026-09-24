# Propose a theme set for this review

You are proposing STRUCTURE. The researcher ratifies substance, so nothing you write here enters
the review until they accept it, and a theme they did not accept cannot reach the schema at all.

## The review's question

{QUESTION}

## The claims the sample papers actually made

Each line is one claim, with the record id it is stored under.

{CLAIMS}

## What to propose

Up to {N_THEMES} themes that GROUP THE CLAIMS ABOVE. A theme is a way of reading this corpus,
not a topic you expect the field to have.

- **Every theme must cite the claim ids it was drawn from.** A theme citing none is dropped
  before the researcher sees it, because a researcher asked to approve a theme the corpus did not
  suggest is being asked to originate one while believing they are approving one.
- **Definitions decide borderline cases, so write them to be applied, not admired.** One sentence
  saying what belongs and what does not. A later reading will assign claims using only that
  sentence.
- **Themes should be disjoint enough that one claim has one obvious home.** Where two overlap,
  either merge them or say in each definition what sends a claim to the other.
- **Do not invent a theme for completeness.** If the sample says nothing about a subject, its
  absence is a finding about the corpus, and inventing a theme hides it.
- **Prefer the claims' own vocabulary** over the field's standard terms, unless the claims use
  the standard terms.

## Output

Return exactly this JSON and nothing else:

```json
{"themes": [
  {"name": "short_snake_case_name",
   "definition": "one sentence: what belongs here, and what does not",
   "example_claim_ids": ["r1", "r7"]}
]}
```
