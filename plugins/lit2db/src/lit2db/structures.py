"""Stage 4b for chemical structures — RESOLVE, never generate (D-084).

The extractor pulls a compound NAME, which is in the text, quotable, and grounds like any other
string. It never emits a SMILES. This module turns that name into a structure by asking a public
authority, so **a model that never writes a structure cannot hallucinate one** — the hazard is
removed rather than checked for afterwards.

That is also, independently, how the collaborator built his own database: 1,062 compounds
identified by looking names up in the Natural Products Atlas, StreptomeDB and the Dictionary of
Natural Products. Eleven of his nineteen columns are resolve-or-compute rather than reading.

WHAT THIS DELIBERATELY DOES NOT DO
  * It does not guess. A name matching several compounds resolves to NOTHING — names lose
    stereochemistry, and a confident wrong identifier is the failure that would discredit the
    whole database. Same discipline as a tied ensemble vote: nothing downstream is handed a guess.
  * It does not fall back to formula matching. Measured on the collaborator's own data: 642
    distinct formulas over 1,058 compounds, and **44 of them share C15H24**. For terpenoids a
    formula agreement is close to no evidence at all.
  * It does not block a record. Not every entry needs a structure (D-083); an unresolved name
    costs the record nothing, because attempting a resolution must never be worse than skipping it.

NETWORK ACCESS IS INJECTED (`fetch`) rather than imported, so the parsing logic is testable with
no network and no mocking of urllib internals. Fails CLOSED: any error resolves to nothing.

**PubChem only, for now.** The Natural Products Atlas is the better domain source and covers 55%
of the collaborator's rows, but this module ships no NPAtlas client because none has ever been run
against the live API — shipping a resolver for an endpoint shape nobody verified is the exact
defect `tests/test_declarations.py` exists to prevent. Add it after one live verification session.
"""
from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timezone

PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_PROPS = "CanonicalSMILES,InChI,InChIKey,MolecularFormula,MolecularWeight,ExactMass"


# Sentinel: the request never completed, as opposed to the authority saying "no match".
_TRANSPORT = object()


def _ssl_context():
    """A context that trusts a real CA bundle.

    Python builds from python.org ship no system trust store, so every HTTPS call here failed
    certificate verification — and because the fetcher fails closed to `None`, that surfaced as
    "authority unreachable or no match" for EVERY compound. Silent, total, and indistinguishable
    from PubChem simply not knowing the name. Found the moment structure resolution was wired
    into the headless driver.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:                                          # noqa: BLE001
        return ssl.create_default_context()


def http_get_json(url: str, timeout_s: float = 20.0):
    """The default fetcher. Returns parsed JSON, `None` for no-match, `_TRANSPORT` on failure.

    The two are held apart deliberately. A 404 from PubChem means "this authority does not know
    that name" — a real answer. A DNS failure, a TLS error or a timeout means "we never asked",
    and reporting that as a non-match would let a run with no network at all look exactly like a
    run whose every compound was unknown. Same distinction the retraction check and the
    negative-data policy already make.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "lit2db/structure-resolver"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=_ssl_context()) as r:
            return json.load(r)
    except urllib.error.HTTPError:
        return None                                            # the authority answered: no match
    except Exception:                                          # noqa: BLE001 — we never asked
        return _TRANSPORT


def _unresolved(name: str, why: str, **extra) -> dict:
    out = {"resolved": False, "name": name, "why": why,
           "retrieved_at": datetime.now(timezone.utc).isoformat()}
    out.update(extra)
    return out


