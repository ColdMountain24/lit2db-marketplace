---
name: scope-elicitation
description: Run the lit2db Stage-0.5 Scope Elicitation Protocol — narrow a research question into a frozen, ratified schema before any extraction. Use when a researcher wants to build a structured database from literature or structured data and has not yet defined the schema, fields, unit of analysis, or inclusion criteria. The agent proposes structure; the researcher ratifies all substance.
---

# Scope Elicitation (Stage 0.5)

The pre-schema narrowing protocol. It converts a research question into a **schema-ready
spec** whose every field traces to a researcher-ratified ledger item. Nothing is extracted
until this completes. This skill is domain-invariant — it carries no domain content.

## The one hard boundary (NORMATIVE)

**The agent proposes STRUCTURE; the researcher originates and ratifies SUBSTANCE.**

Litmus test: if removing a contribution changes *what the database is about*, it was
origination — forbidden. If it only changes *how cleanly the database is structured*, it was
formalization — permitted, subject to ratification.

- **You MAY:** enumerate the ten narrowing axes; surface existing controlled
  vocabularies / ontologies / units for the researcher to ratify; derive candidate fields that
  logically follow from the declared ML task, unit of analysis, and outcome; detect ambiguity,
  gaps, contradiction; propose structural alternatives *with their consequences*.
- **You MUST NOT:** invent or alter the research question; decide what is scientifically
  interesting; introduce any entity / property / relation the researcher did not name; generate
  domain claims, candidate findings, or expected values; unilaterally resolve a substantive
  scope decision — surface the options and WAIT.

## Up-front driver: the ML task

Resolve the **ML task first** (classification / regression / generative / knowledge_graph).
It drives schema shape, the negative-data policy, and the verification strategy downstream.
Knowing it before schema design prevents costly rework.

## The ten narrowing axes

Every database question must resolve all ten. Walk them in the interview (Steps A–E):

1. **Entity class** — what is the row about? (one row = one …)
2. **Target / outcome** — the ML label or measured quantity, with units.
3. **Evidence-tier dimensions** — study design, directness, consistency, risk of bias,
   effect direction, certainty. Measured and predicted values are NEVER pooled.
4. **Measurement type & modality** — measured vs. computed vs. simulated; which are admissible.
5. **Authoritative identity** — the canonical id that resolves two records to the same entity
   (structure key / accession / name-normalization).
6. **Condition features** — the covariates that make a measurement comparable (pH, temp, etc.).
7. **Negative-data policy** — driven by the ML task; classification needs negatives.
8. **Inclusion / exclusion criteria** — versioned, reproducible.
9. **Source scope** — literature adapter, structured adapter, or both; pinned DB versions.
10. **Provenance granularity** — what distinguishes two records (the unit-of-analysis tuple).

## The ratification ledger

Every item you propose enters the ledger as `PROPOSED`. Only the researcher moves it to
`ACCEPTED`, `ACCEPTED_WITH_EDIT` (requires an edit note), or `REJECTED` (reason logged so it
is not re-proposed). The **frozen schema is EXACTLY the ACCEPTED + ACCEPTED_WITH_EDIT set** —
enforced structurally by `SchemaReadySpec` in `src/lit2db/contracts/spec.py`: it refuses to
build if any field traces to an unratified item.

## Output

After Steps A–E are complete and every candidate field traces to a ratified ledger item, emit
a `SchemaReadySpec` (JSON) into `instantiation/<project>/`. Set `spec_version` and freeze.
Then — and only then — Stage 1 ingest may begin.

## Worked grounding

Two concrete instantiations exist as reference (in the project history, not baked here): a
**BBB-permeability binary classifier** and an **enzyme-kinetics regression** (Km / kcat /
kcat·Km). Use them to illustrate the axes to a researcher — never to import their substance
into a new project.
