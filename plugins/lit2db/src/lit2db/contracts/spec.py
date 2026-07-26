"""Schema-ready specification and ratification ledger.

Formalizes the Scope Elicitation Protocol (companion doc): the ratification ledger
(protocol section 4) and the schema-ready specification (protocol section 5) that
Stage 0.5 hands to Stage 2.

CRITICAL INVARIANT (protocol section 1, NORMATIVE): the frozen schema is EXACTLY the
set of ACCEPTED and ACCEPTED_WITH_EDIT ledger items -- nothing else. This module
enforces that structurally: `SchemaReadySpec.frozen_fields()` derives fields only
from ratified ledger entries. An agent cannot inject an unratified field.
"""
from __future__ import annotations
from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


class MLTask(str, Enum):
    classification = "classification"
    regression = "regression"
    generative = "generative"
    knowledge_graph = "knowledge_graph"


class RatificationStatus(str, Enum):
    PROPOSED = "PROPOSED"                     # agent surfaced it; NOT in schema
    ACCEPTED = "ACCEPTED"                     # researcher ratified as-is
    ACCEPTED_WITH_EDIT = "ACCEPTED_WITH_EDIT" # researcher ratified a modified form
    REJECTED = "REJECTED"                     # declined; logged, not in schema


_IN_SCHEMA = {RatificationStatus.ACCEPTED, RatificationStatus.ACCEPTED_WITH_EDIT}


class LedgerItem(BaseModel):
    """One proposed item (axis setting, candidate field, vocab binding, structural
    alternative). proposed_by is always the agent; status is set by the researcher."""
    item_id: str
    kind: Literal["axis_setting", "field", "vocab_binding", "structural_alt",
                  "inclusion_criterion", "source_scope", "threshold_constant"]
    summary: str
    proposed_by: Literal["agent"] = "agent"        # agent proposes structure only
    status: RatificationStatus = RatificationStatus.PROPOSED
    edit_note: Optional[str] = None                # required iff ACCEPTED_WITH_EDIT
    reject_reason: Optional[str] = None            # logged so it is not re-proposed
    payload: dict = Field(default_factory=dict)    # the structured content of the item

    @model_validator(mode="after")
    def _check(self):
        if self.status == RatificationStatus.ACCEPTED_WITH_EDIT and not self.edit_note:
            raise ValueError(f"{self.item_id}: ACCEPTED_WITH_EDIT requires an edit_note")
        return self

    @property
    def in_schema(self) -> bool:
        return self.status in _IN_SCHEMA


class RatificationLedger(BaseModel):
    items: list[LedgerItem] = Field(default_factory=list)

    def ratified(self) -> list[LedgerItem]:
        return [i for i in self.items if i.in_schema]

    def open_items(self) -> list[LedgerItem]:
        return [i for i in self.items if i.status == RatificationStatus.PROPOSED]


class FieldSpec(BaseModel):
    name: str
    type: str                          # e.g. "float", "str", "enum"
    unit: Optional[str] = None
    enum: Optional[list[str]] = None
    valid_range: Optional[tuple[float, float]] = None
    definition: str
    provenance_granularity: str        # what distinguishes two records (protocol axis 10)
    ledger_item_id: str                # MUST trace to a ratified ledger item


class CorpusQuery(BaseModel):
    """The executable search that DEFINES a literature corpus, recorded verbatim.

    A corpus is not named by its subject, it is defined by the query that produced it. Naming
    it ("<topic>, 2020-2025") records the intent and loses the definition: term forms, field
    scoping, and date bounds all silently move the boundary, and a corpus whose query was not
    written down cannot be reproduced, audited, or re-run for a refresh.

    `result_counts` is stored alongside because it is how you detect that a re-run drifted --
    the same query against a growing index returns more next month, and that is a fact the
    refresh cycle must be able to see rather than absorb.

    Domain-INVARIANT: `query` is an opaque string. The scaffold never parses it, never
    generates it, and never judges whether it is the right one -- that is researcher substance
    and it ratifies through the ledger like every other substantive choice.
    """
    corpus: str                                    # the adapter/source this runs against
    query: str                                     # the EXACT executable query string
    endpoint: Optional[str] = None                 # what it was executed against
    executed_at: Optional[str] = None              # ISO timestamp of the run it describes
    result_counts: dict[str, int] = Field(default_factory=dict)   # e.g. hits / retrievable
    notes: Optional[str] = None                    # e.g. known exclusions, term-form caveats
    ledger_item_id: str                            # MUST trace to a ratified ledger item


