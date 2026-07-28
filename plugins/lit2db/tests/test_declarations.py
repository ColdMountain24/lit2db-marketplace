"""THE PLUGIN MAY NOT CLAIM WHAT IT DOES NOT DO.

This file exists because of a root-cause review, not a bug report. Across its first weeks this
project shipped the same defect twelve times, in twelve places, and never recognised it as one
defect:

  * an agent declaring `tools:` it did not hold — silently dropped, leaving an agent that could
    not do its job
  * weights for three confidence signals nothing produced — 0.35 of the declared mass, inert
  * `stages/` announcing itself as "the domain-INVARIANT control flow" with eight empty bodies
  * a corpus that was a name with no query
  * schema fields marked "researcher-ratified" against ledger items that did not exist
  * `judge_prompt=1200` declared in a cost model and never used
  * a token headline declared as work that was 92% cache
  * a stage recorded as "found nothing" that had never run
  * the adversarial judge weighted 0.15 inside a mean while behaving as a veto
  * an audit slice reporting three records having judged two
  * `record_id TEXT PRIMARY KEY` over ids that are not unique
  * "literature and structured data" over an unimplemented structured ingest path

One shape: **a declaration not backed by the thing it names.** Every instance was found by hand,
usually by running something and noticing a number that could not be right.

lit2db exists to stop an extractor asserting what its source does not support. The gate made that
mechanical rather than advisory, and it is the reason the project trusts its own database. Nothing
did the same job for the plugin's claims about itself — so this does. A green suite here is not
proof the code is correct; it is proof the code is not lying about what it is.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "lit2db"
sys.path.insert(0, str(ROOT / "src"))

from lit2db.contracts.routing import DEFAULT_WEIGHTS, UNPRODUCED_SIGNALS  # noqa: E402


def _py_files(root: pathlib.Path) -> list:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _all_source_text() -> str:
    parts = [p.read_text(encoding="utf-8") for p in _py_files(SRC)]
    parts += [p.read_text(encoding="utf-8") for p in _py_files(ROOT / "scripts")]
    parts += [p.read_text(encoding="utf-8") for p in _py_files(ROOT / "mcp")]
    parts += [p.read_text(encoding="utf-8") for p in _py_files(ROOT / "hooks")]
    return "\n".join(parts)


# --- 1. every module is reachable -------------------------------------------------------
def test_no_library_module_is_unreachable():
    """A module nothing imports cannot be wrong, cannot be tested, and cannot be trusted.

    `stages/`, `tools/` and `adapters/` were unreachable for the plugin's entire published life
    while `README` and `CODEMAP` presented them as the architecture. Deleting all three broke
    exactly zero tests, which is the proof they were never part of the system.
    """
    modules = {p.relative_to(SRC).with_suffix("").as_posix().replace("/__init__", "")
               for p in _py_files(SRC)}
    modules.discard("__init__")
    text = _all_source_text()

    unreachable = []
    for m in sorted(modules):
        leaf = m.split("/")[-1]
        # any import form: `from .x import`, `from lit2db.x import`, `from ..x import`,
        # `import lit2db.x`, or a subpackage referenced as `contracts.x`
        pattern = rf"(from\s+\.*[\w.]*\b{re.escape(leaf)}\b\s+import|import\s+[\w.]*\b{re.escape(leaf)}\b)"
        if not re.search(pattern, text):
            unreachable.append(m)
    assert not unreachable, (
        f"unreachable module(s) {unreachable} — nothing imports them. Either wire them into a "
        f"running path or delete them; a module that only exists to describe the design is the "
        f"defect this file exists to prevent.")


# --- 2. nothing the docs call working raises NotImplementedError -------------------------
def test_no_unimplemented_code_ships_in_the_library():
    """`NotImplementedError` in the shipped library is a promise with nothing behind it.

    There were 18 of them across `tools/` and `adapters/`, presented in the README as 'typed
    scaffolding' on the argument that the contract IS the design. The contract is not the design
    if nothing calls it.
    """
    offenders = [str(p.relative_to(ROOT)) for p in _py_files(SRC)
                 if "NotImplementedError" in p.read_text(encoding="utf-8")]
    assert not offenders, (
        f"{offenders} raise NotImplementedError. Implement it, or delete it and correct any "
        f"document that describes it as shipped.")


def test_no_function_in_the_library_has_an_empty_body():
    """`def stage_3_extract(doc, schema) -> ExtractedRecord: ...` — nine of these described the
    pipeline while the pipeline ran somewhere else entirely."""
    empty = []
    for p in _py_files(SRC):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = [b for b in node.body if not isinstance(b, ast.Expr)
                    or not isinstance(b.value, ast.Constant)]
            if not body:                       # only a docstring / bare `...`
                empty.append(f"{p.relative_to(ROOT)}::{node.name}")
    assert not empty, (
        f"empty function body/bodies {empty} — a named, typed function with no body reads as "
        f"implemented architecture to every reader, including its author six weeks later.")


# --- 3. every weight names a signal something actually produces --------------------------
def test_no_weight_is_declared_for_a_signal_nothing_produces():
    """Measured across 86 records / 670 scored fields: `c_verbal`, `c_consistency` and
    `c_logprob` fired on NONE, while carrying 0.35 of the declared weight mass — including the
    second-largest weight in the profile. Renormalization over present signals meant the
    composite still looked right, which is why it survived unnoticed.

    A weight may be added back only together with the code that populates its signal. That order
    is the point: the reverse order is how this got shipped.
    """
    for profile, weights in DEFAULT_WEIGHTS.items():
        bad = sorted(set(weights) & set(UNPRODUCED_SIGNALS))
        assert not bad, (
            f"profile '{profile}' weights {bad}, which no code path sets. Produce the signal in "
            f"the same change that weights it, or leave it out of the profile.")


def test_every_weighted_signal_is_set_somewhere():
    """The general form of the rule above: the weight vector may not outrun the pipeline."""
    text = _all_source_text()
    for profile, weights in DEFAULT_WEIGHTS.items():
        for signal in weights:
            assert re.search(rf'["\']?{re.escape(signal)}["\']?\s*[=:]', text), (
                f"profile '{profile}' weights '{signal}' but nothing in the plugin assigns it")


def test_the_judge_is_never_a_scored_term():
    """D-079, kept mechanical rather than remembered — see `ConfidenceComponents.composite`."""
    for profile, weights in DEFAULT_WEIGHTS.items():
        assert "c_judge" not in weights, f"profile '{profile}' scores the judge; it is a veto"


# --- 4. every MCP tool is reachable from something a user can invoke ---------------------
def _declared_mcp_tools() -> list:
    src = (ROOT / "mcp" / "lit2db_mcp" / "server.py").read_text(encoding="utf-8")
    return re.findall(r"@mcp\.tool\(\)\s*\ndef\s+(\w+)", src)


def test_every_mcp_tool_is_reachable_from_a_command_or_agent_or_script():
    """A tool nobody can reach is a claim in a feature list.

    The reverse of this rule already has its own test (`test_agent_contracts`: no agent may name
    a tool it does not hold), added after three agent contracts were found directing calls to
    tools they had never been given. This is the other direction, and it went unchecked.
    """
    reachable = "\n".join(
        p.read_text(encoding="utf-8")
        for d in ("commands", "agents", "skills", "scripts", "hooks")
        for p in sorted((ROOT / d).rglob("*")) if p.is_file() and p.suffix in (".md", ".py"))
    orphans = [t for t in _declared_mcp_tools() if t not in reachable]
    assert not orphans, (
        f"MCP tool(s) {orphans} are exposed but unreachable from any command, agent, hook or "
        f"script. Wire them up or stop shipping them.")


def test_the_tool_list_in_the_server_docstring_matches_the_tools_it_exposes():
    """The server's module docstring enumerates its tools. A list maintained by hand beside the
    thing it lists is a list that drifts."""
    doc = (ROOT / "mcp" / "lit2db_mcp" / "server.py").read_text(encoding="utf-8").split('"""')[1]
    documented = set(re.findall(r"^\s+-\s+(\w+)", doc, re.M))
    exposed = set(_declared_mcp_tools())
    assert not (documented - exposed), f"documented but not exposed: {sorted(documented - exposed)}"


