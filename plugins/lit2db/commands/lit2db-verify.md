---
description: Run a single extracted record through the deterministic verify → route → gate spine.
argument-hint: [record-json-path]
---
Run the record at `$1` (an ExtractedRecord JSON) through the full deterministic spine using
the **lit2db MCP server**. This is the part of the architecture that wraps the
non-deterministic extractor — the verification layer is the centerpiece.

Steps (call the MCP tools in order):
1. `validate_record` — Pydantic shape check. If it fails, the record is quarantined; stop.
2. Grounding (Stage 4b):
   - literature values → `ground_literature(value, quote)` for each field's grounding quote;
   - structured values → `validate_mapping(value, field_spec)`.
   Feed each result back into the field's `confidence_components.c_grounded`.
3. `score_and_route` — SELECTION: grounding + cross-pass agreement, per-field and record-level
   routing. The judge is deliberately not a term here (D-079).
4. **Adversarial judge (do NOT skip):** hand the record to the **verifier-judge-agent** (a
   DIFFERENT model — by default the same family, D-041). A naive grounding pass ~always
   succeeds; the judge is where surface-grounded-but-wrong values get caught. Record its answer
   as `judge_verdict` on the RECORD — one of `supported` / `partial` / `unsupported` — plus a
   one-line `judge_note`. It is a **veto**: only `supported` clears, and a record you did not
   judge stays `not_run`, which blocks. When writing up results, say "different model, same
   family" and report the gap as a lower bound — never "cross-family".
5. `gate_upsert` — the HARD write-gate. It writes only if the record clears auto-accept, no
   field is quarantined/human_review, every source_status is active, and the judge cleared it.
   A denied record goes to the human-review/quarantine queue, never silently to the DB.

Judging one record costs one read, so verify in that order for a single record. In a WAVE the
order is what makes the cost bearable: `scripts/run_wave.py` scores first and judges only what
survives selection, plus a ratified random audit slice of what did not.

Report the routing summary and the gate decision with reasons.