class SourceScope(BaseModel):
    adapters: list[Literal["literature", "structured"]]
    corpora: list[str] = Field(default_factory=list)
    # The executable definition behind `corpora`. Enforced for the literature adapter by
    # SchemaReadySpec below -- a named corpus with no recorded query is a corpus nobody can
    # reproduce, which is the failure this field exists to make structurally impossible.
    queries: list[CorpusQuery] = Field(default_factory=list)
    structured_databases: list[str] = Field(default_factory=list)
    field_mapping: dict[str, str] = Field(default_factory=dict)   # source_field -> schema_field
    pinned_versions: dict[str, str] = Field(default_factory=dict) # db -> version/snapshot
    retraction_recheck_cadence: str = "each_self_improve_cycle"   # ratified default (D2)


class SchemaReadySpec(BaseModel):
    """The boundary object handed from Stage 0.5 to Stage 2."""
    research_question: str
    ml_task: MLTask
    unit_of_analysis: str                       # explicit tuple, e.g. "(entity, measurement, conditions, source)"
    fields: list[FieldSpec]
    controlled_vocab_bindings: dict[str, str] = Field(default_factory=dict)
    inclusion_exclusion: dict[str, str] = Field(default_factory=dict)  # versioned
    negative_data_policy: str
    evidence_tier_dimensions: list[str] = Field(
        default_factory=lambda: ["study_design", "directness", "consistency",
                                 "risk_of_bias", "effect_direction", "certainty"])
    source_scope: SourceScope
    ledger: RatificationLedger
    spec_version: str = "v0"

    @model_validator(mode="after")
    def _every_field_ratified(self):
        ratified_ids = {i.item_id for i in self.ledger.ratified()}
        for f in self.fields:
            if f.ledger_item_id not in ratified_ids:
                raise ValueError(
                    f"field '{f.name}' traces to ledger item '{f.ledger_item_id}' "
                    f"which is not ACCEPTED/ACCEPTED_WITH_EDIT. "
                    f"The frozen schema is exactly the ratified set (protocol section 1).")
        return self

    @model_validator(mode="after")
    def _corpus_is_defined_not_just_named(self):
        """A literature corpus must carry the executable query that produced it, ratified.

        The schema half of the invariant was already enforced -- an agent cannot slip an
        unratified FIELD into a frozen spec. The corpus half was not: a spec could name
        'europepmc' and freeze, with the actual boundary of the corpus living nowhere. That
        makes the resulting database unreproducible in exactly the way the method claims it
        is not, and it hides a substantive researcher decision (which papers are in scope)
        behind a structural-looking string.
        """
        if "literature" not in self.source_scope.adapters:
            return self
        if not self.source_scope.queries:
            raise ValueError(
                "source_scope declares the literature adapter but records no CorpusQuery. "
                "A corpus is defined by the query that produced it, not by its name — "
                "record the executable query and ratify it as a 'source_scope' ledger item.")
        ratified_ids = {i.item_id for i in self.ledger.ratified()}
        for q in self.source_scope.queries:
            if q.ledger_item_id not in ratified_ids:
                raise ValueError(
                    f"corpus query for '{q.corpus}' traces to ledger item "
                    f"'{q.ledger_item_id}' which is not ACCEPTED/ACCEPTED_WITH_EDIT. "
                    f"Which papers are in scope is researcher substance and ratifies like "
                    f"any other substantive choice.")
        return self
