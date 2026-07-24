"""Structured-data adapter (blueprint 3). Grounding mode: mapping validation.
Bypasses Stage 3; enters at the Stage 4 checkpoint."""
from __future__ import annotations
from .import SourceAdapter


class StructuredDataAdapter(SourceAdapter):
    path = "direct_to_verification"

    def discover(self, scope):
        raise NotImplementedError("enumerate records for the declared query + version")

    def acquire(self, source_id):
        # version-pinned retrieval under the source's access terms
        raise NotImplementedError("wire the structured-source API")

    def emit(self, acquired):
        # declared, versioned field mapping -> StructuredProvenance (db, record_id, db_version)
        raise NotImplementedError("apply the ratified field mapping")

    def check_status(self, source_id):
        # version supersession / withdrawal flags
        raise NotImplementedError("wire supersession check")
