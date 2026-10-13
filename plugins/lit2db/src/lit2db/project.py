"""A lit2db project on disk: from a ratified spec to a spreadsheet, in the researcher's own folder.

## Why this exists (D-199)

Measured 2026-09-16 against the bar "someone who has never heard of lit2db builds a ~20-paper
database on their own Mac, alone": the engine was built and the path through it was not. The
papers for every real run were fetched by scripts in the PRIVATE workspace; the three run prompts
lived there too, written for one domain; `/lit2db-start` called the runner without the config it
requires; the server's default database sat inside the plugin install, which an update replaces;
and nothing exported a table a researcher could open. Each piece was done by hand, per run, by the
one person who knew how. This module is those hand steps, written down once.

## The layout, and why it lives in the working directory

    lit2db/
      spec.json              the ratified spec (written by the interview, never by this module)
      corpus/search.json     the query as executed, every hit, and what happened to each
      corpus/sources/<id>/   offset-anchored stores (`full.txt` is the coordinate system)
      prompts/               the plugin's domain-blind templates, stamped for this project
      run.json               the run configuration `scripts/run_wave.py` reads
      run/                   per-paper passes, verdicts, manifest
      database.db            accepted records, candidates, a human's adjudications
      results/               papers.csv, candidates.csv, accepted.csv

Everything a researcher produces is theirs and sits where they can see it, back it up and delete
it. Nothing is written under the plugin, because the plugin folder is replaced on update.

## What it will not do

It never ratifies. `plan` refuses a spec that does not validate as a `SchemaReadySpec`, so an
unratified field or an unratified corpus query cannot reach a run through here. It never reaches
around a paywall: full text comes only from Europe PMC's open-access set or from a PDF the
researcher already holds and adds by hand (`add_pdf`), an abstract only when the caller says the
ratified scope admits abstracts, and everything else is listed as unreachable rather than
dropped. And it records a retracted source as retracted and does not
read it, because the wave driver stamps every source it is handed `active`.

Stdlib only, like `store` and `gate`: this runs before anything else is known to work.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import pathlib
import re
import ssl
import time
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .ensemble import ANCHOR_LEVELS
from .store import (build_from_abstract, build_from_jats, build_from_pdf, store_dirname,
                    write_store)

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = PLUGIN_ROOT / "templates" / "prompts"

# Europe PMC's pub type for a retraction NOTICE and for a retracted ARTICLE. A notice is not a
# study; a retracted article must not be read as one.
RETRACTED_TYPES = {"retracted publication", "retraction of publication"}


def paths(root: str | pathlib.Path = ".") -> dict:
    base = pathlib.Path(root).resolve() / "lit2db"
    return {
        "base": base,
        "spec": base / "spec.json",
        "search": base / "corpus" / "search.json",
        "sources": base / "corpus" / "sources",
        "prompts": base / "prompts",
        "config": base / "run.json",
        "run": base / "run",
        "db": base / "database.db",
        "results": base / "results",
    }


# D-208. FAT is the original design: k readers, a contradiction hunter, then a judge per batch,
# each a full-harness agent that opens the store with a tool. LEAN is one read and one check per
# paper: one reader, and one judge call that also hunts, each stripped of the harness and handed
# the source pasted in. Measured on the first review wave: fat about 425,000 work tokens per
# paper; lean estimated at under 100,000, to be measured against the fat run (D-208).
RUN_MODES = {
    "fat": {"run_mode": "fat", "harness": "full", "source_inline": False,
            "judge_hunts": False},
    "lean": {"run_mode": "lean", "harness": "stripped", "source_inline": True,
             "judge_hunts": True, "judge_batch_size": "all", "judge_audit_fraction": 1.0},
}


def init(root: str | pathlib.Path = ".") -> dict:
    p = paths(root)
    for key in ("sources", "prompts", "run", "results"):
        p[key].mkdir(parents=True, exist_ok=True)
    return {k: str(v) for k, v in p.items()}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _ssl_context():
    try:
        import certifi                                   # optional: python.org builds lack roots
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def http_get(url: str, *, contact: str = "", timeout: int = 60) -> bytes:
    ua = "lit2db (research tool; https://github.com/ColdMountain24/lit2db-marketplace)"
    if contact:
        ua += f" mailto:{contact}"
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return r.read()


Getter = Callable[[str], bytes]


def search(query: str, *, limit: int, get: Getter, pause: float = 0.2) -> dict:
    """Run a Europe PMC query and return the hit count plus up to `limit` records, in the order
    Europe PMC returns them. The query string is passed through untouched: it is the ratified
    boundary of the corpus, and rewriting it here would move that boundary silently."""
    results, cursor, hit_count = [], "*", None
    while len(results) < limit:
        page = min(1000, limit - len(results))
        qs = urllib.parse.urlencode({"query": query, "format": "json", "resultType": "core",
                                     "pageSize": page, "cursorMark": cursor})
        data = json.loads(get(f"{EPMC}/search?{qs}"))
        if hit_count is None:
            hit_count = int(data.get("hitCount", 0))
        batch = (data.get("resultList") or {}).get("result") or []
        results.extend(batch)
        nxt = data.get("nextCursorMark")
        if not batch or not nxt or nxt == cursor:
            break
        cursor = nxt
        time.sleep(pause)
    return {"hit_count": hit_count or 0, "results": results[:limit]}


def _pub_types(hit: dict) -> list:
    return [t.lower() for t in ((hit.get("pubTypeList") or {}).get("pubType") or [])]


def _describe(hit: dict) -> dict:
    return {
        "pmid": hit.get("pmid"), "pmcid": hit.get("pmcid"), "doi": hit.get("doi"),
        "title": hit.get("title"), "year": hit.get("pubYear"),
        "journal": ((hit.get("journalInfo") or {}).get("journal") or {}).get("title"),
        "license": hit.get("license"),
        "open_access": hit.get("isOpenAccess") == "Y", "in_epmc": hit.get("inEPMC") == "Y",
        "pub_types": _pub_types(hit),
    }


def preview(query: str, *, limit: int, get: Getter) -> dict:
    """Counts only, nothing downloaded: for showing a researcher what a query returns BEFORE
    they ratify it. `readable_full_text` is what Europe PMC flags as open access with full text
    in its own store, which is exactly what `fetch` will try."""
    s = search(query, limit=limit, get=get)
    hits = [_describe(h) for h in s["results"]]
    return {
        "query": query, "endpoint": EPMC, "executed_at": _now(),
        "hits": s["hit_count"], "examined": len(hits),
        "readable_full_text": sum(1 for h in hits if h["open_access"] and h["in_epmc"]
                                  and h["pmcid"]),
        "abstract_available": sum(1 for h in s["results"] if h.get("abstractText")),
        "retracted": sum(1 for h in hits if RETRACTED_TYPES & set(h["pub_types"])),
        "sample_titles": [h["title"] for h in hits[:10]],
    }


def _ratified_epmc_query(spec_path: pathlib.Path) -> str:
    from .spec_context import load_spec                  # pydantic: only needed once a spec exists
    spec = load_spec(spec_path)                          # raises on anything unratified
    queries = [q for q in spec.source_scope.queries if q.corpus.lower() in ("europepmc", "epmc")]
    if not queries:
        raise ValueError("the spec records no ratified Europe PMC query "
                         "(source_scope.queries with corpus 'europepmc')")
    if len(queries) > 1:
        raise ValueError("the spec records more than one Europe PMC query; fetch runs exactly "
                         "one ratified query, so say which by editing the spec, not here")
    return queries[0].query


def fetch(root: str | pathlib.Path = ".", *, limit: int, allow_abstracts: bool,
          get: Getter, query: Optional[str] = None, pause: float = 0.34) -> dict:
    """Execute the ratified query and store every paper that can be read legally.

    Resumable: a paper whose store already exists is not fetched again. Writes `search.json`
    on every run with the full outcome per hit, so the corpus report is read from disk rather
    than reconstructed from memory.
    """
    p = paths(root)
    init(root)
    if query is None:
        query = _ratified_epmc_query(p["spec"])
    s = search(query, limit=limit, get=get)
    retrieved_at = _now()
    papers = []
    for hit in s["results"]:
        d = _describe(hit)
        entry = {**d, "source_id": None, "outcome": None, "why": ""}
        papers.append(entry)
        if RETRACTED_TYPES & set(d["pub_types"]):
            entry.update(outcome="retracted", why="Europe PMC marks it retracted; not read")
            continue
        meta = {"doi": d["doi"], "pmid": d["pmid"], "pmcid": d["pmcid"], "title": d["title"],
                "journal": d["journal"], "year": d["year"], "license": d["license"],
                "query": query, "retrieved_at": retrieved_at, "version": "publishedVersion"}
        if d["open_access"] and d["in_epmc"] and d["pmcid"]:
            sid = d["pmcid"]
            entry["source_id"] = sid
            if (p["sources"] / store_dirname(sid) / "full.txt").exists():
                entry.update(outcome="full_text", why="already stored")
                continue
            try:
                xml = get(f"{EPMC}/{sid}/fullTextXML")
                store = build_from_jats(xml, sid, meta={**meta,
                                        "acquisition_route": "europepmc_open_access_full_text"})
                write_store(store, p["sources"])
                # Europe PMC serves meeting abstracts as open "full text". True, and misleading
                # in a reading list: measured on the first clean run, 2 of 3 "full text" papers
                # were conference abstracts of under 2,400 characters.
                entry.update(outcome="full_text",
                             why="published as a conference abstract"
                             if "abstract" in d["pub_types"] else "")
                time.sleep(pause)
                continue
            except Exception as exc:                     # noqa: BLE001 — recorded, never silent
                entry["why"] = f"full text failed ({type(exc).__name__}: {str(exc)[:120]})"
        if allow_abstracts and hit.get("abstractText"):
            sid = d["pmcid"] or (f"PMID_{d['pmid']}" if d["pmid"] else None) \
                or (f"DOI_{d['doi']}" if d["doi"] else None)
            if sid:
                entry["source_id"] = sid
                if not (p["sources"] / store_dirname(sid) / "full.txt").exists():
                    store = build_from_abstract(d["title"] or "", hit["abstractText"], sid,
                                                meta={**meta,
                                                      "acquisition_route": "europepmc_abstract"})
                    write_store(store, p["sources"])
                entry.update(outcome="abstract_only",
                             why=(entry["why"] + "; " if entry["why"] else "")
                             + "no open full text, abstract stored")
                continue
        entry.update(outcome="unreachable",
                     why=entry["why"] or "no open full text in Europe PMC"
                     + ("" if allow_abstracts else "; abstracts not admitted by this run"))

    counts = {"hits": s["hit_count"], "examined": len(papers)}
    for e in papers:
        counts[e["outcome"]] = counts.get(e["outcome"], 0) + 1
    # A PDF a researcher added by hand is not a hit of this query, so it is not counted among the
    # query's outcomes; but rewriting this file without it would drop it from the corpus without
    # a word. Carried forward as it was, after the query's own hits.
    local = _local_entries(p)
    if local:
        counts[LOCAL_PDF] = len(local)
    record = {"query": query, "endpoint": EPMC, "executed_at": retrieved_at,
              "limit": limit, "allow_abstracts": allow_abstracts, "counts": counts,
              "papers": papers + local}
    p["search"].parent.mkdir(parents=True, exist_ok=True)
    p["search"].write_text(json.dumps(record, indent=1), encoding="utf-8")
    return {"counts": counts, "search": str(p["search"])}


# --- sources added by hand: PDFs with no JATS anywhere ------------------------------------------

LOCAL_PDF = "local_pdf"


def _local_entries(p: dict) -> list:
    if not p["search"].exists():
        return []
    rec = json.loads(p["search"].read_text(encoding="utf-8"))
    return [e for e in rec.get("papers", []) if e.get("route") == LOCAL_PDF]


def _normal_doi(doi: str) -> str:
    """A DOI with any resolver prefix removed. Lowercased, because DOIs are case-insensitive and
    one paper must map to one store directory however its DOI was typed."""
    d = str(doi or "").strip()
    d = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", d, flags=re.I)
    return d.lower()


def add_pdf(root: str | pathlib.Path = ".", pdf_path: str | pathlib.Path = "", *,
            source_id: str = "", doi: str = "", title: str = "", how_found: str = "") -> dict:
    """Store a PDF the researcher holds as a readable source, beside what `fetch` stored.

    The source id is `DOI:<doi>` whenever a DOI is given (so it files where every other reader
    of that DOI looks, via `store_dirname`), and the given `source_id` otherwise. The store is
    built by `store.build_from_pdf`; the corpus record gains an entry with route `local_pdf`,
    the PDF's sha256 and `how_found`, which `fetch` carries forward when it rewrites the record.

    Re-adding the same PDF is a no-op. Adding a DIFFERENT text under an id that already has a
    store is refused: offsets recorded against the old text would silently point elsewhere.
    """
    p = paths(root)
    init(root)
    nd = _normal_doi(doi)
    sid = f"DOI:{nd}" if nd else str(source_id or "").strip()
    if not sid:
        raise ValueError("give the PDF a DOI or a source_id; a source with neither cannot be "
                         "cited back to anything")
    src = pathlib.Path(pdf_path).expanduser()
    if not src.is_file():
        raise ValueError(f"no PDF at {src}")
    data = src.read_bytes()
    sha = hashlib.sha256(data).hexdigest()

    d = p["sources"] / store_dirname(sid)
    if (d / "full.txt").exists():
        try:
            old = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        if old.get("pdf_sha256") != sha:
            raise ValueError(
                f"{sid} already has a stored text at {d} "
                f"({old.get('acquisition_route') or old.get('source_text_scope') or 'unknown'}"
                f"{', from a different PDF' if old.get('pdf_sha256') else ''}). Replacing it would "
                f"move every offset recorded against it. Remove that folder first if replacing "
                f"it is what you mean.")
        store_meta, outcome = old, "already stored"
    else:
        store = build_from_pdf(data, sid, meta={
            "doi": nd or None, "title": title or None, "acquisition_route": LOCAL_PDF,
            "how_found": how_found, "source_file": src.name, "retrieved_at": _now()})
        write_store(store, p["sources"])
        store_meta, outcome = store["meta"], "stored"

    why = "added by hand from a local PDF" + (f"; found via {how_found}" if how_found else "")
    entry = {"pmid": None, "pmcid": None, "doi": nd or None, "title": title or None,
             "year": None, "journal": None, "license": None, "open_access": None,
             "in_epmc": False, "pub_types": [], "source_id": sid, "outcome": "full_text",
             "why": why, "route": LOCAL_PDF, "pdf_sha256": sha, "how_found": how_found,
             "pdf_file": str(src.resolve()), "added_at": _now()}
    if p["search"].exists():
        rec = json.loads(p["search"].read_text(encoding="utf-8"))
    else:
        rec = {"query": None, "endpoint": None, "executed_at": None, "counts": {},
               "papers": []}
    rec["papers"] = [e for e in rec.get("papers", [])
                     if not (e.get("route") == LOCAL_PDF and e.get("source_id") == sid)]
    rec["papers"].append(entry)
    rec.setdefault("counts", {})[LOCAL_PDF] = sum(1 for e in rec["papers"]
                                                  if e.get("route") == LOCAL_PDF)
    p["search"].parent.mkdir(parents=True, exist_ok=True)
    p["search"].write_text(json.dumps(rec, indent=1), encoding="utf-8")

    out = {"source_id": sid, "dir": str(d), "outcome": outcome, "pdf_sha256": sha,
           "chars": store_meta.get("chars", len((d / "full.txt").read_text(encoding="utf-8"))),
           "pages": store_meta.get("page_count"),
           "references_cut": (store_meta.get("references_cut") or {}).get("cut"),
           "pages_flagged": sorted(
               {f["page"] for key in ("interleaved_pages", "column_order_suspect_pages",
                                      "overprinted_pages")
                for f in store_meta.get(key) or [] if not f.get("recovered")})}
    if nd and nd in {_normal_doi(e.get("doi")) for e in rec["papers"]
                     if e.get("outcome") == "full_text" and e.get("route") != LOCAL_PDF}:
        out["note"] = ("this DOI is already stored as JATS full text from Europe PMC, which is "
                       "read in preference (JATS first); this PDF store will not be read")
    return out


def add_pdfs(root: str | pathlib.Path, manifest: str | pathlib.Path) -> dict:
    """`add_pdf` for every entry of a manifest: a JSON list of
    `{pdf, source_id, doi, title, how_found}`, `pdf` relative to the manifest's folder.
    One bad entry does not stop the rest; each failure is reported with its reason."""
    mpath = pathlib.Path(manifest).expanduser()
    items = json.loads(mpath.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError("a PDF manifest is a JSON list of {pdf, source_id, doi, title, "
                         "how_found}")
    added, failed = [], []
    for i, it in enumerate(items):
        pdf = pathlib.Path(str((it or {}).get("pdf") or "")).expanduser()
        if not pdf.is_absolute():
            pdf = mpath.parent / pdf
        try:
            added.append(add_pdf(root, pdf, source_id=it.get("source_id", ""),
                                 doi=it.get("doi", ""), title=it.get("title", ""),
                                 how_found=it.get("how_found", "")))
        except Exception as exc:                         # noqa: BLE001: reported, never silent
            failed.append({"entry": i, "pdf": str(pdf), "error": f"{type(exc).__name__}: {exc}"})
    return {"added": added, "failed": failed}


def readable_sources(root: str | pathlib.Path = ".") -> list:
    """Stored source ids, in the order the search returned them, then those added by hand.

    One paper, one source: where a DOI is held twice, JATS full text is read in preference to a
    PDF, and any full text in preference to an abstract. Two stores of one paper would each
    yield the same records, and the duplicates would look like independent evidence.
    """
    p = paths(root)
    if not p["search"].exists():
        return []
    rec = json.loads(p["search"].read_text(encoding="utf-8"))
    doi = lambda e: _normal_doi(e.get("doi"))                     # noqa: E731
    full = [e for e in rec["papers"] if e["outcome"] == "full_text" and doi(e)]
    jats_dois = {doi(e) for e in full if e.get("route") != LOCAL_PDF}
    full_dois = {doi(e) for e in full}
    out = []
    for e in rec["papers"]:
        if e.get("route") == LOCAL_PDF and doi(e) in jats_dois:
            continue
        if e["outcome"] == "abstract_only" and doi(e) and doi(e) in full_dois:
            continue
        if e["outcome"] in ("full_text", "abstract_only") and e["source_id"]:
            d = store_dirname(e["source_id"])
            if (p["sources"] / d / "full.txt").exists() and d not in out:
                out.append(d)
    return out


VOLATILE = ("_generated_at", "_generated_hash", "run_timestamp")


def _config_hash(cfg: dict) -> str:
    """A fingerprint of everything in a config except the parts that move every time it is
    written. It is how `plan` tells its own last output from a researcher's edits."""
    stable = {k: v for k, v in cfg.items() if k not in VOLATILE}
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _identity_declaration(tiers: list, ordinal_within: list, names: set) -> dict:
    """The ratified identity chain, in the shape `merge_passes` reads.

    A BARE FIELD NAME IS A STATEMENT ABOUT GRAIN, not a shortcut. `ensemble._identify` treats a
    chain with no `ordinal_within` as "this entity type has at most one record per identity", so
    a project whose row is a MEASUREMENT and whose identity is the molecule silently collapses
    every measurement of one molecule into one row. Measured on the first BBB wave
    (2026-09-18, PMC13532014): the same three passes merged to 3 records under a bare identity
    and 6 once the chain was written out.
    """
    for tier in tiers:
        unknown = [f for f in tier if f not in names]
        if unknown:
            raise ValueError(f"identity tier {'+'.join(tier)} names fields nobody ratified: "
                             f"{unknown} (ratified: {sorted(names)})")
    # An anchor level (`@sentence`, `@span`) scopes the ordinal by the place a record was read
    # from rather than by a field, so it names no field and is not checked against them.
    unknown = [f for f in ordinal_within if f not in names and f not in ANCHOR_LEVELS]
    if unknown:
        raise ValueError(f"--ordinal-within names fields nobody ratified: {unknown}")
    chain = [t[0] if len(t) == 1 else list(t) for t in tiers]
    return {"chain": chain, "ordinal_within": list(ordinal_within)}