def resolve_structure(name: str, fetch=http_get_json, timeout_s: float = 20.0) -> dict:
    """Compound name -> a structure record from a public authority, or an explicit non-answer.

    Returns `{"resolved": True, "authority": "pubchem", "authority_id": <CID>, "smiles": ...,
    "inchikey": ..., "inchi": ..., "molecular_formula": ..., "molecular_weight": ...,
    "exact_mass": ..., "retrieved_at": ...}` or `{"resolved": False, "why": ...}`.

    `retrieved_at` is load-bearing and weaker than this project's provenance contract wants:
    **PubChem publishes no release version**, so the pin is (CID, retrieval timestamp) and a
    re-run months later may resolve differently. `StructuredProvenance.db_version` exists because
    "an unpinned value is unreproducible"; this is the honest best available, and saying so is
    the point rather than quietly filling the field with today's date and calling it a version.
    """
    n = (name or "").strip()
    if not n:
        return _unresolved(name, "empty name")

    url = f"{PUBCHEM}/compound/name/{urllib.parse.quote(n)}/property/{_PROPS}/JSON"
    blob = fetch(url, timeout_s)
    if blob is _TRANSPORT:
        # WE NEVER ASKED. Held apart from a real non-match because they are opposite findings:
        # this one is a run failure to retry, and a whole wave of it (a bad TLS trust store, no
        # network) would otherwise read as "none of these compounds are in PubChem" — which for
        # a novel-compound database is a *plausible-looking* and completely wrong conclusion.
        return _unresolved(n, "authority unreachable — not asked, not answered",
                           unreachable=True)
    if not blob:
        # The authority answered and does not know this name. A real finding, and for this
        # schema often the interesting one: a genuinely new compound is not in PubChem yet.
        return _unresolved(n, "no match in authority")

    props = (blob.get("PropertyTable") or {}).get("Properties") or []
    if not props:
        return _unresolved(n, "no match in authority")
    if len(props) > 1:
        # AMBIGUITY RESOLVES TO NOTHING. A name mapping to several CIDs usually means the name
        # omits stereochemistry, which for terpenoids is the difference that matters.
        return _unresolved(n, f"name matches {len(props)} compounds — ambiguous, not guessed",
                           ambiguous=True,
                           candidates=[p.get("CID") for p in props[:10]])

    p = props[0]
    if not p.get("InChIKey"):
        return _unresolved(n, "authority returned no InChIKey")
    return {"resolved": True, "name": n, "authority": "pubchem",
            "authority_id": str(p.get("CID", "")),
            "smiles": p.get("CanonicalSMILES"), "inchi": p.get("InChI"),
            "inchikey": p.get("InChIKey"),
            "molecular_formula": p.get("MolecularFormula"),
            "molecular_weight": p.get("MolecularWeight"),
            "exact_mass": p.get("ExactMass"),
            "retrieved_at": datetime.now(timezone.utc).isoformat()}


# Which resolved keys become fields on the record, and in what order they are attached.
STRUCTURE_FIELDS = ("smiles", "inchikey", "inchi", "molecular_formula",
                    "molecular_weight", "exact_mass", "authority_id")


def structure_fields(resolved: dict, source_id: str, producing_process: str) -> list:
    """Turn a resolved structure into FieldValue dicts carrying `StructuredProvenance`.

    Each structure field's evidence is the authority record, NOT a span in the paper — which is
    the whole reason a SMILES could not be span-grounded. The compound NAME carries the
    literature provenance; these carry the lookup. Two auditable links on one row.

    Returns [] when nothing resolved, so an unresolved name simply contributes no fields and the
    record stands on what it does have.
    """
    if not (resolved or {}).get("resolved"):
        return []
    prov = {"kind": "structured",
            "source_id": source_id,
            "retrieval_timestamp": resolved["retrieved_at"],
            "producing_process": producing_process,
            "source_status": "active",
            "database": resolved.get("authority", "pubchem"),
            "record_id": str(resolved.get("authority_id", "")),
            # PubChem has no release version; the retrieval date IS the pin. Stated, not faked.
            "db_version": f"retrieved:{resolved['retrieved_at'][:10]}",
            "source_query": f"name={resolved.get('name', '')}"}
    out = []
    for key in STRUCTURE_FIELDS:
        val = resolved.get(key)
        if val in (None, ""):
            continue
        out.append({
            "field_name": key if key != "authority_id" else "authority_compound_id",
            "value": val,
            "provenance": dict(prov),
            # A structure that came back from the authority IS grounded — against the authority.
            # `validate_mapping` is the structured adapter's grounding rule and this is its case.
            "confidence_components": {"c_grounded": 1.0, "_grounding_mode": "authority_resolved"},
            "contradiction_search": "clean",
        })
    return out
