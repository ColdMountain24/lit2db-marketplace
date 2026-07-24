"""Literature adapter (blueprint 3). Grounding mode: span-entailment. Routes to Stage 3."""
from __future__ import annotations
from .import SourceAdapter


class LiteratureAdapter(SourceAdapter):
    path = "full_extraction"

    def discover(self, scope):
        # OpenAlex / Semantic Scholar / PubMed + domain registries
        raise NotImplementedError("wire bibliographic discovery")

    def acquire(self, source_id):
        # OA-resolve via Unpaywall/CORE BEFORE parse; non-OA -> manual-acquisition queue
        raise NotImplementedError("wire OA resolution + GROBID/Nougat parse")

    def emit(self, acquired):
        # offset-anchored records with LiteratureProvenance (doi, section, quote, offset)
        raise NotImplementedError("emit offset-anchored provenance records")

    def check_status(self, source_id):
        # Crossref is-retracted + Retraction Watch (blueprint 3, D2)
        raise NotImplementedError("wire retraction check")