def plan(root: str | pathlib.Path = ".", *, entity_type: str, identity_field: str,
         models: list, judge_model: str, hunter_model: str, judge_audit_fraction: float,
         auto_accept_threshold: float = 0.95, min_populated_fields: int = 0,
         identity_chain: Optional[list] = None, ordinal_within: Optional[list] = None,
         evidence_grounded: Optional[list] = None, grounding_mode: str = "lexical",
         grounding_model: str = "sonnet",
         force: bool = False, plugin_version: str = "", run_mode: str = "fat") -> dict:
    """Write the prompts and `run.json` for this project. Decides nothing about the science.

    Every argument without a default is a choice the researcher ratifies, which is why there is
    no default for the audit fraction (D-081) or the models. `auto_accept_threshold` keeps the
    shipped conservative placeholder, and says so in the config.

    `identity_chain` is a list of tiers, each a list of ratified field names, tried in order;
    `ordinal_within` is the scope inside which unidentified records may be aligned by position.
    Together they are how a project states a row grain finer than one row per named thing.
    """
    from .spec_context import load_spec
    p = paths(root)
    spec = load_spec(p["spec"])                          # the refusal point for anything unratified
    names = {f.name for f in spec.fields}
    if identity_field not in names:
        raise ValueError(f"identity field {identity_field!r} is not a ratified field "
                         f"(ratified: {sorted(names)})")
    if not models:
        raise ValueError("no extraction models given")
    if run_mode not in RUN_MODES:
        raise ValueError(f"run_mode is one of {sorted(RUN_MODES)}, not {run_mode!r}")
    if run_mode == "lean" and len(models) != 1:
        raise ValueError("a lean run has ONE reader (D-208); the fat run is the ensemble")
    # An EMPTY chain is a statement, not a missing argument: a claim has no identifying value
    # worth matching on, so its readings align by where they were read (`@sentence`) alone.
    tiers = [list(t) for t in (identity_chain if identity_chain is not None
                               else [[identity_field]])]
    identity = _identity_declaration(tiers, ordinal_within or [], names)
    unknown = [f for f in (evidence_grounded or []) if f not in names]
    if unknown:
        raise ValueError(f"--evidence-grounded names fields nobody ratified: {unknown}")
    if grounding_mode not in ("lexical", "llm"):
        raise ValueError("grounding_mode is 'lexical' (a substring test) or 'llm' (a model "
                         "reads the quote and says whether it supports the value)")
    papers = readable_sources(root)
    if not papers:
        raise ValueError("no readable papers are stored yet; run fetch first")

    # DO NOT SILENTLY RESTORE WHAT SOMEONE FIXED BY HAND. On the first BBB wave the identity
    # chain had to be hand-written into `run.json`, and re-running `plan` would have put the
    # collapsing single-field identity back with nothing said. A config this function wrote and
    # nobody touched carries its own fingerprint and is replaced without ceremony; an edited one
    # stops here, names what differs, and needs `--force` (which keeps a `.bak`).
    if p["config"].exists():
        previous = json.loads(p["config"].read_text(encoding="utf-8"))
        edited = previous.get("_generated_hash") != _config_hash(previous)
        if edited and not force:
            raise ValueError(
                f"{p['config']} has been edited since plan wrote it. Re-planning would "
                f"overwrite those edits. Pass them as arguments instead (identity chains go in "
                f"--identity-chain and --ordinal-within), or re-run with --force, which keeps a "
                f"copy at run.json.bak.")
        if edited:
            (p["config"].with_suffix(".json.bak")).write_text(
                json.dumps(previous, indent=1), encoding="utf-8")

    p["prompts"].mkdir(parents=True, exist_ok=True)
    for name in ("extract", "judge", "hunter"):
        body = (TEMPLATES / f"{name}.md").read_text(encoding="utf-8")
        body = body.replace("<<ENTITY_TYPE>>", entity_type) \
                   .replace("<<IDENTITY_FIELD>>", identity_field)
        (p["prompts"] / f"{name}.md").write_text(body, encoding="utf-8")

    n = len(papers)
    cfg = {
        "_generated_by": f"lit2db project plan {plugin_version}".strip(),
        "_generated_at": _now(),
        "papers": papers,
        "stores": str(p["sources"]),
        "out": str(p["run"]),
        "db_path": str(p["db"]),
        "spec": str(p["spec"]),
        "extract_prompt": str(p["prompts"] / "extract.md"),
        "judge_prompt": str(p["prompts"] / "judge.md"),
        "hunter_prompt": str(p["prompts"] / "hunter.md"),
        "models": list(models),
        "judge_model": judge_model,
        "hunter_model": hunter_model,
        "judge_audit_fraction": judge_audit_fraction,
        "auto_accept_threshold": auto_accept_threshold,
        "_threshold_note": ("UNCALIBRATED conservative placeholder unless the researcher set it "
                            "from their own reviewed records."),
        "min_populated_fields": min_populated_fields,
        "identity_fields": {entity_type: identity},
        "identity_primary": identity_field,
        "wave": "run",
        "run_timestamp": _now(),
        "weights_key": "numeric",
        # D-061: a controlled-vocabulary value is never verbatim in a paper (no source contains
        # the string "no_significant_difference"), so a lexical check scores a CORRECT label 0.0.
        # Every enum field is grounded on its anchored evidence instead. Derived from the ratified
        # field TYPES, so it adds nothing. Measured on the first clean-room run: without it all
        # ten records scored 0.3 on a unanimous, correctly quoted label.
        #
        # ENUMS ARE NOT THE ONLY VALUES A PAPER DOES NOT SPELL. Measured on the first BBB wave
        # (2026-09-18): `species = "mouse"` against a quote reading "mice", `penetration_unit =
        # "%/g"` against "2%ID/g", `molecule_key` a slug appearing nowhere. All correct, all
        # scored 0.0, and they were the weakest link in 42 of 50 records. Which fields are
        # normalized rather than transcribed is a fact about the schema that only the researcher
        # can state, so it arrives as an argument; `grounding_mode="llm"` is the other answer,
        # where a model judges support instead of a substring test (D-110/D-112).
        "evidence_grounded_fields": sorted({f.name for f in spec.fields if f.enum}
                                           | set(evidence_grounded or [])),
        "grounding_mode": grounding_mode,
        "grounding_model": grounding_model,
        # A field the researcher ratified as never-auto-acceptable is HUMAN-REVIEW OUTPUT BY
        # DESIGN, so it is scored and shown but kept out of the record composite, which is the
        # weakest link among the fields the record will be written with. Without this, one
        # prose field holds down every record carrying it and the yield reads as a broken
        # pipeline (contracts/spec.py, `auto_acceptable`).
        "review_lane": [f.name for f in spec.fields if not f.auto_acceptable],
        # The structure and taxon lookups key on field NAMES from one earlier project. Off unless
        # this spec happens to use those names, so a stranger's run never calls a chemistry API.
        "resolve_structures": "compound_name" in names,
        "producing_process": (f"lit2db{('@' + plugin_version) if plugin_version else ''}"
                              f"/extract:{'+'.join(models)}/spec@{spec.spec_version}"),
        # A brake for a runaway loop, not a budget: ~6x the 62k work tokens per paper measured on
        # the 183-paper wave, so an honest run never trips it.
        "fuse": {"max_calls": 30 * n, "max_tokens": 400_000 * n},
    }
    # D-208: the run mode, written out key by key so a config says what it does.
    cfg.update(RUN_MODES[run_mode])
    if run_mode == "lean" and not min_populated_fields:
        cfg["min_populated_fields"] = 1
        cfg["_single_pass_note"] = (
            "Lean run (D-208): one reader, and the second signal is the one-check judge, which "
            "reads every record. min_populated_fields=1 meets the single-pass floor; it is not "
            "calibrated and is not claimed to be.")
    # D-203: the uncertainty groups the spec ratified, so the gate checks what the extractor is
    # told to report. Derived from the spec, so it adds nothing; written only when there are any,
    # so a spec without them plans byte-for-byte as before.
    groups = spec.uncertainty_groups()
    if groups:
        cfg["uncertainty_groups"] = groups
    # The fields the researcher locked ("every row must have this"), so the gate holds a row
    # missing one out of the accepted table. `FieldSpec.required` reached no run until this
    # (found 2026-10-03, M12): BBB's four locked fields were reported, never enforced. Written
    # only when there are any, like the groups, so a spec with none plans as before.
    required = [f.name for f in spec.fields if f.required]
    if required:
        cfg["required_fields"] = required
    cfg["_generated_hash"] = _config_hash(cfg)
    p["config"].write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    return {"config": str(p["config"]), "papers": n,
            "identity": cfg["identity_fields"][entity_type]}


