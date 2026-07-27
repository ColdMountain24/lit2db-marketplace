#!/usr/bin/env python3
"""Drive a whole wave headlessly: spawn every agent, run the spine, stop for the researcher.

`run_corpus.py` prepares per-paper task files and aggregates afterward; the agent work between
them ran through a human-attended session. That does not scale — 137 papers is ~1,650 agent
invocations, and an orchestrator holding all of it in one context fills up around paper fifteen.
This is the missing half: it spawns the agents itself, runs the deterministic spine in-process,
and survives being left alone overnight.

WHAT IT WILL NOT DO. It never adjudicates. Every verification decision still belongs to the
spine (`ground_literature` -> `score_and_route` -> `gate_upsert`) and every scientific decision
still belongs to the researcher. Where the run encounters a question only a researcher can
answer, it writes it to QUESTIONS.jsonl and keeps going — it does not guess, and it does not
quietly pick a side. A wave that ends with an empty catalogue and a wave that ends with forty
open questions are different results, and the difference must be visible.

FOUR PROPERTIES IT EXISTS TO GUARANTEE

  RESUMABLE at the paper boundary. A paper whose `scored.json` exists is skipped. There is
  deliberately no cache of model OUTPUT: the k passes must stay independent, and a cache keyed
  on (source, prompt, model) would hand all three passes the same answer and manufacture
  unanimity out of a cache hit.

  PATIENT WITH USAGE LIMITS. Hitting a limit is normal on a long run, not an error. The driver
  parses the reset time when the CLI reports one, sleeps until then, and resumes — rather than
  burning the remaining papers against a wall or, worse, recording their failures as findings.

  HONEST WHEN IT DEGRADES. A pass that fails after retries is recorded as a FAILED pass, never
  silently dropped. Dropping it would shrink k and turn "two models could not be reached" into
  "two models agreed", which is a fabricated consensus — the exact failure the ensemble exists
  to prevent.

  QUESTION-GATED BETWEEN WAVES. Scope disagreements the ensemble surfaces are catalogued as
  they occur. The next wave is not meant to start until they are answered, because running
  wave 2 under a schema wave 1 already showed to be wrong just buys more records to throw away.

    python3 scripts/run_wave.py --config wave.json --dry-run     # plan + budget, no calls
    caffeinate -is python3 scripts/run_wave.py --config wave.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from lit2db.ensemble import merge_passes                      # noqa: E402
from lit2db.fuse import Fuse, FuseExceeded                    # noqa: E402
from lit2db.store import find_spans, section_of               # noqa: E402
from lit2db.contracts.provenance import process_fingerprint   # noqa: E402

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
_log_lock = threading.Lock()

# A judge verdict is an ordinal, not a probability. PARTIAL sits below any sane auto-accept bar
# on purpose: "the core is right but something over-reaches" is precisely the case a human
# should see, and rounding it up would remove the judge from the accept decision entirely.
VERDICT_TO_C = {"SUPPORTED": 1.0, "PARTIAL": 0.5, "UNSUPPORTED": 0.0}


def _extract_json(text: str) -> dict | None:
    """Pull one JSON object out of a model reply.

    Tried in order: a fenced ```json block, the widest brace span, then each brace span from
    the outside in. A single greedy `{...}` fails whenever the reply also contains prose with
    braces, and a bare `json.loads` fails on the fenced form models most often produce.
    """
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    candidates = ([fenced.group(1)] if fenced else [])
    span = re.search(r"\{[\s\S]*\}", text)
    if span:
        candidates.append(span.group(0))
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    return None


def log(msg: str) -> None:
    with _log_lock:
        print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------------------------------
# Talking to the model
# ---------------------------------------------------------------------------------------
_LIMIT_RE = re.compile(r"(usage limit|rate.?limit|resets? at|try again)", re.I)
_RESET_RE = re.compile(r"resets? at ([0-9]{1,2}):([0-9]{2})\s*(am|pm)?", re.I)


def _seconds_until_reset(text: str, default: int) -> int:
    """Parse a reset time out of the CLI's message; fall back to a fixed backoff.

    Sleeping to the stated reset beats exponential backoff here: the limit is a wall clock, so
    doubling delays either wakes far too early (wasting retries) or far too late (wasting the
    night). If nothing parses, the caller's default applies.
    """
    m = _RESET_RE.search(text or "")
    if not m:
        return default
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    now = dt.datetime.now()
    target = now.replace(hour=hour % 24, minute=minute, second=30, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return max(60, int((target - now).total_seconds()))


def call_agent(prompt: str, model: str, *, label: str, cwd: pathlib.Path,
               fuse: Fuse, stage: str = "", read_dirs: tuple = (),
               timeout: int = 1800, retries: int = 3, backoff: int = 900) -> dict:
    """One headless agent invocation. Returns {ok, text, tokens, attempts}.

    `read_dirs` MUST include the source store. A headless agent cannot pause to ask for a
    permission grant, so a path it was not given is simply unreadable — and the first live run
    of this driver failed exactly there: all three passes ran, could not open `full.txt`, and
    refused to emit records rather than invent them ("I won't emit records I can't ground").
    The contract held; the plumbing did not. Granting the directory is the fix, and the failure
    being loud rather than empty is why it took one run to find instead of a whole wave.
    """
    cmd = ["claude", "-p", prompt, "--model", model,
           "--output-format", "json", "--permission-mode", "acceptEdits"]
    for d in (str(cwd), *(str(x) for x in read_dirs)):
        cmd += ["--add-dir", d]
    last = ""
    for attempt in range(1, retries + 1):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout, cwd=str(cwd))
        except subprocess.TimeoutExpired:
            last = f"timeout after {timeout}s"
            log(f"    {label}: {last} (attempt {attempt}/{retries})")
            continue
        blob = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            usage = {}
            try:
                data = json.loads(r.stdout)
                text = data.get("result") or data.get("text") or ""
                usage = data.get("usage") or {}
            except (json.JSONDecodeError, TypeError, ValueError):
                text = r.stdout
            # Hand the fuse the RAW usage mapping: it keeps input/output/cache_read/cache_write
            # apart, and collapsing them to one integer here would throw away the cache split
            # that makes a long run's cost legible.
            norm = fuse.record(usage, unit=label, stage=stage)
            return {"ok": True, "text": text, "tokens": sum(norm.values()),
                    "attempts": attempt}
        last = blob.strip()[-400:]
        if _LIMIT_RE.search(blob):
            nap = _seconds_until_reset(blob, backoff)
            log(f"    {label}: usage limit — sleeping {nap // 60}m, then retrying")
            time.sleep(nap)
            continue
        log(f"    {label}: exit {r.returncode} (attempt {attempt}/{retries}) {last[:120]}")
        time.sleep(10 * attempt)
    return {"ok": False, "text": last, "tokens": 0, "attempts": retries}


# ---------------------------------------------------------------------------------------
# The per-paper pipeline
# ---------------------------------------------------------------------------------------
def run_passes(paper: str, cfg: dict, pdir: pathlib.Path, fuse: Fuse) -> tuple[list, list]:
    """k independent extraction passes, one model each (D-053), run concurrently."""
    store = pathlib.Path(cfg["stores"]) / paper
    template = pathlib.Path(cfg["extract_prompt"]).read_text(encoding="utf-8")
    models = cfg["models"]

    def one(i: int) -> tuple[int, dict]:
        out_dir = pdir / f"pass{i + 1}"
        out_dir.mkdir(parents=True, exist_ok=True)
        prompt = (template
                  .replace("{STORE}", str(store))
                  .replace("{OUT_DIR}", str(out_dir))
                  .replace("{WRITES}", f"pass{i + 1}.json")
                  + "\n\nYou are ONE independent pass of an ensemble. Do not read any other "
                    "pass's output directory. Write only to your own out_dir.")
        return i, call_agent(prompt, models[i], label=f"{paper} pass{i + 1}/{models[i]}",
                             cwd=pdir, fuse=fuse, stage="extract", read_dirs=(store,))

    passes, failures, completed = [None] * len(models), [], [False] * len(models)
    with cf.ThreadPoolExecutor(max_workers=len(models)) as ex:
        for i, res in ex.map(one, range(len(models))):
            f = pdir / f"pass{i + 1}" / f"pass{i + 1}.json"
            if res["ok"] and f.exists():
                try:
                    passes[i] = json.loads(f.read_text())["records"]
                    completed[i] = True          # zero records is a RESULT, not a failure
                    continue
                except (json.JSONDecodeError, KeyError) as exc:
                    res = {"ok": False, "text": f"unparseable output: {exc}"}
            # A failed pass is RECORDED, never dropped. Silently shrinking k would turn
            # "this model could not be reached" into "the models agreed".
            failures.append({"pass": i + 1, "model": models[i], "why": res["text"][:200]})
            passes[i] = []
    return passes, failures, completed


def assemble(paper: str, cfg: dict, merged: dict, judge: dict, hunt: dict) -> tuple[list, list]:
    """Merged values + provenance + judge + hunter -> records the spine can score.

    `merge_passes` returns values WITHOUT provenance — it computes agreement, not evidence — so
    each modal value's quote is re-attached from whichever pass produced it, and the offset is
    resolved from the store rather than trusted from an agent.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("srv", PLUGIN / "mcp/lit2db_mcp/server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)

    store = pathlib.Path(cfg["stores"]) / paper
    full = (store / "full.txt").read_text(encoding="utf-8")
    sdict = {"full_text": full, "sections": json.loads((store / "sections.json").read_text())}
    fingerprint = process_fingerprint(
        pathlib.Path(cfg["extract_prompt"]).read_text(encoding="utf-8"))
    passes = merged["_passes"]

    def quote_for(acc, name, value):
        for p in passes:
            for rec in p or []:
                a = next((f.get("value") for f in rec["fields"]
                          if f["field_name"] == cfg["identity_primary"]), None)
                if (a or "").lower() != (acc or "").lower():
                    continue
                for f in rec["fields"]:
                    if f["field_name"] == name and f.get("value") == value:
                        return f.get("verbatim_quote")
        return None

    out, dropped = [], []
    for rec in merged["records"]:
        acc = next((f.get("value") for f in rec["fields"]
                    if f["field_name"] == cfg["identity_primary"]), None)
        fields = []
        for f in rec["fields"]:
            name, value = f["field_name"], f.get("value")
            if value is None:
                continue
            quote = quote_for(acc, name, value)
            hits = find_spans(full, quote) if quote else []
            if not hits:
                # An unanchorable quote is a real outcome: the value does not get written on it.
                dropped.append({"record": rec["record_id"], "field": name,
                                "why": "no quote" if not quote else "quote not in full.txt"})
                continue
            off = hits[0]["start"]
            cc = dict(f.get("confidence_components") or {})
            if name in cfg.get("evidence_grounded_fields", []):
                # D-061: a controlled-vocabulary value is never verbatim in a paper — no source
                # contains the string "biochemically_characterized". Grounding it lexically
                # scores a CORRECT value 0.0. So ground the evidence instead: reaching this
                # line means the quote anchored in full.txt, and whether that quote supports
                # the classification is the judge's call, not a substring test's.
                cc["c_grounded"] = 1.0
                cc["_grounding_mode"] = "evidence_anchored"
            else:
                cc["c_grounded"] = srv.ground_literature(value=value,
                                                        quote=quote)["c_grounded"]
            v = judge.get(rec["record_id"])
            if v:
                cc["c_judge"] = VERDICT_TO_C[v]
            fv = {"field_name": name, "value": value, "confidence_components": cc,
                  "provenance": {
                      "kind": "literature", "source_id": paper,
                      "retrieval_timestamp": cfg["run_timestamp"],
                      "producing_process": cfg["producing_process"],
                      "process_fingerprint": fingerprint,
                      "verbatim_quote": quote, "char_offset": off,
                      "section": section_of(sdict, off) or "unknown",
                      "source_status": "active",
                      "source_chars_total": len(full), "source_chars_read": len(full)}}
            # PER FIELD, not per paper. `contradiction_search` describes THIS value: `found`
            # only when a span argues against this field, `clean` when the hunter searched and
            # nothing did. Propagating the paper-level verdict to every field made one real
            # contradiction mark all ten fields `found`, which the gate then blocked with
            # "not searched is not clean" — a message that is simply untrue of a field the
            # hunter did read. `not_run` stays reserved for a hunter that never completed.
            searched = hunt["state_by_record"].get(rec["record_id"], "not_run")
            evid = []
            for c in hunt.get("contradictions", []):
                if c.get("applies_to") != [rec["record_id"], name]:
                    continue
                h = find_spans(full, c.get("verbatim_quote", ""))
                if not h:                      # never fabricate an anchor
                    dropped.append({"record": rec["record_id"], "field": name,
                                    "why": "hunter quote unanchorable"})
                    continue
                evid.append({"verbatim_quote": c["verbatim_quote"],
                             "char_offset": h[0]["start"], "kind": c["kind"],
                             "explanation": c["explanation"]})
            if evid:
                fv["contradictions"] = evid
            fv["contradiction_search"] = (
                "not_run" if searched == "not_run" else ("found" if evid else "clean"))
            fields.append(fv)
        if fields:
            out.append({"record_id": rec["record_id"], "entity_type": rec["entity_type"],
                        "fields": fields})
    return out, dropped


def catalogue_questions(paper: str, merged: dict, failures: list, dropped: list) -> list:
    """Deterministic signals that a RESEARCHER, not the pipeline, has to resolve.

    Everything here is a fact about the run, not an opinion about the chemistry. The head
    session turns these into questions in the researcher's own language; this function only
    guarantees nothing gets silently decided while nobody is watching.
    """
    qs = []
    for a in merged.get("alignment", []):
        if a["found_by_passes"] == 1:
            qs.append({"paper": paper, "kind": "scope_disagreement",
                       "detail": f"{a['identity']} was found by only one of the readings",
                       "identity_tier": a["identity_tier"]})
        elif a["identity_tier"] == "ordinal":
            qs.append({"paper": paper, "kind": "weak_identity",
                       "detail": f"{a['identity']} was matched only by order of appearance",
                       "identity_tier": a["identity_tier"]})
    for name, rep in (merged.get("ensemble") or {}).items():
        if rep.get("ambiguous_modal"):
            qs.append({"paper": paper, "kind": "no_consensus_value",
                       "detail": f"{name} split with no majority", "groups": rep.get("groups")})
    for f in failures:
        qs.append({"paper": paper, "kind": "pass_failed",
                   "detail": f"pass {f['pass']} ({f['model']}) did not complete: {f['why']}"})
    for d in dropped:
        if d["why"] != "no quote":
            qs.append({"paper": paper, "kind": "unanchorable_quote", "detail": str(d)})
    return qs


def do_paper(paper: str, cfg: dict, out: pathlib.Path, fuse: Fuse) -> dict:
    pdir = out / paper
    pdir.mkdir(parents=True, exist_ok=True)
    if (pdir / "scored.json").exists():
        return {"paper": paper, "status": "skipped"}

    t0 = time.time()
    log(f"{paper}: {len(cfg['models'])} extraction passes")
    passes, failures, completed = run_passes(paper, cfg, pdir, fuse)
    if not any(completed):
        # Nothing RAN. Leave it unscored so a retry picks it up.
        log(f"{paper}: every pass failed to run — leaving unscored for retry")
        return {"paper": paper, "status": "all_passes_failed", "failures": failures}
    if not any(passes):
        # Every pass ran and unanimously found nothing. That is a legitimate result and a
        # real datum — "we looked and there is nothing here" is not "we could not look", and
        # conflating them would make the driver retry sound papers forever while reporting a
        # broken run. Measured on PMC10019314: a genome announcement that predicts terpene
        # gene clusters but characterises no enzyme, so all three passes correctly returned
        # zero records. It is scored (as empty) so resume does not revisit it.
        log(f"{paper}: unanimous empty extraction — recorded as a negative result")
        (pdir / "scored.json").write_text(json.dumps({"records": [], "gate": []}, indent=1))
        return {"paper": paper, "status": "done", "n_records": 0, "n_written": 0,
                "failures": failures, "dropped": [],
                "questions": [{"paper": paper, "kind": "empty_extraction",
                               "detail": "all readings found no records; the screen admitted "
                                         "this paper but it may not report the entity"}]}

    merged = merge_passes(passes, identity_fields=cfg["identity_fields"])
    merged["_passes"] = passes
    (pdir / "merged.json").write_text(json.dumps(
        {k: v for k, v in merged.items() if k != "_passes"}, indent=1))
    log(f"{paper}: {[len(p) for p in passes]} -> {len(merged['records'])} records "
        f"{merged['identity_tiers']}")

    # --- judge per record, hunter per paper (D-036) ------------------------------------
    jt = pathlib.Path(cfg["judge_prompt"]).read_text(encoding="utf-8")
    store = pathlib.Path(cfg["stores"]) / paper

    def judge_one(rec):
        claim = "; ".join(f"{f['field_name']} = {f.get('value')}" for f in rec["fields"]
                          if f.get("value") is not None)
        p = jt.replace("{STORE}", str(store)).replace("{CLAIM}", claim)
        r = call_agent(p, cfg["judge_model"], label=f"{paper} judge/{rec['record_id']}",
                       cwd=pdir, fuse=fuse, stage="judge", read_dirs=(store,))
        m = re.search(r'"verdict"\s*:\s*"(SUPPORTED|PARTIAL|UNSUPPORTED)"', r["text"] or "")
        return rec["record_id"], (m.group(1) if m else None)

    verdicts = {}
    with cf.ThreadPoolExecutor(max_workers=cfg.get("judge_concurrency", 4)) as ex:
        for rid, v in ex.map(judge_one, merged["records"]):
            if v:
                verdicts[rid] = v
    log(f"{paper}: judged {len(verdicts)}/{len(merged['records'])}")

    hp = pathlib.Path(cfg["hunter_prompt"]).read_text(encoding="utf-8")
    values = json.dumps([{ "record_id": r["record_id"],
                           "fields": {f["field_name"]: f.get("value") for f in r["fields"]}}
                         for r in merged["records"]], indent=1)
    hr = call_agent(hp.replace("{STORE}", str(store)).replace("{VALUES}", values),
                    cfg["hunter_model"], label=f"{paper} hunter", cwd=pdir, fuse=fuse,
                    stage="hunter", read_dirs=(store,))
    hunt = {"state_by_record": {}, "contradictions": []}
    parsed = _extract_json(hr["text"] or "")
    if parsed:
        state = parsed.get("contradiction_search", "not_run")
        hunt["state_by_record"] = {r["record_id"]: state for r in merged["records"]}
        hunt["contradictions"] = parsed.get("contradictions", [])
    else:
        # Fails closed to 'not_run', which BLOCKS every record — correct, but indistinguishable
        # from a hunter that genuinely did not run unless the raw reply is kept. The first live
        # run lost a whole paper's gating to an unparsed reply with nothing on disk to explain
        # why, so the response is always written now.
        log(f"{paper}: hunter reply did not parse — every field stays 'not_run' (blocking)")
    (pdir / "hunter.json").write_text(json.dumps(hunt, indent=1))
    (pdir / "hunter_raw.txt").write_text(hr["text"] or "(no output)")

    # --- spine: score, route, gate ------------------------------------------------------
    import importlib.util
    spec = importlib.util.spec_from_file_location("srv", PLUGIN / "mcp/lit2db_mcp/server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)

    records, dropped = assemble(paper, cfg, merged, verdicts, hunt)
    scored, written = [], 0
    for r in records:
        sr = srv.score_and_route(record=r, weights_key=cfg.get("weights_key", "numeric"),
                                 ensemble_k=len(cfg["models"]), ensemble_min_agreeing=0)
        comp = sr.get("_composite_confidence") or 0.0
        g = srv.gate_upsert(record=sr, composite_confidence=comp, db_path=cfg["db_path"],
                            autoaccept=cfg["auto_accept_threshold"],
                            require_contradiction_search=True)
        written += bool(g.get("written"))
        scored.append({"record": sr, "composite": comp, "gate": g})

    (pdir / "scored.json").write_text(json.dumps({"records": [s["record"] for s in scored],
                                                  "gate": [{"record_id": s["record"]["record_id"],
                                                            "composite": s["composite"],
                                                            "gate": s["gate"]} for s in scored]},
                                                 indent=1))
    qs = catalogue_questions(paper, merged, failures, dropped)
    log(f"{paper}: {written}/{len(scored)} written, {len(qs)} question(s), "
        f"{time.time() - t0:.0f}s")
    return {"paper": paper, "status": "done", "n_records": len(scored), "n_written": written,
            "failures": failures, "dropped": dropped, "questions": qs}


# ---------------------------------------------------------------------------------------
def wait_for_offpeak(start_hour: int | None) -> None:
    """Hold until the configured local hour. Long runs are cheapest and least disruptive
    overnight, and a driver that starts itself is one less thing to remember at midnight."""
    if start_hour is None:
        return
    now = dt.datetime.now()
    target = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    wait = (target - now).total_seconds()
    if wait > 60:
        log(f"off-peak start {start_hour:02d}:00 — sleeping {wait / 3600:.1f}h")
        time.sleep(wait)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="wave config JSON")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--start-hour", type=int, help="hold until this local hour (0-23)")
    a = ap.parse_args()

    cfg = json.loads(pathlib.Path(a.config).read_text())
    out = pathlib.Path(cfg["out"]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    papers = cfg["papers"] if isinstance(cfg["papers"], list) else \
        json.loads(pathlib.Path(cfg["papers"]).read_text())
    if a.limit:
        papers = papers[:a.limit]
    todo = [p for p in papers if not (out / p / "scored.json").exists()]

    fuse = Fuse(label=f"wave:{out.name}")
    fuse.raise_ceiling(max_calls=cfg["fuse"]["max_calls"],
                       max_tokens_total=cfg["fuse"]["max_tokens"],
                       reason=f"{len(papers)}-paper wave")

    log(f"wave '{out.name}': {len(papers)} papers, {len(todo)} to run "
        f"({len(papers) - len(todo)} already done)")
    log(f"models {cfg['models']} | judge {cfg['judge_model']} | threshold "
        f"{cfg['auto_accept_threshold']} | db {cfg['db_path']}")
    log(f"fuse {fuse.max_calls:,} calls / {fuse.max_tokens_total:,} tokens")
    if a.dry_run:
        log("DRY RUN — no model calls made")
        return 0

    wait_for_offpeak(a.start_hour)
    results, questions = [], []
    try:
        for n, p in enumerate(todo, 1):
            log(f"--- paper {n}/{len(todo)} ---")
            r = do_paper(p, cfg, out, fuse)
            results.append(r)
            questions.extend(r.get("questions", []))
            _write_manifest(out, cfg, papers, results, questions, fuse, complete=False)
    except FuseExceeded as exc:
        log(f"FUSE TRIPPED — {exc}")
    except KeyboardInterrupt:
        log("interrupted — progress is on disk, re-run to resume")

    _write_manifest(out, cfg, papers, results, questions, fuse, complete=True)
    done = [r for r in results if r["status"] == "done"]
    log(f"WAVE END: {len(done)} papers, "
        f"{sum(r['n_written'] for r in done)} records written, "
        f"{len(questions)} questions catalogued")
    if questions:
        log("Answer the catalogue before starting the next wave — see QUESTIONS.jsonl")
    return 0


def _write_manifest(out, cfg, papers, results, questions, fuse, *, complete):
    done = [r for r in results if r["status"] == "done"]
    (out / "manifest.json").write_text(json.dumps({
        "complete": complete, "config": cfg,
        "n_papers": len(papers), "n_done": len(done),
        "n_records": sum(r["n_records"] for r in done),
        "n_written": sum(r["n_written"] for r in done),
        "n_questions": len(questions),
        "papers_with_failed_passes": [r["paper"] for r in results if r.get("failures")],
        "fuse": fuse.snapshot(), "per_paper": results,
    }, indent=1) + "\n")
    with (out / "QUESTIONS.jsonl").open("w") as fh:
        for q in questions:
            fh.write(json.dumps(q) + "\n")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FuseExceeded as exc:
        print(f"\nRUN STOPPED — {exc}", file=sys.stderr)
        sys.exit(2)
