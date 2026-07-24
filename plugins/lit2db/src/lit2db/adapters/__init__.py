"""Source adapters (blueprint 3). The adapter CONTRACT is the invariant; concrete
discovery/acquisition is domain config. Both adapters converge at the Stage 4 verification
layer -- literature via span-entailment, structured via mapping validation.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Literal
from ..contracts import ProvenanceRecord


class SourceAdapter(ABC):
    """Every adapter must: (1) discover, (2) acquire legally, (3) emit provenance-anchored
    records, (4) declare the downstream path, (5) check retraction/supersession status."""
    path: Literal["full_extraction", "direct_to_verification"]

    @abstractmethod
    def discover(self, scope: dict) -> list[str]: ...
    @abstractmethod
    def acquire(self, source_id: str) -> dict: ...
    @abstractmethod
    def emit(self, acquired: dict) -> list[ProvenanceRecord]: ...
    @abstractmethod
    def check_status(self, source_id: str) -> str: ...