def calibrate(root: str | pathlib.Path = ".") -> dict:
    """What the researcher's own verdicts say about where the bar could sit. It never sets one.

    Confirming candidates is work a researcher was going to do anyway, and `record_adjudication`
    keeps it. This reads those verdicts back joined to the signals the gate had, and reports
    precision per bucket plus precision and yield at every threshold the observed scores can
    actually take. Choosing the operating point is a promise to whoever uses the database about
    how wrong it may be, so it stays the researcher's to ratify (`calibration.py`), and
    `cant_tell` is held out of every denominator rather than counted as wrong.
    """
    from .calibration import frontier, render, table
    from .output import adjudications
    p = paths(root)
    if not p["db"].exists():
        raise ValueError(f"no database at {p['db']}; nothing has been reviewed yet")
    adj = adjudications(str(p["db"]))
    rows = adj["adjudications"]
    if not rows:
        return {"reviewed": 0,
                "next": "Nothing has been reviewed yet. `project.py review --focus "
                        "judge-supported` puts the sharpest records in front of you first."}
    by_judge = table(rows, lambda r: r.get("judge_verdict") or "not_run")
    by_fields = table(rows, lambda r: r.get("n_populated_fields", 0))
    bars = frontier(rows, lambda r: r.get("composite_confidence"))
    return {
        "reviewed": len(rows), "verdicts": adj.get("counts", {}),
        "by_surface": adj.get("by_surface", {}),
        "tables": {"by_judge_verdict": render(by_judge, "judge verdict"),
                   "by_fields_populated": render(by_fields, "fields populated"),
                   "by_threshold": render(bars, "accept bar")},
        "frontier": [{"bar": b["bar"], "precision": b["precision"], "n": b["n_verifiable"],
                      "ci95": b["ci95"], "kept": b["n_accepted"],
                      "kept_fraction": b["kept_fraction"]} for b in bars],
        "note": ("This reports; it does not choose. Where the bar sits is a promise about how "
                 "wrong the database may be, and it is yours to ratify. Records answered "
                 "'cant tell' are held out of every denominator and counted separately."),
    }


