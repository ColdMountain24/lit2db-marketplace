"""Stage orchestration skeleton (blueprint 2, the pipeline at a glance).

This is the domain-INVARIANT control flow. The lead/orchestrator (Opus) owns this; it
delegates per-paper extraction to Sonnet subagents (blueprint 7.1). Every stage is a stub
that calls the contracts + tools above. No domain logic lives here.
"""
from __future__ import annotations
from ..contracts import SchemaReadySpec, ExtractedRecord, default_route, RouteDecision


def stage_0_control_plane(config: dict): ...                 # plan, schema version, caps, dashboards
def stage_0_5_scope_elicitation(seed: dict) -> SchemaReadySpec: ...   # -> ratified spec
def stage_1_ingest(spec: SchemaReadySpec): ...              # via adapters
def stage_2_schema_design(spec: SchemaReadySpec): ...       # 8-step protocol -> frozen schema
def stage_3_extract(doc, schema) -> ExtractedRecord: ...    # literature path only
def stage_4_verify(rec: ExtractedRecord) -> ExtractedRecord: ...     # ensemble/ground/judge/consistency
def stage_5_cross_source(records): ...                      # resolve + classify disagreement
def stage_6_route(rec: ExtractedRecord) -> ExtractedRecord:
    for fv in rec.fields:
        fv.route = default_route(fv)
    # record-level quarantine (D1) is decided by the caller on wholesale failure
    return rec
def stage_7_output(records): ...                            # FAIR DB + ML-readiness + audit + dashboard
def stage_8_self_improve(corrections): ...                  # triaged re-optimization + A/B rollout
