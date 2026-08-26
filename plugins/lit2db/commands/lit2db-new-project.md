---
description: Start a new lit2db project — copy the instantiation template and launch Stage-0.5 scope elicitation.
argument-hint: [project-name]
---
Start a new domain instantiation of the lit2db pipeline.

The scaffold in `src/lit2db/` is **domain-blind**. All domain substance lives under
`instantiation/<project>/` and must trace to a ratified ratification-ledger item.

Do this:
1. Create `instantiation/$1/` by copying `instantiation/_TEMPLATE/instantiation.yaml`.
2. Launch the **scope-elicitation-agent** to run the Stage-0.5 interview (Steps A–E over the
   ten narrowing axes). The agent PROPOSES structure; it must not originate domain substance.
3. Every proposed item enters the ratification ledger as `PROPOSED`. **Stop and wait for the
   researcher** to move each to ACCEPTED / ACCEPTED_WITH_EDIT / REJECTED. Do not freeze a
   schema until every field traces to a ratified item.
4. Once they are ratified, launch the **schema-architect-agent** to run the eight-step Schema
   Design Protocol over the ratified spec and freeze it: minimal-sufficient v0, a controlled
   vocabulary and canonical unit bound to every field, enum-conditional unit rules, mandatory
   provenance fields, the six-dimension evidence tier, and a versioned citable constant for any
   derived threshold. It operationalizes what was ratified and adds nothing — a field it cannot
   trace to an ACCEPTED ledger item is a build error, never a call it gets to make. In the
   self-improve loop it is the same agent that versions a schema change; a frozen schema is
   never mutated in place.

## Arriving from `/lit2db-start`

`/lit2db-start` hands off here with an intake already collected. When it does, **run a
confirmation pass, not a second questionnaire** — a researcher asked the same thing twice in
different words concludes the tool was not listening, and starts clicking through.

Two kinds of item arrive and they are NOT interchangeable:

- **Stated by the researcher** — their words for the row, the fields, the literature, the
  exclusions. Put their phrasing in front of them and confirm it. One pass, quick.
- **Derived by the agent** — the query, identity chain, negative-data policy, evidence tiers,
  provenance granularity. The researcher has never seen these. Each gets the full
  propose-and-ratify treatment, and each is labelled as the agent's proposal when shown.

**Never present a derived item as though the researcher had already agreed to it.** That is
precisely how two fields once froze into the BBB schema citing ledger items that did not exist,
under a note claiming they were researcher-ratified. The scaffold was correct both times; the
work was routed around it.

**No axis is skipped because the intake "probably covers it."** Speed comes from confirming a
concrete proposal, never from asking less. If the intake leaves an axis untouched, say so and
walk it properly.

The hard boundary (NORMATIVE): you MAY enumerate axes, surface existing vocabularies, derive
candidate fields from the researcher's declared ML task + unit of analysis, and propose
structural alternatives. You MUST NOT invent the research question, decide what is
scientifically interesting, or introduce any entity/property the researcher did not ratify.
