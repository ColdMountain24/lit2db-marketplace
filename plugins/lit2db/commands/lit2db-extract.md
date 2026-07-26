---
description: Run one source end-to-end — store, k extraction passes, verify, route, gate.
argument-hint: <source-id-or-path> [--k N]
---

# Extract one source (Stages 1 → 7)

Runs `$1` through the whole spine and reports what was written and what was refused.
**A denied value is the system working, not a failure of the run.**

Read `instantiation/<project>/instantiation.yaml` first. You need `routing.ensemble_k`,
`routing.ensemble_min_agreeing`, the frozen schema, and — for Stage 3 — each entity type's
**identity field** (Stage-0.5 axis 5). If the instantiation does not name one for a type
that can have several records per paper, stop and ask: without it, records cannot be aligned
across passes and merging would compare different entities to each other.

## 1 — Store

`resolve_access` → `check_retraction` → fetch → `build_store`.

A retracted or superseded source stops here. If no legal open copy exists, add it to the
manual-acquisition queue (`rank_manual_queue`) and stop — **never reach around a paywall.**

`build_store` writes `sources/<source_id>/full.txt`, which is the coordinate system: every
offset from here on is an index into that exact file.

## 2 — k independent extraction passes

Run the `extractor-agent` **k times** (default 3) over the same store.

- Each pass must be **independent**. Do not show one pass another's output, do not summarize
  a previous pass into the next prompt, and do not run them as a conversation. Their
  disagreement is the entire signal; contaminate it and `c_ensemble` measures nothing.
- Passes may be run concurrently — they share no state by design.
- A pass that finds nothing for a field must **omit** it. An omission is a real outcome that
  lowers agreement; a guess to fill the gap destroys the measurement.

## 3 — Merge

`merge_extraction_passes(passes, identity_fields)`.

Records align on the ratified identity field, and a record a pass did not find counts as a
missing value for each of its fields — so something only 1 of 3 passes saw scores 1/3 and
cannot auto-accept. It is still emitted: a compound the other passes missed is the most
interesting thing this stage produces.

Keep the returned `ensemble` report. It is what a human needs to adjudicate a disagreement,
and it does not travel inside the records.

## 4 — Verify

Per record (not per value — D-031):

- `ground_literature` on every value against its quote → `c_grounded`.
- `verifier-judge-agent` reads the **full source** and verifies a reconstructed claim without
  seeing the extracted JSON → `c_judge`. Note the shipped judge is a different *model*, same
  family — say "different model, same family", never "cross-family".
- `contradiction-hunter-agent` searches the whole source for counter-evidence →
  `contradiction_search` + `contradictions`. Set the state explicitly: `clean` and `not_run`
  are different facts and must never be conflated.

## 5 — Route and gate

`score_and_route(record, weights_key, ensemble_k, ensemble_min_agreeing)` then `gate_upsert`.

Never hand-set a route or a confidence component. The gate is the only thing that decides,
and it fails closed.

## 6 — Report

State plainly:

- written vs refused, **with the reason for each refusal**;
- every value where passes disagreed, with the candidates from the `ensemble` report;
- anything that could not be read, and why (paywalled, retracted, no OA copy);
- tokens consumed (`accounting.py`), never a dollar figure.

Do not summarize a refusal as a problem to fix. A value that grounds perfectly and is still
refused — because the judge dissented, the source was retracted, or the passes disagreed — is
this system's entire thesis in one line.