# --- 5. the plugin's own metadata is true ------------------------------------------------
def test_the_manifest_does_not_promise_an_unimplemented_source_kind():
    """The description said "from scientific literature and structured data" for the plugin's
    whole published life. Structured GROUNDING exists (`validate_mapping`); structured INGEST
    never has — there is no adapter that pulls records out of a structured database. Half a
    claim in the one string a marketplace visitor reads first.
    """
    desc = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["description"]
    assert "structured data" not in desc.lower(), (
        "the manifest promises structured-data sources; no structured ingest path exists")


def test_readme_does_not_advertise_deleted_scaffolding():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for gone in ("src/lit2db/stages/", "src/lit2db/adapters/", "src/lit2db/tools/"):
        # a historical mention is fine; presenting it as a shipped component is not
        assert f"| `{gone}` |" not in readme, f"README still lists {gone} as a component"


# --- 6. ids the database keys on are unique ----------------------------------------------
def test_a_colliding_record_id_cannot_silently_replace_a_row(tmp_path):
    """`record_id TEXT PRIMARY KEY` + `INSERT OR REPLACE` over ids that are NOT unique.

    Measured: `merge_passes` returned 15 records under 11 ids on PMC10325987, and ids are
    per-source ordinals, so `ts6` exists in more than one paper. Two colliding records that both
    cleared the gate would have overwritten each other with no error and nothing in any artifact.
    It never fired only because every paper carrying duplicates wrote zero records.
    """
    from lit2db.output import upsert

    db = str(tmp_path / "t.db")
    prov = {"kind": "literature", "source_id": "S1", "producing_process": "p@1",
            "retrieval_timestamp": "2026-07-19T00:00:00Z", "source_status": "active",
            "verbatim_quote": "the value is v", "char_offset": 0}
    base = {"record_id": "ts1", "entity_type": "e", "judge_verdict": "supported",
            "fields": [{"field_name": "f", "value": "v", "provenance": prov,
                        "route": "auto_accept", "contradiction_search": "clean"}]}
    assert upsert(base, 1.0, db, autoaccept=0.95)["written"] is True
    # the SAME record again is idempotent — a re-run must not be a collision
    assert upsert(base, 1.0, db, autoaccept=0.95)["written"] is True

    other = json.loads(json.dumps(base))
    other["fields"][0]["value"] = "a completely different value"
    res = upsert(other, 1.0, db, autoaccept=0.95)
    assert res["written"] is False, "a different record under the same id must not overwrite"
    assert any("already held by a DIFFERENT record" in r for r in res["reasons"])


