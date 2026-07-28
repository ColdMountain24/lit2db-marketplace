"""lit2db MCP server — the DETERMINISTIC pipeline spine.

This server is the domain-invariant, non-negotiable half of the architecture: the
verification, routing, and hard write-gate machinery that wraps the (non-deterministic)
LLM extractor. The LLM *proposes* extracted values with grounding quotes; this server
*verifies, scores, routes, and gates* them into an auditable SQLite store. No domain
substance lives here — thresholds and weights come from the ratified instantiation.

Tools exposed (one per pipeline responsibility):
  - validate_record     Stage 3/4a  Pydantic-validate an ExtractedRecord's shape.
  - ground_literature   Stage 4b    Deterministic span-grounding: does each value
                                     actually appear (lexically / numerically) in its quote?
  - build_store         Stage 1     JATS XML -> the offset-anchored store on disk.
  - locate_spans        Stage 1     Exact CHARACTER offsets for a string in a store.
  - validate_mapping    Stage 4b    Structured-adapter grounding: type/range/enum conformance.
  - merge_extraction_passes
                        Stage 3     Align k passes on the ratified identity field and
                                     merge them into one record set with ensemble signals.
  - aggregate_ensemble  Stage 3     k extraction passes -> c_ensemble + c_consistency,
                                     compared under a stated normalization (never by an LLM).
  - score_and_route     Stage 5/6   Composite confidence + per-field + record-level routing.
  - gate_upsert         Stage 7     The HARD write-gate: write iff it clears auto-accept,
                                     no field is quarantined/human_review, source is active.
  - db_query            Stage 7     Read the ML-ready view (auto-accepted, non-retracted).

Grounding here is deliberately the *naive lexical/numeric* check — it is the baseline the
project empirically showed passes ~100% while true factual precision is far lower. The
ADVERSARIAL JUDGE is the orchestrator's job (verifier-judge-agent, a different model — by
default the same family, per D-041; a different provider is opt-in per D-025). Its verdict is
fed back as the c_judge component. Separation is the point:
the server never adjudicates meaning, only mechanical conformance.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Make the ported lit2db contracts importable regardless of install state.
_PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parents[2]))
_SRC = _PLUGIN_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lit2db.contracts import (  # noqa: E402
    ExtractedRecord, FieldValue, RouteDecision, FailureReason,
    ConfidenceComponents, default_route, DEFAULT_WEIGHTS, required_agreement,
)
from lit2db.contracts.spec import SchemaReadySpec  # noqa: E402
from lit2db.ensemble import DEFAULT_STEPS, merge_passes, summarize  # noqa: E402
from lit2db.store import (  # noqa: E402
    build_from_jats, find_spans, quote_at, section_of, write_store,
)
from lit2db.gate import gate_reasons, resolve_threshold  # noqa: E402
from lit2db.dedup import dedupe as _dedupe  # noqa: E402
from lit2db.entity import build_index as _build_index, explain as _explain  # noqa: E402
from lit2db.screening import screen_corpus as _screen_corpus  # noqa: E402
from lit2db.grounding import (ground_literature as _ground,  # noqa: E402
                              validate_mapping as _validate_mapping)
from lit2db.output import (query as _query, record_candidate as _record_candidate,  # noqa: E402
                           review_queue as _review_queue, upsert as _upsert)
from lit2db.scoring import score_and_route as _score_and_route  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("lit2db")

# --- Config, resolved from the active instantiation (env-overridable) ----------------
# Falls back to lit2db.gate.DEFAULT_AUTOACCEPT (0.95), a conservative PLACEHOLDER — a real
# project overrides it from its calibrated `routing.auto_accept_threshold`. See gate.py.
AUTOACCEPT = resolve_threshold(env=os.environ)
DB_PATH = os.environ.get("LIT2DB_DB_PATH", str(_PLUGIN_ROOT / "examples" / "demo.db"))


# ------------------------------------------------------------------------------------
# Stage 3/4a — schema validation
# ------------------------------------------------------------------------------------
@mcp.tool()
def validate_record(record: dict) -> dict:
    """Pydantic-validate an ExtractedRecord's shape (Stage 3 output / Stage 4a).

    Returns {ok, errors}. A shape failure here is a hard block upstream of any grounding —
    an unparseable record cannot be verified, only quarantined."""
    try:
        rec = ExtractedRecord.model_validate(record)
        return {"ok": True, "record_id": rec.record_id, "n_fields": len(rec.fields)}
    except Exception as e:  # pydantic ValidationError or otherwise
        return {"ok": False, "errors": str(e)}


# ------------------------------------------------------------------------------------
# Stage 4b — grounding (literature: span-entailment proxy; structured: mapping validation)
# ------------------------------------------------------------------------------------
@mcp.tool()
def ground_literature(value: object, quote: str) -> dict:
    """Deterministic span-grounding for the literature adapter (Stage 4b, naive baseline).

    Thin wrapper over `lit2db.grounding.ground_literature`. Multi-valued fields ground PER
    ELEMENT: a list used to be stringified whole, so `["(+)-d-cadinol"]` was compared as the
    literal `"['(+)-d-cadinol']"` and scored 0.0 while the identical scalar scored 1.0.
    """
    return _ground(value, quote)


@mcp.tool()
def validate_mapping(value: object, field_spec: dict) -> dict:
    """Structured-adapter grounding (Stage 4b): type / range / enum conformance.

    Thin wrapper over `lit2db.grounding.validate_mapping`. A value outside the ratified
    valid_range is NOT dropped — it is flagged so the researcher can recalibrate the bound
    (the segregate-don't-drop discipline).
    """
    return _validate_mapping(value, field_spec)


@mcp.tool()
def build_store(xml: str, source_id: str, root_dir: str = "", meta: dict = {}) -> dict:
    """Parse JATS full-text XML into the Stage-1 offset-anchored store (Stage 1).

    Writes `full.txt`, `sections.json`, and `meta.json` under `<root_dir>/<source_id>/` and
    returns their paths plus stats. `full.txt` IS the coordinate system: an offset means
    "index into that exact file" and nothing else, because a source has no intrinsic offsets
    — a PDF has none, and XML byte positions move on re-serialization.

    Includes title, abstract, body, table cells, figure captions, and `<back>` appendices.
    **Excludes the reference list** — a bibliography is dense with other papers' claims, so
    leaving it in would let a value ground against text this paper never asserted.

    Raises rather than emitting an empty store: a silent zero-length store is
    indistinguishable, three stages later, from a paper that genuinely had nothing in it.
    """
    store = build_from_jats(xml, source_id, meta=dict(meta) or None)
    paths = write_store(store, root_dir or (_PLUGIN_ROOT / "sources"))
    return {**paths, **store["stats"], "source_id": source_id,
            "sections": [s["title"] for s in store["sections"]]}


@mcp.tool()
def locate_spans(source_dir: str, needle: str, limit: int = 50) -> dict:
    """Exact character offsets of `needle` in a store, with each hit's section label.

    **Use this instead of computing an offset yourself.** Grep is the right way to FIND a
    passage, but `grep -b` reports BYTE offsets while the store's contract is CHARACTER
    offsets — and every paper in this literature carries non-ASCII (µ, °, –, Greek), so the
    two diverge silently and by a growing amount through the document. An offset that is
    wrong still slices *something* out of the file, so the error survives every downstream
    check and lands in the database as a real-looking quote.

    Returns every occurrence up to `limit`, never just the first: a repeated entity is
    precisely the case the offset exists to disambiguate.
    """
    d = Path(source_dir)
    text = (d / "full.txt").read_text(encoding="utf-8")
    sections = json.loads((d / "sections.json").read_text())
    store = {"full_text": text, "sections": sections}
    hits = find_spans(text, needle, limit=limit)
    for h in hits:
        h["section"] = section_of(store, h["start"])
        h["quote"] = quote_at(text, h["start"], h["end"])
    return {"needle": needle, "n": len(hits), "spans": hits,
            "truncated": len(hits) >= limit}


# ------------------------------------------------------------------------------------
# Stage 3 — ensemble agreement across k extraction passes
# ------------------------------------------------------------------------------------
@mcp.tool()
def merge_extraction_passes(passes: list, identity_fields: dict = {}, rel_tol: float = 0.01,
                            expand_binomials: bool = False, synonyms: dict = {}) -> dict:
    """Merge k extraction passes into one record set carrying ensemble signals (Stage 3).

    `passes` is k lists of ExtractedRecord-shaped dicts — one list per independent pass.
    `identity_fields` maps entity_type -> the field that identifies the entity, e.g.
    `{"compound": "compound_name"}`. That mapping is ratified project substance (Stage-0.5
    axis 5, authoritative identity); this tool never guesses it.

    **Alignment, not comparison, is the hard part.** Three passes over one paper may find
    five compounds, four, and six, in different orders. Aligning them by position would
    compare one entity's measurement against another's and produce a confident, grounded,
    entirely wrong record — so records with no ratified identity field are refused unless the
    type is single-record per pass.

    A record a pass did not find becomes a missing VALUE for each of its fields, so
    record-level and field-level disagreement flow through one mechanism and cannot drift
    apart. A value only 1 of 3 passes proposed scores 1/3 and cannot auto-accept — but it is
    still emitted, because a compound the other passes missed is the most interesting thing
    an ensemble can surface, not something to delete.

    Returns `{records, ensemble, k, alignment}`. The per-field agreement report rides
    ALONGSIDE the records rather than inside them: `FieldValue` is a frozen contract and
    widening it to carry review detail is the quiet schema growth the invariant forbids.
    """
    res = merge_passes(list(passes), identity_fields=dict(identity_fields) or None,
                       rel_tol=rel_tol, expand_binomials=expand_binomials,
                       synonyms=dict(synonyms) or None)
    # Shape-check what we hand back; a merge that produced an invalid record should fail here
    # rather than three stages later at the gate.
    for r in res["records"]:
        ExtractedRecord.model_validate(r)
    return res


@mcp.tool()
def aggregate_ensemble(values: list, rel_tol: float = 0.01, expand_binomials: bool = False,
                       synonyms: dict = {}, normalizers: list = []) -> dict:
    """Turn k proposed values for ONE field into `c_ensemble` + `c_consistency` (Stage 3).

    Pass the value each of the k independent extraction passes produced, in any order, using
    null for a pass that found nothing (that is a real outcome and must not be dropped —
    otherwise a field two passes could not locate looks unanimous).

    This is deterministic on purpose. Whether two passes agree is a comparison under a stated
    normalization, not a judgement, so it does not belong to an agent: an LLM asked "do these
    agree?" would give a different answer on different days, and the routing bar built on top
    of it would mean nothing.

    Comparison is numeric (within `rel_tol`) when both sides are scalar measurements, and
    string-based under unicode/confusable/whitespace/case normalization otherwise — so
    `4.2` == `4.20`, `"4.2 uM"` == `"4.2 µM"`, but `2-MIB` != `2-methylisoborneol`.
    Domain knowledge is yours to supply: `synonyms` comes from the ratified instantiation's
    controlled vocabulary, and `expand_binomials` opts into abbreviated-genus matching.

    Returns `modal_value: null` with `ambiguous_modal: true` on a tie — there is no consensus
    value at k=2 split, or 2-2 at k=4, and nothing downstream should be handed one.
    """
    return summarize(list(values), rel_tol=rel_tol, expand_binomials=expand_binomials,
                     synonyms=dict(synonyms) or None,
                     steps=tuple(normalizers) if normalizers else DEFAULT_STEPS)


# ------------------------------------------------------------------------------------
# Stage 5/6 — composite confidence + routing
# ------------------------------------------------------------------------------------
@mcp.tool()
def score_and_route(record: dict, weights_key: str = "numeric",
                    ensemble_k: int = 0, ensemble_min_agreeing: int = 0,
                    review_lane: list | None = None) -> dict:
    """Composite confidence per field (blueprint 5.2) + per-field and record-level routing.

    Thin wrapper. The implementation is `lit2db.scoring.score_and_route` — see that module for
    why it is library code and not server code.
    """
    return _score_and_route(record, weights_key, ensemble_k, ensemble_min_agreeing, review_lane)


# ------------------------------------------------------------------------------------
# Stage 7 — the HARD write-gate + storage
# ------------------------------------------------------------------------------------
@mcp.tool()
def gate_upsert(record: dict, composite_confidence: float,
                db_path: str = "", autoaccept: float = -1.0,
                require_contradiction_search: bool = False,
                review_lane: list | None = None) -> dict:
    """The HARD write-gate (Stage 7). Writes to the DB IFF ALL hold:
      (1) composite_confidence >= auto-accept threshold,
      (2) no field routes to quarantine or human_review,
      (3) every field's source_status is 'active',
      (4) no field is contradicted by its own source (a BLOCK, never a confidence penalty —
          every confidence signal scores the span the extractor chose to surface),
      (5) `record.judge_verdict` is 'supported' — the adversarial judge's VETO (D-079). Not
          configurable, and absence blocks: a record nobody challenged has not passed its
          challenge, exactly as an unsearched value is not a clean one,
      (6) the record_id is not already held by a DIFFERENT record.
    Set require_contradiction_search=True to also block values whose source was never
    searched for counter-evidence: "we did not look" is not "we looked and it was clean".
    'deny' wins. A denied record is NOT written; its reasons are returned for routing to the
    human-review or quarantine queue.

    Thin wrapper. The conditions live in `lit2db.gate` — the same predicate the PreToolUse hook
    applies, so the two enforcement points cannot drift apart — and the write in
    `lit2db.output`.
    """
    return _upsert(record, composite_confidence, db_path or DB_PATH,
                   autoaccept if autoaccept >= 0 else AUTOACCEPT,
                   require_contradiction_search, review_lane)


# ------------------------------------------------------------------------------------
# Stage 1 — legal access resolution + the manual-acquisition queue
# ------------------------------------------------------------------------------------
# OA version ordinal (ratified D-026). A value read from a preprint is NOT the same claim as
# the same value in the version of record: numbers move in peer review. Version therefore rides
# in provenance and gates auto-accept -- it is not cosmetic metadata.
VERSION_RANK = {"publishedVersion": 3, "acceptedVersion": 2, "submittedVersion": 1}
CONTACT_EMAIL = os.environ.get("LIT2DB_CONTACT_EMAIL", "")


def can_auto_accept_version(version) -> bool:
    """Only the version of record may auto-accept. Anything earlier -- or unknown -- is
    flagged for human review rather than silently trusted (D-026)."""
    return VERSION_RANK.get(str(version or ""), 0) >= 3


def _ua(email: str) -> dict:
    e = email or CONTACT_EMAIL
    return {"User-Agent": f"lit2db/0.2.0 (+https://github.com/ColdMountain24/lit2db-marketplace"
                          f"{'; mailto:' + e if e else ''})"}


def _get_json(url: str, email: str, timeout_s: float):
    import ssl, urllib.request
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=_ua(email))
    with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))


@mcp.tool()
def resolve_access(doi: str, email: str = "", timeout_s: float = 15.0) -> dict:
    """Find the best LEGAL full-text location for a DOI (Stage 1, before any parse).

    Checks Unpaywall for an openly-licensed copy — publisher-hosted or in a repository. Never
    attempts to reach around a paywall; a source with no legal copy is returned with
    `needs_manual=True` for the manual-acquisition queue, which is the ratified behaviour.

    Unpaywall REQUIRES a contact email (free, no key). Pass `email` or set LIT2DB_CONTACT_EMAIL.
    On one measured corpus this lifted minable coverage from 56% to 83% with no credentials.

    Returns {ok, doi, is_oa, needs_manual, best:{url,host_type,version,license,auto_acceptable},
    n_alternatives, oa_status}. `version` is load-bearing: repository copies are frequently
    submitted (pre-review) or accepted (pre-copyedit) manuscripts, and only `publishedVersion`
    may auto-accept (D-026). Fails CLOSED — a failed lookup is `ok=False`, never "no OA exists".
    """
    d = (doi or "").strip().removeprefix("https://doi.org/").removeprefix("doi:")
    e = email or CONTACT_EMAIL
    if not d:
        return {"ok": False, "error": "no DOI supplied", "needs_manual": True}
    if not e:
        return {"ok": False, "needs_manual": True,
                "error": "Unpaywall requires a contact email. Pass email= or set "
                         "LIT2DB_CONTACT_EMAIL. This is a politeness requirement of the API, "
                         "not authentication — it unlocks no paywalled content."}
    try:
        u = _get_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(d, safe='/')}"
                      f"?email={urllib.parse.quote(e)}", e, timeout_s)
    except Exception as exc:
        return {"ok": False, "doi": d, "needs_manual": True,
                "error": f"Unpaywall lookup failed ({type(exc).__name__}) — access is UNKNOWN, "
                         f"not 'closed'. Retry before queueing for manual acquisition."}
    locs = [l for l in (u.get("oa_locations") or []) if l.get("url")]
    best = u.get("best_oa_location") or (locs[0] if locs else None)
    if not best:
        return {"ok": True, "doi": d, "is_oa": False, "needs_manual": True,
                "oa_status": u.get("oa_status"), "title": u.get("title"),
                "note": "no legal open copy found — route to the manual-acquisition queue"}
    version = best.get("version")
    return {"ok": True, "doi": d, "is_oa": True, "needs_manual": False,
            "oa_status": u.get("oa_status"), "title": u.get("title"),
            "n_alternatives": max(0, len(locs) - 1),
            "best": {"url": best.get("url_for_pdf") or best.get("url"),
                     "host_type": best.get("host_type"), "version": version,
                     "license": best.get("license"),
                     "auto_acceptable": can_auto_accept_version(version)}}


@mcp.tool()
def rank_manual_queue(items: list, terms: list = [], top_n: int = 25) -> dict:
    """Rank sources that need manual acquisition by likely payoff, so a human's time goes to the
    papers most worth chasing (Stage 1, the drained-queue discipline).

    `items`: dicts with any of {doi, title, abstract, year, cited_by, journal}.
    `terms`: the project's OWN priority terms, from the ratified instantiation — this tool
    supplies the ranking MECHANISM and never a domain vocabulary of its own (the scaffold stays
    domain-blind; what counts as interesting is the researcher's call).

    Score = term hits in title/abstract (weighted, title counts double) + recency + log citations.
    Every item is returned with a `why` breakdown: an opaque ranking a researcher cannot audit is
    just a different way of hiding a decision.
    """
    import math
    terms = [str(t).lower() for t in (terms or []) if str(t).strip()]
    years = [i.get("year") for i in items if isinstance(i.get("year"), int)]
    newest = max(years) if years else None
    out = []
    for it in items:
        title, abstract = str(it.get("title") or ""), str(it.get("abstract") or "")
        tl, al = title.lower(), abstract.lower()
        hit_t = sorted({t for t in terms if t in tl})
        hit_a = sorted({t for t in terms if t in al and t not in hit_t})
        term_score = 2.0 * len(hit_t) + 1.0 * len(hit_a)
        yr = it.get("year")
        rec = 0.0 if not (isinstance(yr, int) and newest) else max(0.0, 1.0 - (newest - yr) / 10.0)
        cited = it.get("cited_by") or 0
        cite_score = math.log1p(max(0, int(cited))) / math.log(101)  # ~1.0 at 100 citations
        score = term_score + 1.5 * rec + 1.0 * cite_score
        out.append({**{k: it.get(k) for k in ("doi", "title", "year", "cited_by", "journal")},
                    "score": round(score, 3),
                    "why": {"terms_in_title": hit_t, "terms_in_abstract": hit_a,
                            "recency": round(rec, 2), "citation_component": round(cite_score, 2)}})
    out.sort(key=lambda r: (-r["score"], r.get("doi") or ""))
    return {"n": len(out), "ranked_by": "term hits (title x2) + recency + log citations",
            "terms_used": terms,
            "note": "no terms supplied — ranking is recency + citations only" if not terms else "",
            "queue": out[:max(0, top_n)]}


def status_from_relations(relations) -> str:
    """Crossref `updated-by` relation types -> SourceStatus value. Pure, so it is testable
    without a network round-trip. Retraction is absorbing: a paper that was corrected and then
    retracted is retracted, never 'corrected'."""
    status = "active"
    for rel in (relations or []):
        r = str(rel or "").lower()
        if r in ("retraction", "withdrawal"):
            return "retracted"
        if r == "new_version":
            status = "superseded"
        elif r in ("correction", "corrigendum", "erratum") and status == "active":
            status = "corrected"
    return status


@mcp.tool()
def check_retraction(doi: str, timeout_s: float = 10.0) -> dict:
    """Retraction / supersession check against Crossref (blueprint 3, ratified addition D2).

    Maps Crossref `updated-by` relations onto SourceStatus:
      retraction|withdrawal -> retracted · correction|corrigendum|erratum -> corrected
      new_version           -> superseded · (none)                        -> active

    Returns {ok, status, evidence, checked_at}. **Fails CLOSED and says so**: if the lookup errors
    or the DOI is unknown, `ok` is False and `status` is None. Do NOT stamp source_status='active'
    on a failed check — an unverified source must route to human review, or the retraction gate
    silently becomes a no-op. `active` here means "Crossref was reached and reports no retraction",
    never "we could not tell".
    """
    import ssl, urllib.request  # noqa: E402 - kept local to the network path

    # macOS python.org builds ship with no CA store wired up (ssl.get_default_verify_paths().cafile
    # is None), so every HTTPS call dies with CERTIFICATE_VERIFY_FAILED until someone runs
    # "Install Certificates.command". Prefer certifi when it is importable so the check works on a
    # fresh machine instead of silently degrading to "unknown" for every source.
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()

    d = (doi or "").strip().removeprefix("https://doi.org/").removeprefix("doi:")
    if not d:
        return {"ok": False, "status": None, "evidence": "no DOI supplied", "checked_at": None}
    now = datetime.now(timezone.utc).isoformat()
    req = urllib.request.Request(
        f"https://api.crossref.org/works/{urllib.parse.quote(d, safe='/')}",
        headers={"User-Agent": "lit2db/0.1.0 (retraction-check; mailto:aryannnpathak@gmail.com)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as r:
            msg = json.loads(r.read().decode("utf-8"))["message"]
    except Exception as e:
        return {"ok": False, "status": None, "checked_at": now,
                "evidence": f"Crossref lookup failed ({type(e).__name__}); status is UNKNOWN, "
                            f"not active — route to human review"}
    rels = [(u.get("type") or "").lower() for u in (msg.get("updated-by") or [])]
    return {"ok": True, "status": status_from_relations(rels), "checked_at": now,
            "evidence": {"doi": d, "relations": rels,
                         "title": (msg.get("title") or [None])[0]}}


@mcp.tool()
def db_query(db_path: str = "", limit: int = 50) -> dict:
    """Read the ML-ready view: auto-accepted, active-source records only (Stage 7 output)."""
    return _query(db_path or DB_PATH, limit=limit)


@mcp.tool()
def resolve_entities(records: list, identity: list = [], fallback: list = [],
                     explain_text: bool = False) -> dict:
    """Stage 5 — group per-source records into canonical entities and report conflicts.

    Wired here because it had NOT been. `entity.py` shipped with tests, a dedicated
    `entity-resolver-agent`, prose in five command/agent files, and a CODEMAP line claiming it
    was "wired into the MCP server" — while nothing imported it. A whole declared pipeline stage
    was unreachable, and `tests/test_declarations.py` caught it on its first run.

    It CLASSIFIES disagreement across sources; it never resolves it. Which of two conflicting
    values is right is researcher substance.
    """
    kw = {}
    if identity:
        kw["identity"] = tuple(identity)
    if fallback:
        kw["fallback"] = tuple(fallback)
    index = _build_index(list(records), **kw)
    return {"index": index, "explain": _explain(index) if explain_text else None}


@mcp.tool()
def screen_corpus(sources: list, require_any: list = [], exclude_any: list = [],
                  require_all: list = []) -> dict:
    """Corpus-build screen (T22): keep sources whose text mentions the ratified entity terms.

    The terms are RATIFIED INPUT, never inferred here. Screening is measured to be lossy —
    LLM4SCREENLIT reports raw-accuracy screening losing 63% of relevant studies — so this is a
    deterministic term match whose recall a researcher can reason about, and what it drops is
    reported rather than discarded silently.
    """
    return _screen_corpus(list(sources), require_any=tuple(require_any),
                          exclude_any=tuple(exclude_any), require_all=tuple(require_all))


@mcp.tool()
def dedupe_corpus(papers: list) -> dict:
    """Corpus-build dedup (T16): collapse corrections/errata onto the paper they correct.

    Identifiers where they exist, a flagged near-duplicate where they do not — never a silent
    merge on title similarity alone.
    """
    return _dedupe(list(papers))


@mcp.tool()
def record_candidate(record: dict, composite_confidence: float, gate_result: dict,
                     db_path: str = "", source_id: str = "") -> dict:
    """Record a record in the CANDIDATE pool, whatever the gate decided.

    The candidate pool is the large database; `records` is the smaller high-quality one that
    cleared the gate. This tool cannot reach the second — different table, and `db_query` reads
    only `records`. A near-miss kept with its evidence is a minute of a researcher's attention
    away from being a row; discarded, it is a paper to read again.
    """
    return _record_candidate(record, composite_confidence, gate_result,
                             db_path or DB_PATH, source_id)


@mcp.tool()
def review_queue(db_path: str = "", source_id: str = "", limit: int = 100) -> dict:
    """What a human would confirm next, best-first, with the reason each record stopped short.

    Best-first because the point is acceleration: the near-misses are where attention converts
    into rows, and a worst-first queue spends it on the records least likely to survive.
    """
    return _review_queue(db_path or DB_PATH, source_id=source_id, limit=limit)


if __name__ == "__main__":
    mcp.run()
