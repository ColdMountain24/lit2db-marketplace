"""Custom-tool stubs (blueprint 7.3). Correct signatures + contracts; the bodies are
domain-invariant plumbing left as NotImplementedError where an external service or a
domain choice is required. These are the in-process tools the Claude Code agents call.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from ..contracts import (
    LiteratureProvenance, StructuredProvenance, ExtractedRecord, FieldValue,
    SourceStatus,
)


def grobid_parse(pdf_path: str) -> dict:
    """Parse a born-digital PDF main body via GROBID (blueprint 3, Stage 1).
    Returns an offset-anchored contextual store: text chunks + tables + figure captions
    with section labels and char offsets. VLM/Nougat path handled separately for
    math-heavy / SI-table pages."""
    raise NotImplementedError("wire to a GROBID service endpoint")


def query_structured_source(db: str, query: str, version: str) -> list[StructuredProvenance]:
    """Query a structured registry pinned to `version` (blueprint 3, structured adapter).
    Records skip Stage 3 and enter at the Stage 4 mapping-validation checkpoint."""
    raise NotImplementedError("wire to the structured-source API under its access terms")


def check_retraction(source_id: str, kind: str) -> SourceStatus:
    """Retraction/supersession check (blueprint 3, ratified addition D2).
    Literature: Crossref is-retracted + Retraction Watch. Structured: version supersession."""
    raise NotImplementedError("wire to Crossref / Retraction Watch")


def retrieve_spans(query: str, doc_store: dict, k: int = 8) -> list[dict]:
    """Retrieval-augmented span retrieval over a parsed doc (blueprint Stage 3)."""
    raise NotImplementedError("wire to the project retriever / embedding index")


def extract_record(schema: dict, context: str, entity_type: str) -> ExtractedRecord:
    """Citation-grounded extraction into a Pydantic-validated record (blueprint Stage 3).
    Every value must carry a verbatim quote + offset. Three classification steps precede
    value extraction: modality (measured vs predicted), entity-type routing, derived-field
    threshold application with the versioned constant."""
    raise NotImplementedError("implement via structured-output / tool-calling extractor")


def nli_entails(claim: str, span: str) -> float:
    """Span-entailment grounding for literature (blueprint 5.1 4b). Returns entailment
    score in [0,1] (ALCE-style citation precision/recall)."""
    raise NotImplementedError("wire to an NLI/AutoAIS entailment model")


def validate_mapping(value: object, source_field: dict, schema_field: dict) -> bool:
    """Mapping validation for structured data (blueprint 5.1 4b): type/range/enum
    conformance of the mapped value + confirm source version is pinned."""
    raise NotImplementedError("implement type/range/enum conformance check")


def resolve_entity(record: ExtractedRecord, canonical_index: dict) -> str:
    """Entity resolution / record linkage (blueprint Stage 5). Returns a canonical entity
    id. Method is entity-type specific (structure key vs. name vs. accession)."""
    raise NotImplementedError("wire to LinkTransformer / InChIKey / SMILES canonicalization")


def db_upsert(record: ExtractedRecord) -> None:
    """Write a record to the output DB (blueprint Stage 7). The PreToolUse write-gate hook
    denies this call if composite confidence is below the auto-accept threshold."""
    raise NotImplementedError("wire to the storage backend (Postgres default)")
