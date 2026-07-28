"""Organism name -> a taxon from a public authority. The sibling of `structures.py`.

WHY THIS EXISTS, measured rather than assumed. Nine of twenty-four adjudicated records were the
SAME ORGANISM written two ways — `sp (#HK18)` against `sp. (#HK18)`, `sp. KC 191` against
`strain KC 191`, `commune Vaucher (EAWAG 122b)` against `commune EAWAG 122b`, `Tü 6071` against
`Tue 6071`. That was the single largest class of researcher attention spent in the whole queue.

Tested against NCBI Taxonomy on those exact strings:

    as stored (species carries the strain)   ->  0 of 4 resolve
    with the strain stripped                 ->  4 of 4 resolve

So the authority is not the problem. The `species` field is carrying the strain, which is the
defect D-058 already ruled on for `source_organism` and which the compound spec repeated. D-058
also closed the tempting escape route: normalisation "cannot know `DSM 43851` is a strain
suffix", so the real fix belongs in the SCHEMA.

**This is deliberately NOT that fix.** The compound spec is FROZEN and a wave is running against
it. This resolves the organism to an authority and contributes NEW fields, exactly as
`resolve_structure` does for compounds — additive, so the stored organism text is untouched and
no extracted record needs re-doing. Two records naming one organism two ways then join on the
taxid instead of on the string. Splitting `species` properly stays a Stage-8 amendment.

`unreachable` is NOT `no match`, for the reason D-094 states: one is a run to retry, the other is
evidence about the organism. A whole wave of the first read as the second would be plausible and
wrong.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from .structures import _ssl_context, http_get_json  # one HTTPS/certifi path, not two

AUTHORITY = "ncbi_taxonomy"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# NCBI allows 3 requests/second without an API key. Exceeding it returns HTTP 429, which
# `http_get_json` reports as unreachable — and a self-inflicted rate limit reported as "the
# authority could not be reached" is a measurement of our own impatience. Measured while
# building this: firing four lookups back to back produced two spurious errors.
MIN_INTERVAL_S = 0.4
_last_call = [0.0]

# --- strain-text folds, each reported separately -----------------------------------------
# Conventions, not differences. Reported per rung so a loosening stays auditable rather than
# disappearing into one number — the same discipline the compound-name ladder uses.
_PREFIX = re.compile(r"\b(?:sp{1,2}\.?|strain|str\.|isolate|nov\.)\s*", re.I)
_AUTHORITY_CITATION = re.compile(r"\b(?:Vaucher|Ehrenb\.?|L\.|Kütz\.?|Kutz\.?)\b")
_BRACKETS = re.compile(r"[()\[\]#]")
_TRANSLIT = {"ü": "ue", "ö": "oe", "ä": "ae", "ß": "ss"}


def fold_strain(s: str) -> tuple:
    """(folded, [rungs applied]). Conservative on purpose: it removes CONVENTIONS, never digits.

    `Tü 6071` and `Tue 6071` are one strain under two transliterations; `KC 191` and `KC 192`
    are two strains, and nothing here can merge them because no rung touches a numeral.
    """
    rungs = []
    out = s or ""
    if _AUTHORITY_CITATION.search(out):
        out = _AUTHORITY_CITATION.sub(" ", out)
        rungs.append("authority_citation")
    if _PREFIX.search(out):
        out = _PREFIX.sub(" ", out)
        rungs.append("rank_prefix")
    if any(ch in out for ch in _TRANSLIT):
        for ch, rep in _TRANSLIT.items():
            out = out.replace(ch, rep)
        rungs.append("transliteration")
    if _BRACKETS.search(out):
        out = _BRACKETS.sub(" ", out)
        rungs.append("brackets")
    folded = re.sub(r"\s+", " ", out).strip().lower()
    return folded, rungs


def species_epithet(species: str):
    """The species epithet, or None when the field carries only a strain.

    `sp.` / `strain` mean "species not determined", so a value that OPENS with one has no
    epithet at all — everything after it is the strain. Reading the first surviving token as an
    epithet produced the query `Streptomyces kc` from `sp. KC 191`: a binomial that cannot exist,
    asked of the authority as though it might. Caught by a test asserting the query list.

    An epithet is lower-case alphabetic; a token carrying digits or capitals is a strain code
    (`KC 191`, `EAWAG 122b`, `Tü 6071`), never a species.
    """
    raw = (species or "").strip()
    if not raw or _PREFIX.match(raw):
        return None
    head = _AUTHORITY_CITATION.sub(" ", raw).strip().split()
    if not head:
        return None
    token = head[0].strip("().[]#")
    if len(token) < 3 or not token.isalpha() or not token.islower():
        return None
    return token


def candidates(genus: str, species: str) -> list:
    """Query strings from most to least specific. The rung that matches is REPORTED, because
    'matched on genus alone' and 'matched on genus and species' are different facts about how
    much of the organism the authority actually confirmed."""
    genus = (genus or "").strip()
    species = (species or "").strip()
    out = []
    if genus and species:
        out.append(("genus_species_strain", f"{genus} {species}"))
        epithet = species_epithet(species)
        if epithet:
            out.append(("genus_species", f"{genus} {epithet}"))
    if genus:
        out.append(("genus", genus))
    seen, uniq = set(), []
    for rung, q in out:
        if q.lower() not in seen:
            seen.add(q.lower())
            uniq.append((rung, q))
    return uniq


def _throttle() -> None:
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def resolve_taxon(genus: str, species: str = "", fetch=http_get_json,
                  timeout_s: float = 20.0) -> dict:
    """Resolve an organism to an NCBI taxid. Fails CLOSED and says WHICH way it failed.

    Returns `resolved` with `taxid`, `matched_rung` and `query`; or `resolved=False` with either
    `unreachable=True` (the authority was not reached — retry) or `unreachable=False` (the
    authority was reached and holds no such taxon — evidence).
    """
    tried = []
    for rung, query in candidates(genus, species):
        _throttle()
        url = f"{ESEARCH}?db=taxonomy&retmode=json&term={_q(query)}"
        data = fetch(url, timeout_s=timeout_s)
        if data is None:
            return {"resolved": False, "unreachable": True, "authority": AUTHORITY,
                    "why": f"authority unreachable while querying {query!r} — NOT evidence that "
                           f"the organism is unknown; this is a run to retry",
                    "tried": tried + [rung]}
        ids = ((data or {}).get("esearchresult") or {}).get("idlist") or []
        tried.append(rung)
        if ids:
            return {"resolved": True, "unreachable": False, "authority": AUTHORITY,
                    "taxid": str(ids[0]), "matched_rung": rung, "query": query,
                    "tried": tried,
                    "retrieved_at": datetime.now(timezone.utc).isoformat()}
    return {"resolved": False, "unreachable": False, "authority": AUTHORITY,
            "why": "authority reached; no taxon matched at any rung", "tried": tried}


def _q(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote_plus(s)


def taxon_fields(resolved: dict, source_id: str, producing_process: str) -> list:
    """The resolved taxon as FieldValue dicts, mirroring `structures.taxon`-style provenance.

    ADDITIVE: returns [] when nothing resolved, so an unresolved organism costs the record
    nothing and the frozen fields are never rewritten. The organism TEXT keeps its literature
    provenance; these carry the authority lookup.
    """
    if not (resolved or {}).get("resolved"):
        return []
    prov = {"kind": "structured", "source_id": source_id,
            "retrieval_timestamp": resolved["retrieved_at"],
            "producing_process": producing_process, "source_status": "active",
            "database": AUTHORITY, "record_id": str(resolved.get("taxid", ""))}
    return [{"field_name": "organism_taxid", "value": resolved["taxid"], "provenance": prov},
            {"field_name": "organism_taxon_rung", "value": resolved["matched_rung"],
             "provenance": prov}]


def same_organism(a: dict, b: dict) -> bool | None:
    """Do two resolved organisms denote the same taxon? None when either did not resolve.

    None rather than False on purpose: 'we could not check' is not 'they differ', which is the
    same distinction `cant_tell` draws in the adjudication vocabulary and `not_run` draws for the
    judge. Returning False here would silently convert an unreachable authority into a
    disagreement.
    """
    if not (a or {}).get("resolved") or not (b or {}).get("resolved"):
        return None
    return a["taxid"] == b["taxid"]