# --- 7. the guard rails cannot be quietly removed -----------------------------------------
def test_this_file_is_wired_into_the_suite():
    """A declaration audit that nobody runs is itself an unbacked declaration."""
    assert __file__.endswith("test_declarations.py")
    assert (ROOT / "tests" / "test_declarations.py").exists()


# --- 8. a contract field nobody reads is a declaration too --------------------------------
# `FieldSpec.required` defaulted to True, was inherited silently by six of eleven terpenoid
# fields, and was read by NOTHING for the plugin's entire published life. `EvidenceTier` was
# declared on every value and never populated. `is_inferential` outlived the rule it selected.
# Each is the same defect wearing a Pydantic annotation, and none of the checks above would
# have caught them — so this is the generalized form.
#
# A field may be declared and unread only if it is listed here WITH a reason. The list is the
# point: an exception somebody had to write down is a different thing from one nobody noticed.
# Three categories, each with a stated reason. Writing 29 exceptions down is not a weakening —
# an exception somebody had to justify is a different object from one nobody noticed, and the
# inventory is what makes the next one visible.
#
# 1. SPEC CONTENT. Researcher-authored substance consumed by extraction prompts and by humans
#    reading the frozen spec, never branched on in Python. `research_question` is not supposed to
#    drive an `if`; it is supposed to appear in a prompt and in a datasheet.
SPEC_CONTENT = {
    "SchemaReadySpec", "CorpusQuery", "SourceScope", "LedgerItem", "RatificationLedger",
    "MLTask", "RatificationStatus",
}
# 2. STRUCTURED-ADAPTER FIELDS, unread because the structured INGEST path does not exist. This
#    is a flagged gap, not a benign exemption — if that path is ever built these become live, and
#    if it never is, these should go with it. Kept visible on purpose.
STRUCTURED_PENDING = {"StructuredProvenance"}
# 3. GENUINELY INERT behaviour flags — recorded, deliberately not acted on.
RECORDED_NOT_ACTED_ON = {
    "EvidenceTier": "a project may populate it from its own extraction prompt; the shipped "
                    "pipeline does not, and its docstring says so",
    "is_inferential": "a per-value label the judge prompt and the reviewer read; it stopped "
                      "selecting a stricter judge bar when D-079 made the veto uniform",
    "judge_note": "carried to the reviewer through the gate's reasons, not branched on",
    "edit_note": "ledger bookkeeping, read by a human reviewing ratification history",
    "definition": "the field's meaning, injected into the extraction prompt",
    "provenance_granularity": "what distinguishes two records; read by the schema architect",
    "source_status_checked_at": "recorded so a reader knows how stale the retraction check is",
}