# The PRISMA 2020 flow, in the order the diagram reads. Each stage names what it counts and
# what the count comes FROM, because a flow diagram whose numbers were typed in by hand is the
# thing this emitter exists to replace (framework-paradigm.md section 4: PRISMA covers corpus
# construction completely, we invent nothing, and the only claim we make is that the numbers came
# out of the record).
FLOW = (
    ("identified", "records returned by the ratified query", "search.json: hits"),
    ("examined", "records retrieved and assessed", "search.json: the papers examined"),
    ("excluded_not_retrievable", "no legally readable copy, so never assessed",
     "search.json: unreachable"),
    ("excluded_retracted", "retracted, refused before reading", "search.json: retracted"),
    ("assessed_full_text", "assessed as full text", "search.json: full_text"),
    ("assessed_abstract_only", "assessed as abstract only, the ratified fallback",
     "search.json: abstract_only"),
    ("identified_other_methods", "added by hand from a PDF the researcher held (PRISMA 2020: "
     "identification via other methods)", "search.json: papers with route local_pdf"),
    ("assessed_other_methods", "of those, stored and assessed as full text",
     "search.json: local_pdf papers whose store exists"),
    ("read", "actually read by the pipeline", "one scored.json per paper"),
    ("contributed_records", "papers that yielded at least one record", "the run manifest"),
    ("yielded_nothing", "read and reported nothing that qualified, a recorded negative",
     "the run manifest"),
)


