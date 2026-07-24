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

The hard boundary (NORMATIVE): you MAY enumerate axes, surface existing vocabularies, derive
candidate fields from the researcher's declared ML task + unit of analysis, and propose
structural alternatives. You MUST NOT invent the research question, decide what is
scientifically interesting, or introduce any entity/property the researcher did not ratify.