def test_no_contract_field_is_declared_and_never_read():
    """Every field on a BEHAVIOURAL contract is used, or listed as deliberately inert.

    `FieldSpec.required` defaulted to True, was inherited silently by six of eleven terpenoid
    fields, and was read by NOTHING for the plugin's entire published life — a flag that implied
    enforcement and had none. That is the category this catches: a field whose existence implies
    behaviour. Fields that are merely CONTENT are exempted by model above, with a reason.
    """
    import lit2db.contracts as C

    text = _all_source_text()
    unread = []
    for model_name in dir(C):
        model = getattr(C, model_name)
        if not (isinstance(model, type) and hasattr(model, "model_fields")):
            continue
        if model_name in SPEC_CONTENT | STRUCTURED_PENDING | set(RECORDED_NOT_ACTED_ON):
            continue
        for fname in model.model_fields:
            if fname in RECORDED_NOT_ACTED_ON:
                continue
            if re.search(rf'(\.{re.escape(fname)}\b|["\']{re.escape(fname)}["\']|\b{re.escape(fname)}\s*=)',
                         text):
                continue
            unread.append(f"{model_name}.{fname}")
    assert not unread, (
        f"contract field(s) {sorted(set(unread))} imply behaviour and get none. Use them, delete "
        f"them, or list them above with the reason they are inert.")