def prisma(root: str | pathlib.Path = ".") -> dict:
    """The flow counts, emitted from the record rather than counted by hand.

    IT STOPS WHERE PRISMA STOPS. The diagram terminates at "studies included"; everything lit2db
    does downstream of that box (which values were admitted, which refused, and why) is reported
    separately and is not folded in here, because folding it in would quietly redefine a standard
    the field reads in one particular way (D-123).

    Every number carries the artifact it was read from, so a reader can check it rather than
    trust it, and a stage nothing can answer prints "not counted" rather than 0 (D-078's rule:
    a counter that found nothing and a list that is clear must never look the same).
    """
    p = paths(root)
    if not p["search"].exists():
        raise ValueError(f"no corpus at {p['search']}; run fetch first")
    rec = json.loads(p["search"].read_text(encoding="utf-8"))
    c = rec.get("counts") or {}
    manifest = {}
    if (p["run"] / "manifest.json").exists():
        manifest = json.loads((p["run"] / "manifest.json").read_text(encoding="utf-8"))
    per_paper = manifest.get("per_paper") or []
    done = [r for r in per_paper if r.get("status") == "done"]
    local = [e for e in rec.get("papers", []) if e.get("route") == LOCAL_PDF]

    counted = {
        "identified": c.get("hits"),
        "examined": c.get("examined"),
        "excluded_not_retrievable": c.get("unreachable", 0),
        "excluded_retracted": c.get("retracted", 0),
        "assessed_full_text": c.get("full_text", 0),
        "assessed_abstract_only": c.get("abstract_only", 0),
        "identified_other_methods": len(local),
        "assessed_other_methods": sum(
            1 for e in local
            if (p["sources"] / store_dirname(e["source_id"]) / "full.txt").exists()),
        "read": len(done) if per_paper else None,
        "contributed_records": sum(1 for r in done if r.get("n_records")) if per_paper else None,
        "yielded_nothing": sum(1 for r in done if not r.get("n_records")) if per_paper else None,
    }
    if rec.get("query") is None:
        # No query ran, so the database arm counted nothing; 0 would read as "ran and was clear".
        for key in ("excluded_not_retrievable", "excluded_retracted", "assessed_full_text",
                    "assessed_abstract_only"):
            counted[key] = None
    reasons = {}
    for e in rec.get("papers", []):
        if e.get("outcome") in ("unreachable", "retracted") and e.get("why"):
            reasons[e["why"]] = reasons.get(e["why"], 0) + 1

    if rec.get("query") is None:
        head = ["No database query has been run: every source was added by hand, so the "
                "database stages read `not counted`.", ""]
    else:
        head = [f"Query, as ratified and executed: `{rec.get('query', '')}`", "",
                f"Endpoint: {rec.get('endpoint', '')} · executed {rec.get('executed_at', '')}",
                ""]
    lines = [f"# PRISMA flow, emitted {_now()}", "", *head,
             "| stage | n | what it counts | read from |", "|---|---:|---|---|"]
    for key, what, source in FLOW:
        n = counted.get(key)
        lines.append(f"| {key.replace('_', ' ')} | {'not counted' if n is None else n} | "
                     f"{what} | {source} |")
    if reasons:
        lines += ["", "## Why records were excluded before assessment", "",
                  "| reason | n |", "|---|---:|"]
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {why} | {n} |")
    lines += ["", "## What this does not cover", "",
              "PRISMA's flow terminates at the studies included. What each admitted paper then "
              "yielded, and which values were written, held or refused, is in `candidates.csv`, "
              "`accepted.csv` and the run's questions, and is deliberately not folded into the "
              "numbers above.", ""]
    if not per_paper:
        lines += ["No run has been scored yet, so the reading stages read `not counted` rather "
                  "than 0: nothing has been read, which is not the same as nothing being "
                  "readable.", ""]

    p["results"].mkdir(parents=True, exist_ok=True)
    (p["results"] / "prisma.md").write_text("\n".join(lines), encoding="utf-8")
    (p["results"] / "prisma-flow.json").write_text(json.dumps(
        {"query": rec.get("query"), "endpoint": rec.get("endpoint"),
         "executed_at": rec.get("executed_at"), "emitted_at": _now(),
         "counts": counted, "exclusion_reasons": reasons,
         "sources": {k: src for k, _, src in FLOW}}, indent=1), encoding="utf-8")
    return {"counts": counted, "exclusion_reasons": reasons,
            "written": [str(p["results"] / "prisma.md"), str(p["results"] / "prisma-flow.json")]}


