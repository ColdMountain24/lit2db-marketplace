# Example instantiations

Real, worked **schema/ledger artifacts** from three lit2db pilot projects. These
show what the Stage-0.5 elicitation interview produces: a ratified, frozen
schema-ready spec whose every field traces to a researcher-approved ledger item.

| Project | Files | State |
|---|---|---|
| `enzyme/`  | `schema_ready_spec_enzyme_FROZEN.json` | Frozen (regression pilot #2) |
| `bandgap/` | `schema_ready_spec_bandgap_PROPOSED.json` | Proposed (pilot #3, pre-freeze) |
| `bbb/`     | `schema_ready_spec_FROZEN.json`, `ratification_ledger.json` | Frozen (pilot #1) |

## What these are — and are NOT

These are **ratified-schema artifacts only**: field definitions, unit bindings,
controlled vocabularies, ML-task declarations, and (for BBB) the ratification
ledger recording which domain decisions the researcher approved. They contain
**no redistributed source text** — no paper abstracts, no `evidence_quote`
excerpts, no extracted data rows.

The full extracted datasets (with per-record provenance and verbatim source
quotes) are deliberately not shipped here: they carry source-copyright obligations
that are resolved per project, not by the plugin. This mirrors the core lit2db
invariant — the scaffold is domain-blind; domain substance lives only in a
project's own instantiation.

Use these as reference when running `/lit2db-new-project <your-domain>`.
