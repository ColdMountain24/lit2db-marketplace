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
3. **Adversarial judge (do NOT skip):** hand each grounded value to the
   **verifier-judge-agent** (a DIFFERENT model family). A naive grounding pass ~always
   succeeds; the judge is where surface-grounded-but-wrong values get caught. Record its
   verdict as `c_judge`.
4. `score_and_route` — composite confidence + per-field + record-level routing.
5. `gate_upsert` — the HARD write-gate. It writes only if the record clears auto-accept, no
   field is quarantined/human_review, and every source_status is active. A denied record goes
   to the human-review/quarantine queue, never silently to the DB.

Report the routing summary and the gate decision with reasons.