def _field_columns(payload: dict) -> dict:
    row = {}
    for f in payload.get("fields") or []:
        name = f.get("field_name")
        if not name:
            continue
        value = f.get("value")
        row[name] = json.dumps(value) if isinstance(value, (list, dict)) else value
        quote = (f.get("provenance") or {}).get("verbatim_quote")
        if quote:
            row[f"{name} (quote)"] = quote
    return row


def _write_csv(path: pathlib.Path, rows: list, lead: list) -> int:
    cols = list(lead)
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def export(root: str | pathlib.Path = ".") -> dict:
    """Three spreadsheets, in the order a status report gives them: the papers, the candidates
    waiting for a human, the accepted rows. Never the last alone (see `/lit2db-status`)."""
    import sqlite3
    p = paths(root)
    out = {}

    papers = json.loads(p["search"].read_text(encoding="utf-8"))["papers"] \
        if p["search"].exists() else []
    tables = {"records": [], "candidates": [], "adjudications": []}
    if p["db"].exists():
        con = sqlite3.connect(str(p["db"]))
        con.row_factory = sqlite3.Row
        have = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        for t in tables:
            if t in have:
                tables[t] = [dict(r) for r in con.execute(f"select * from {t}")]
        con.close()

    verdicts = {(a["source_id"], a["record_id"]): a for a in tables["adjudications"]}
    per_source = {}
    for c in tables["candidates"]:
        per_source.setdefault(c["source_id"], {"found": 0, "accepted": 0})
        per_source[c["source_id"]]["found"] += 1
        if c["decision"] == "allow":
            per_source[c["source_id"]]["accepted"] += 1

    paper_rows = []
    for e in papers:
        sid = store_dirname(e["source_id"]) if e.get("source_id") else ""
        ran = bool(sid) and (p["run"] / sid / "scored.json").exists()
        counts = per_source.get(sid, {})
        paper_rows.append({
            "title": e.get("title"), "year": e.get("year"), "journal": e.get("journal"),
            "doi": e.get("doi"), "pmid": e.get("pmid"), "pmcid": e.get("pmcid"),
            "licence": e.get("license"), "what we could read": e.get("outcome"),
            "why": e.get("why"), "read by the pipeline": "yes" if ran else "no",
            "values found": counts.get("found", 0) if ran else "",
            "values accepted": counts.get("accepted", 0) if ran else "",
        })
    out["papers"] = _write_csv(p["results"] / "papers.csv", paper_rows,
                               ["title", "year", "journal", "doi", "pmid", "pmcid"])

    cand_rows = []
    for c in tables["candidates"]:
        if c["decision"] == "allow":
            continue
        payload = json.loads(c["payload_json"] or "{}")
        v = verdicts.get((c["source_id"], c["record_id"]))
        cand_rows.append({
            "source": c["source_id"], "record": c["record_id"],
            "confidence": c["composite_confidence"],
            "why it was not accepted": " | ".join(json.loads(c["reasons_json"] or "[]")),
            "fact-check verdict": c["judge_verdict"],
            "your verdict": (v or {}).get("verdict", ""), "your note": (v or {}).get("note", ""),
            **_field_columns(payload)})
    out["candidates"] = _write_csv(p["results"] / "candidates.csv", cand_rows,
                                   ["source", "record", "confidence", "why it was not accepted"])

    acc_rows = []
    for r in tables["records"]:
        payload = json.loads(r["payload_json"] or "{}")
        source = r["record_id"].split(":", 1)[0] if ":" in r["record_id"] else ""
        acc_rows.append({"source": source, "record": r["record_id"],
                         "confidence": r["composite_confidence"], **_field_columns(payload)})
    out["accepted"] = _write_csv(p["results"] / "accepted.csv", acc_rows,
                                 ["source", "record", "confidence"])

    # A review project also gets its themed reading order, from the same claims. Written here
    # rather than only on demand so the three tiers and the grouping can never describe
    # different states of the database.
    from . import themes
    if p["spec"].exists():
        try:
            body = themes.markdown(str(p["db"]), str(p["spec"]))
        except (ValueError, KeyError):
            body = None                      # no ratified theme set: not a review project
        if body:
            (p["results"] / "themes.md").write_text(body, encoding="utf-8")
            out["themes"] = str(p["results"] / "themes.md")
    out["folder"] = str(p["results"])
    return out
