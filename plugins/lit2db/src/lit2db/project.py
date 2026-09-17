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
around a paywall: full text comes only from Europe PMC's open-access set, an abstract only when
the caller says the ratified scope admits abstracts, and everything else is listed as
unreachable rather than dropped. And it records a retracted source as retracted and does not
read it, because the wave driver stamps every source it is handed `active`.

Stdlib only, like `store` and `gate`: this runs before anything else is known to work.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import ssl
import time
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .store import build_from_abstract, build_from_jats, store_dirname, write_store

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
    record = {"query": query, "endpoint": EPMC, "executed_at": retrieved_at,
              "limit": limit, "allow_abstracts": allow_abstracts, "counts": counts,
              "papers": papers}
    p["search"].parent.mkdir(parents=True, exist_ok=True)
    p["search"].write_text(json.dumps(record, indent=1), encoding="utf-8")
    return {"counts": counts, "search": str(p["search"])}


def readable_sources(root: str | pathlib.Path = ".") -> list:
    """Stored source ids, in the order the search returned them."""
    p = paths(root)
    if not p["search"].exists():
        return []
    rec = json.loads(p["search"].read_text(encoding="utf-8"))
    out = []
    for e in rec["papers"]:
        if e["outcome"] in ("full_text", "abstract_only") and e["source_id"]:
            d = store_dirname(e["source_id"])
            if (p["sources"] / d / "full.txt").exists() and d not in out:
                out.append(d)
    return out


def plan(root: str | pathlib.Path = ".", *, entity_type: str, identity_field: str,
         models: list, judge_model: str, hunter_model: str, judge_audit_fraction: float,
         auto_accept_threshold: float = 0.95, min_populated_fields: int = 0,
         plugin_version: str = "") -> dict:
    """Write the prompts and `run.json` for this project. Decides nothing about the science.

    Every argument without a default is a choice the researcher ratifies, which is why there is
    no default for the audit fraction (D-081) or the models. `auto_accept_threshold` keeps the
    shipped conservative placeholder, and says so in the config.
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
    papers = readable_sources(root)
    if not papers:
        raise ValueError("no readable papers are stored yet; run fetch first")

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
        "identity_fields": {entity_type: identity_field},
        "identity_primary": identity_field,
        "wave": "run",
        "run_timestamp": _now(),
        "weights_key": "numeric",
        # D-061: a controlled-vocabulary value is never verbatim in a paper (no source contains
        # the string "no_significant_difference"), so a lexical check scores a CORRECT label 0.0.
        # Every enum field is grounded on its anchored evidence instead. Derived from the ratified
        # field TYPES, so it adds nothing. Measured on the first clean-room run: without it all
        # ten records scored 0.3 on a unanimous, correctly quoted label.
        "evidence_grounded_fields": [f.name for f in spec.fields if f.enum],
        # The structure and taxon lookups key on field NAMES from one earlier project. Off unless
        # this spec happens to use those names, so a stranger's run never calls a chemistry API.
        "resolve_structures": "compound_name" in names,
        "producing_process": (f"lit2db{('@' + plugin_version) if plugin_version else ''}"
                              f"/extract:{'+'.join(models)}/spec@{spec.spec_version}"),
        # A brake for a runaway loop, not a budget: ~6x the 62k work tokens per paper measured on
        # the 183-paper wave, so an honest run never trips it.
        "fuse": {"max_calls": 30 * n, "max_tokens": 400_000 * n},
    }
    p["config"].write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    return {"config": str(p["config"]), "papers": n}


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
    out["folder"] = str(p["results"])
    return out