def test_the_structured_exemption_names_a_gap_we_have_admitted():
    """`StructuredProvenance` is exempt because the structured ingest path is unimplemented —
    the same fact the README and manifest were corrected to state. If one is ever fixed without
    the other, this is where the inconsistency surfaces."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "STRUCTURED_PENDING" not in readme
    assert "not implemented" in readme.lower(), (
        "the structured-ingest gap is exempted in the audit but no longer admitted in the README")


# --- 9. the two tiers stay two tiers ------------------------------------------------------
def test_the_candidate_pool_cannot_reach_the_ml_ready_view(tmp_path):
    """The large database and the high-quality one are separate TABLES, not one table with a
    status column — because a shipped BBB database was once found holding 18
    rejected-but-present records, which is what a flag everyone must remember to filter buys you.
    """
    from lit2db.output import query, record_candidate, review_queue

    db = str(tmp_path / "t.db")
    prov = {"kind": "literature", "source_id": "S1", "producing_process": "p@1",
            "retrieval_timestamp": "2026-07-19T00:00:00Z", "source_status": "active",
            "verbatim_quote": "the value is v", "char_offset": 0}
    rec = {"record_id": "ts1", "entity_type": "e", "judge_verdict": "unsupported",
           "fields": [{"field_name": "f", "value": "v", "provenance": prov}]}
    record_candidate(rec, 0.2, {"decision": "deny", "reasons": ["struck out"]}, db, "PMC1")

    assert query(db)["n"] == 0, "a candidate must never appear in the ML-ready view"
    q = review_queue(db)
    assert q["candidates_total"] == 1 and q["ml_ready_total"] == 0
    assert q["queue"][0]["reasons"] == ["struck out"], "the reviewer sees why it stopped"


def test_record_candidate_is_not_a_gated_write_tool():
    """It must not be in WRITE_TOOLS: the PreToolUse hook would otherwise deny writes to a pool
    that is explicitly ungated, and the candidate half of the product would never fill."""
    from lit2db.gate import is_write_tool

    assert not is_write_tool("mcp__lit2db__record_candidate")
    assert is_write_tool("mcp__lit2db__gate_upsert"), "the gated one still is"


def test_candidates_are_keyed_by_source_because_ids_are_per_source(tmp_path):
    """`ts6` exists in more than one paper. The ML-ready table refuses the collision loudly;
    the candidate pool simply keys on the pair, since it makes no uniqueness claim."""
    from lit2db.output import record_candidate, review_queue

    db = str(tmp_path / "t.db")
    rec = {"record_id": "ts6", "entity_type": "e", "judge_verdict": "not_run", "fields": []}
    record_candidate(rec, 0.9, {"decision": "deny", "reasons": []}, db, "PMC1")
    record_candidate(rec, 0.9, {"decision": "deny", "reasons": []}, db, "PMC2")
    assert review_queue(db)["candidates_total"] == 2, "same id, different papers, both kept"


# --- 10. optional by default --------------------------------------------------------------
def test_a_field_is_optional_unless_the_researcher_locks_it():
    """The product is a large candidate pool plus a smaller high-quality table. Completeness is
    not the bar: a record stating four things it can evidence beats one asserting nine it cannot.
    """
    from lit2db.contracts.spec import FieldSpec

    assert FieldSpec.model_fields["required"].default is False, (
        "fields must be optional by default; required-ness is something a researcher ratifies")


def test_absence_of_an_unlocked_field_costs_a_record_nothing():
    from lit2db.gate import gate_reasons

    rec = {"record_id": "r", "entity_type": "e", "judge_verdict": "supported",
           "fields": [{"field_name": "present_one", "value": 1,
                       "provenance": {"source_status": "active"},
                       "route": "auto_accept", "contradiction_search": "clean"}]}
    assert gate_reasons(rec, 1.0, 0.95, require_contradiction_search=True) == []


def test_a_locked_field_that_is_absent_holds_the_record_back():
    """The other half: when the researcher HAS locked a field, absence is a real denial —
    and it comes with a reason, so the record is actionable in the queue rather than lost."""
    from lit2db.gate import gate_reasons

    rec = {"record_id": "r", "entity_type": "e", "judge_verdict": "supported",
           "fields": [{"field_name": "present_one", "value": 1,
                       "provenance": {"source_status": "active"},
                       "route": "auto_accept", "contradiction_search": "clean"}]}
    reasons = gate_reasons(rec, 1.0, 0.95, require_contradiction_search=True,
                           required_fields=("accession",))
    assert reasons == ["locked field 'accession' is absent"]
