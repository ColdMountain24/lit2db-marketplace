#!/usr/bin/env python3
"""Drive a whole wave headlessly: spawn every agent, run the spine, stop for the researcher.

The first runner prepared per-paper task files and aggregated afterward, with the agent work
between them running through a human-attended session (removed in v0.29.0; see git history).
That does not scale — 137 papers is ~2,600 agent invocations, and an orchestrator holding all of
it in one context fills up around paper fifteen. This spawns the agents itself, runs the
deterministic spine in-process, and survives being left alone overnight.

Its companion is `replay.py`, which re-runs everything downstream of extraction over saved
passes with no model calls. Reach for that first: every spine defect found so far was
reproducible from artifacts already on disk.

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
import hashlib
import json
import math
import pathlib
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from lit2db.accounting import STREAMS, RunAccount             # noqa: E402
from lit2db.ensemble import merge_passes                      # noqa: E402
from lit2db.fuse import Fuse, FuseExceeded                    # noqa: E402
from lit2db.gate import single_pass_problems                   # noqa: E402
from lit2db.output import record_candidate, upsert
from lit2db.structures import resolve_structure, structure_fields            # noqa: E402
from lit2db.scoring import score_and_route                    # noqa: E402
from lit2db.contracts import DEFAULT_WEIGHTS, required_agreement  # noqa: E402
# THE PIPELINE ITSELF NOW LIVES IN THE LIBRARY. It used to live here, and scoring/gating were
# reached by loading the MCP server file as a module — so the code that ran and the code that
# shipped were two different things. This file is a DRIVER now: config, resume, manifests, token
# accounting, concurrency, and sleeping through usage limits. Nothing below decides anything
# about a record.
from lit2db.pipeline import (                                 # noqa: E402
    DENIAL_PROCESS, VERDICT_TO_STATE, _extract_json, _parse_verdicts,
    apply_verdicts, assemble, catalogue_questions, select_for_judging)

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
_log_lock = threading.Lock()
# The gate writes SQLite. With paper_concurrency > 1 several papers reach the gate at once,
# and concurrent writers to one SQLite file produce "database is locked" — which would surface
# as a gate DENIAL, i.e. a paper silently losing records to a plumbing fault while every
# artifact says the pipeline worked. Serializing the write is cheap: the gate is milliseconds
# against agent calls measured in minutes.
_gate_lock = threading.Lock()

def log(msg: str) -> None:
    with _log_lock:
        print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)



def _resolve_structures_for(scored: list, cfg: dict, pdir: pathlib.Path) -> None:
    """Attach resolved structure fields to each record, in place. Never raises.

    Failure is a NON-ANSWER, not an exception: an unresolvable name simply contributes no
    structure fields (`structure_fields` returns []), which is what D-083 requires. Resolutions
    are cached per name within a paper because the same compound is often reported twice.
    """
    seen: dict = {}
    log_path = pdir / "structures.json"
    audit = []
    for s in scored:
        rec = s["record"]
        name = next((f.get("value") for f in rec.get("fields", [])
                     if f.get("field_name") == "compound_name"), None)
        if not name:
            continue
        if name not in seen:
            try:
                seen[name] = resolve_structure(name)
            except Exception as exc:                      # noqa: BLE001 — never kill a paper
                seen[name] = {"resolved": False, "why": f"{type(exc).__name__}: {exc}"}
            time.sleep(0.2)                                # public endpoint, be polite
        res = seen[name]
        try:
            extra = structure_fields(res, rec["fields"][0]["provenance"]["source_id"],
                                     cfg["producing_process"])
        except Exception:                                  # noqa: BLE001
            extra = []
        rec.setdefault("fields", []).extend(extra)
        audit.append({"record_id": rec["record_id"], "name": name,
                      "resolved": bool(res.get("resolved")),
                      "inchikey": res.get("inchikey"), "why": res.get("why")})
    if audit:
        log_path.write_text(json.dumps(audit, indent=1))



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
               fuse: Fuse, stage: str = "", unit: str = "", read_dirs: tuple = (),
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
            # A TIMEOUT IS NOT RETRIED. This is the real fix for the hang v0.30.0 responded to.
            # Measured: one Sonnet pass hit the 1800s timeout, and with retries=3 that single
            # pass would have burned 90 MINUTES before the driver gave up — silently, because a
            # driver waiting is indistinguishable from a driver working. The cause was a prompt
            # that sent the agent grepping a document that fits in context several times over:
            # a DETERMINISTIC hang, so the second attempt buys an identical wait at full price.
            # Retries still cover what they were for — a transient non-zero exit, and a usage
            # limit — which are the failures where trying again is a different event.
            last = f"timeout after {timeout}s"
            log(f"    {label}: {last} — not retried (a timeout repeats)")
            break
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
            # `unit` is the PAPER, not the call. per_unit_mean() is what extrapolates to a
            # wave budget, and a mean over calls answers a question nobody asked — papers are
            # what a wave is counted in. Falls back to the label so an uninstrumented caller
            # still records something attributable rather than "(unattributed)".
            norm = fuse.record(usage, unit=unit or label, stage=stage)
            return {"ok": True, "text": text, "tokens": sum(norm.values()),
                    "work_tokens": norm["input"] + norm["output"], "streams": dict(norm),
                    "attempts": attempt}
        last = blob.strip()[-400:]
        if _LIMIT_RE.search(blob):
            nap = _seconds_until_reset(blob, backoff)
            log(f"    {label}: usage limit — sleeping {nap // 60}m, then retrying")
            time.sleep(nap)
            continue
        log(f"    {label}: exit {r.returncode} (attempt {attempt}/{retries}) {last[:120]}")
        time.sleep(10 * attempt)
    return {"ok": False, "text": last, "tokens": 0, "work_tokens": 0,
            "streams": {s: 0 for s in STREAMS}, "attempts": retries}


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
        # RESUME AT THE STAGE, NOT THE PAPER. A paper that died during judging has three
        # finished extractions on disk; re-running them costs the most expensive stage twice
        # and — worse — silently REPLACES the ensemble that the surviving artifacts came from,
        # so a resumed paper is no longer the paper that was partly judged. Reuse is the
        # correctness fix; the saved tokens are a side effect.
        done = out_dir / f"pass{i + 1}.json"
        if done.exists():
            try:
                json.loads(done.read_text())["records"]
                log(f"    {paper} pass{i + 1}/{models[i]}: reusing completed pass on disk")
                return i, {"ok": True, "text": "(reused)", "tokens": 0, "work_tokens": 0,
                           "streams": {s: 0 for s in STREAMS}, "attempts": 0, "reused": True}
            except (json.JSONDecodeError, KeyError, OSError):
                pass          # unreadable: fall through and re-run it
        prompt = (template
                  .replace("{STORE}", str(store))
                  .replace("{OUT_DIR}", str(out_dir))
                  .replace("{WRITES}", f"pass{i + 1}.json")
                  + "\n\nYou are ONE independent pass of an ensemble. Do not read any other "
                    "pass's output directory. Write only to your own out_dir.")
        return i, call_agent(prompt, models[i], label=f"{paper} pass{i + 1}/{models[i]}",
                             cwd=pdir, fuse=fuse, stage="extract", unit=paper,
                             read_dirs=(store,), timeout=cfg.get("_call_timeout", 1800))

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


def do_paper(paper: str, cfg: dict, out: pathlib.Path, fuse: Fuse) -> dict:
    """One paper, end to end. ONE PAPER MAY NOT KILL A WAVE.

    Measured: PMC10046388 raised out of `merge_passes` and the traceback ended the run on paper
    1 of 2, after paying for three extraction passes. In a 137-paper wave left running
    overnight that is the whole wave lost to one unusual paper — and unlike a fuse trip it
    leaves no resumable state, because the paper never reached `scored.json`.

    `FuseExceeded` is deliberately NOT caught here: it is the safety device, it means the whole
    run must stop, and swallowing it per-paper would turn a runaway-loop brake into a hiccup.
    """
    try:
        return _do_paper(paper, cfg, out, fuse)
    except FuseExceeded:
        raise
    except Exception as exc:                                    # noqa: BLE001 — deliberate
        log(f"{paper}: FAILED — {type(exc).__name__}: {exc}")
        return {"paper": paper, "status": "error", "n_records": 0, "n_written": 0,
                "failures": [{"pass": None, "model": None, "why": f"{type(exc).__name__}: {exc}"}],
                "dropped": [], "error": f"{type(exc).__name__}: {exc}",
                "questions": [{"paper": paper, "kind": "paper_failed",
                               "detail": f"{type(exc).__name__}: {exc}"}]}


def _paper_budget(cfg: dict, paper: str) -> int:
    """The per-call timeout for one paper, scaled to the document.

    A flat 1800s was applied to a 1.7kB store and a 149kB store alike. Every store in this
    corpus fits in context several times over (largest ~37k tokens against a 200k window), so
    per-call time should track size.

    THERE IS NO LONGER A PAPER DEADLINE. v0.30.0 added one because a per-call timeout times a
    retry count leaves the PAPER unbounded — but a wall-clock stop is the wrong instrument for
    that, and it did real damage: when it expired, every remaining call returned "deadline
    reached" WITHOUT RUNNING, and the driver scored the paper anyway. It recorded 77 records as
    verified-and-denied when the verification had simply not happened, and marked the paper done
    so no resume would revisit it. Not retrying a timeout (see `call_agent`) bounds the paper
    through the mechanism that was actually unbounded, and leaves a skipped stage impossible
    rather than merely detected.
    """
    store = pathlib.Path(cfg["stores"]) / paper / "full.txt"
    kb = (store.stat().st_size / 1024) if store.exists() else 40.0
    per_call = int(cfg.get("call_timeout_base", 420) + kb * cfg.get("call_timeout_per_kb", 12))
    return min(per_call, int(cfg.get("call_timeout_max", 1800)))


def _do_paper(paper: str, cfg: dict, out: pathlib.Path, fuse: Fuse) -> dict:
    pdir = out / paper
    pdir.mkdir(parents=True, exist_ok=True)
    if (pdir / "scored.json").exists():
        return {"paper": paper, "status": "skipped"}

    t0 = time.time()
    cfg = {**cfg, "_call_timeout": _paper_budget(cfg, paper)}
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

    # --- STAGE ORDER (D-079). hunter -> assemble -> score -> SELECT -> judge -> gate -----
    # The judge used to run first, over every merged record, before anything knew which records
    # a verdict could affect. Measured over 165 records, 139 of those calls could not have
    # changed any outcome: at the 0.95 bar only a unanimous, fully-grounded record can be
    # written, and for such a record the judge can only lower the score. So selection comes
    # first now, and the adversarial read is spent on the records it can actually decide — plus
    # a ratified random audit slice of the rejected, so the veto's reject-side behaviour stays
    # measurable. A saving that erased the measurement justifying the pipeline would not be one.
    #
    # The hunter moves ahead of the judge because a contradiction is a SELECTION condition:
    # without it, selection would be reading an incomplete record.
    store = pathlib.Path(cfg["stores"]) / paper

    hp = pathlib.Path(cfg["hunter_prompt"]).read_text(encoding="utf-8")
    values = json.dumps([{ "record_id": r["record_id"],
                           "fields": {f["field_name"]: f.get("value") for f in r["fields"]}}
                         for r in merged["records"]], indent=1)
    hr = call_agent(hp.replace("{STORE}", str(store)).replace("{VALUES}", values),
                    cfg["hunter_model"], label=f"{paper} hunter", cwd=pdir, fuse=fuse,
                    stage="hunter", unit=paper, read_dirs=(store,),
                    timeout=cfg.get("_call_timeout", 1800))
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

    # --- assemble + score: everything the pipeline can decide without a model call --------
    records, dropped = assemble(paper, cfg, merged, hunt)
    scored = []
    for r in records:
        sr = score_and_route(record=r, weights_key=cfg.get("weights_key", "numeric"),
                                 # k=1 has no ensemble to speak of. Passing ensemble_k=1 is
                                 # refused by the contract on purpose: one pass agrees with
                                 # itself, so it would assert agreement nobody measured. Pass 0
                                 # instead, which is the contract's own documented way to run
                                 # without the signal — an absent c_ensemble routes to human
                                 # review and fails closed, rather than passing for free.
                                 ensemble_k=(len(cfg["models"])
                                             if len(cfg["models"]) > 1 else 0),
                                 ensemble_min_agreeing=0,
                                 review_lane=cfg.get("review_lane", []))
        scored.append({"record": sr, "composite": sr.get("_composite_confidence") or 0.0})

    # The audit sample is salted with the WAVE and the PAPER, never with a clock or a PRNG, so a
    # resumed leg re-draws exactly the same rows. A sample nobody can reconstruct is not evidence.
    pick = select_for_judging(scored, cfg, salt=f"{cfg.get('wave', 'wave')}|{paper}")
    log(f"{paper}: {len(scored)} scored -> {len(pick['selected'])} selected, "
        f"{len(pick['audit'])} audited of {len(pick['auditable'])} auditable "
        f"({len(pick['to_judge'])} judge calls, was {len(scored)})")

    # --- judge per record (D-036), over the selected set + the audit slice ---------------
    jt = pathlib.Path(cfg["judge_prompt"]).read_text(encoding="utf-8")
    jdir = pdir / "judge"
    jdir.mkdir(exist_ok=True)
    batch_n = max(1, int(cfg.get("judge_batch_size", 1)))

    def _claim(rec):
        return "; ".join(f"{f['field_name']} = {f.get('value')}" for f in rec["fields"]
                         if f.get("value") is not None)

    def judge_batch(batch):
        """One judge call over `batch` records. Returns (call_ran, [(record_id, verdict|None)]).

        `call_ran` is reported separately from the verdicts because "the judge answered and I
        could not parse it" and "the judge was never invoked" are different facts that both
        arrive here as an empty verdict — and only the second one means the stage did not run.

        THE RAW RESPONSE IS ALWAYS PERSISTED, verdict or not. Previously only a regex-scraped
        verdict survived and the judge's reasoning was discarded, so nobody could audit why a
        record passed its adversarial check — in a pipeline whose entire claim is auditability.
        It also made the 7-of-45 missing verdicts undiagnosable: there was nothing left to read.
        """
        ids = [r["record_id"] for r in batch]
        if batch_n == 1:
            p = jt.replace("{STORE}", str(store)).replace("{CLAIM}", _claim(batch[0]))
        else:
            claims = "\n".join(f"[{r['record_id']}] {_claim(r)}" for r in batch)
            p = (jt.replace("{STORE}", str(store)).replace("{CLAIM}", claims)
                 + "\n\n## Several claims in one call\n"
                   f"You are given {len(batch)} claims, each prefixed with its record id in "
                   "square brackets. **Judge each one INDEPENDENTLY against the source.** A "
                   "verdict on one claim is not evidence about another, and claims sharing a "
                   "source is not a reason to give them the same verdict.\n"
                   "Return a JSON ARRAY, one object per claim, each carrying its "
                   '`record_id` alongside the fields described above.')
        label = f"{paper} judge/{'+'.join(ids)}" if batch_n > 1 else f"{paper} judge/{ids[0]}"
        r = call_agent(p, cfg["judge_model"], label=label, cwd=pdir, fuse=fuse,
                       stage="judge", unit=paper, read_dirs=(store,),
                       timeout=cfg.get("_call_timeout", 1800))
        (jdir / f"{'_'.join(ids)}.raw.txt").write_text(r["text"] or "")

        parsed = _parse_verdicts(r["text"] or "", ids)
        (jdir / f"{'_'.join(ids)}.json").write_text(json.dumps(
            {"record_ids": ids, "ok": r["ok"], "parsed": parsed}, indent=1))
        # THE WHOLE VERDICT OBJECT, not just its verdict word. `weakest_supported_claim` is the
        # line a reviewer holding a denial actually needs, and v0.24.0 went to the trouble of
        # parsing it structurally; returning only the word here would have discarded it again
        # one function later, which is the exact defect that release existed to fix.
        return r["ok"], [(rid, parsed.get(rid)) for rid in ids]

    by_id = {s["record"]["record_id"]: s["record"] for s in scored}
    recs = [by_id[rid] for rid in pick["to_judge"]]
    batches = [recs[i:i + batch_n] for i in range(0, len(recs), batch_n)]
    verdicts, unjudged, judge_calls_ran = {}, [], 0
    if batches:
        with cf.ThreadPoolExecutor(max_workers=cfg.get("judge_concurrency", 4)) as ex:
            for ran, pairs in ex.map(judge_batch, batches):
                judge_calls_ran += bool(ran)
                for rid, v in pairs:
                    if v and v.get("verdict") in VERDICT_TO_STATE:
                        verdicts[rid] = v
                    else:
                        unjudged.append(rid)
    # A MISSING VERDICT IS A FAILURE, NOT AN ABSENCE. Silently treating "the judge did not
    # answer" as "there was nothing to say" lets a record skip the adversarial check that is
    # the point of the pipeline. Measured: 7 of 45 records in the v4 slice, invisible.
    # Only records that were SENT can be missing a verdict; the rest are `not_run` by design.
    if unjudged:
        log(f"{paper}: NO VERDICT for {len(unjudged)}/{len(recs)} — raw responses in judge/")
    log(f"{paper}: judged {len(verdicts)}/{len(recs)}"
        + (f" (batches of {batch_n})" if batch_n > 1 else ""))

    # --- a stage that never ran may not be recorded as a stage that found nothing --------
    # THE WORST DEFECT v0.30.0 INTRODUCED. When the paper deadline expired, every remaining
    # judge and hunter call returned without being invoked; the driver read each missing verdict
    # as an absence, scored the paper, and wrote `scored.json` — marking it DONE and
    # unresumable with 77 records whose adversarial check had simply not happened. The pipeline
    # committed the exact error class it exists to catch, silently, in its own bookkeeping.
    #
    # The line drawn here is between a stage that did not RUN and a stage that ran badly:
    #   - no call in the stage executed  -> the stage is missing. Nothing is scored, no
    #     `scored.json` is written, and the paper stays in `todo` for the next leg. The finished
    #     extraction passes are already on disk, so the retry resumes at the stage (v0.23.0) and
    #     re-reads nothing.
    #   - calls executed and the replies were unusable -> that IS a result. It fails closed
    #     (`not_run` blocks every field), the raw replies are on disk, and it is catalogued as a
    #     question. It is scored, because re-running a paper whose replies are reproducibly
    #     unparseable would loop forever, and a permanent deny that a human can audit beats a
    #     paper that silently never finishes.
    #
    # `batches` is empty when NOTHING needed judging — every record was rejected on evidence the
    # judge cannot overturn and the audit slice came up empty. That is a completed stage with no
    # work in it, not a skipped one, so it must not read as a failure.
    skipped = ([f"judge ({len(batches)} call(s), none executed)"] if batches and not
               judge_calls_ran else []) + ([] if hr["ok"] else ["hunter (call did not execute)"])
    if skipped:
        log(f"{paper}: VERIFICATION INCOMPLETE — {', '.join(skipped)}; "
            f"leaving unscored so the next leg resumes it")
        return {"paper": paper, "status": "incomplete", "n_records": 0, "n_written": 0,
                "skipped_stages": skipped, "failures": failures, "dropped": dropped,
                "questions": [{"paper": paper, "kind": "verification_skipped",
                               "detail": f"{len(scored)} record(s) went unverified: "
                                         f"{'; '.join(skipped)}. The paper is NOT scored and "
                                         f"will be retried; nothing was written."}]}

    # --- spine: veto, gate ---------------------------------------------------------------
    # Nothing has been written yet. Scoring happened above because it is free and it decides who
    # gets judged; the GATE runs only now, with every verdict in hand, so a paper whose judge
    # stage failed above returns `incomplete` with nothing in the database.
    apply_verdicts(scored, verdicts, judged=set(pick["to_judge"]))

    # STRUCTURE RESOLUTION (D-084), and it runs HERE on purpose: after scoring, before the gate.
    # It is a deterministic PubChem lookup, not a model call, so nothing about it needs an agent
    # -- the headless driver simply never reached it, and four of the compound schema's ten
    # fields were absent from every record of the first arm run as a result.
    #
    # After scoring because D-083 ruled an unresolved name costs the record nothing: a structure
    # lookup must not be able to change whether a record is accepted. Before the gate so the
    # fields are on the row that gets written. The compound NAME keeps its literature
    # provenance; these carry `StructuredProvenance` pointing at the authority record.
    if cfg.get("resolve_structures", True):
        _resolve_structures_for(scored, cfg, pdir)

    written = 0
    for s in scored:
        with _gate_lock:
            s["gate"] = upsert(
                record=s["record"], composite_confidence=s["composite"],
                db_path=cfg["db_path"], autoaccept=cfg["auto_accept_threshold"],
                require_contradiction_search=True, review_lane=cfg.get("review_lane", []),
                min_populated_fields=cfg.get("min_populated_fields", 0))
            # EVERY record enters the candidate pool, whatever the gate said. A record that
            # missed the bar still carries its quote, offset, grounding score, agreement
            # fraction and judge verdict — which is most of the work of confirming it by hand.
            # Throwing those away was discarding the bulk of what this pipeline is FOR: it
            # accelerates curation, it does not replace it.
            record_candidate(s["record"], s["composite"], s["gate"],
                             db_path=cfg["db_path"], source_id=paper)
        written += bool(s["gate"].get("written"))

    # What the judge was and was not asked, recorded rather than left to be inferred. A wave that
    # reports a saving without reporting its coverage is asking to be taken on trust.
    selected = set(pick["selected"])
    judge_scope = {
        "records": len(scored), "selected": len(selected),
        "audited": len(pick["audit"]), "auditable": len(pick["auditable"]),
        "audit_fraction": float(cfg["judge_audit_fraction"]),
        "judge_calls": len(pick["to_judge"]),
        "judge_calls_under_judge_everything": len(scored),
        "audit_sample": [{"record_id": r, "denial_class": pick["denial_class"][r],
                          "verdict": (verdicts.get(r) or {}).get("verdict")}
                         for r in pick["audit"]],
        "duplicate_record_ids": pick["duplicate_record_ids"],
        "denial_classes": {c: sum(1 for v in pick["denial_class"].values() if v == c)
                           for c in sorted(set(pick["denial_class"].values()))},
    }
    (pdir / "scored.json").write_text(json.dumps({"records": [s["record"] for s in scored],
                                                  "judge_scope": judge_scope,
                                                  "gate": [{"record_id": s["record"]["record_id"],
                                                            "composite": s["composite"],
                                                            "gate": s["gate"]} for s in scored]},
                                                 indent=1))

    # A record the score would have written and the judge struck out is the single most
    # informative outcome this pipeline produces, and one the old scheme could not name.
    vetoed = [{"record_id": rid, "verdict": verdicts[rid]["verdict"],
               "note": verdicts[rid].get("weakest_supported_claim")}
              for rid in pick["selected"]
              if rid in verdicts and verdicts[rid]["verdict"] != "SUPPORTED"]
    disagreements = [{"record_id": rid, "denial_class": pick["denial_class"][rid]}
                     for rid in pick["audit"]
                     if (verdicts.get(rid) or {}).get("verdict") == "SUPPORTED"]
    qs = catalogue_questions(paper, merged, failures, dropped, unjudged=unjudged,
                             review_lane=tuple(cfg.get("review_lane", [])),
                             vetoed=vetoed, audit_disagreements=disagreements,
                             blocked_on_process=judge_scope["denial_classes"].get(
                                 DENIAL_PROCESS, 0),
                             duplicate_record_ids=pick["duplicate_record_ids"])
    log(f"{paper}: {written}/{len(scored)} written, {len(qs)} question(s), "
        f"{time.time() - t0:.0f}s")
    return {"paper": paper, "status": "done", "n_records": len(scored), "n_written": written,
            "failures": failures, "dropped": dropped, "questions": qs,
            "judge_scope": judge_scope}


def _recover_done(out: pathlib.Path, paper: str) -> dict | None:
    """Rebuild a finished paper's manifest row from its `scored.json`.

    Only counts and outcomes are recoverable. TOKENS ARE NOT: they were spent in an earlier
    process whose account died with it, so a resumed wave's token totals describe the legs
    that actually ran and say so, rather than quietly under-reporting a number that looks
    complete. `carried_from_disk` is the flag that keeps the two readable apart.
    """
    f = out / paper / "scored.json"
    try:
        d = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    gate = d.get("gate", [])
    return {"paper": paper, "status": "done", "carried_from_disk": True,
            "n_records": len(d.get("records", [])),
            "n_written": sum(bool((g.get("gate") or {}).get("written")) for g in gate),
            "failures": [], "dropped": [], "questions": []}


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


def preflight(cfg: dict, papers: list) -> list[str]:
    """Every contract check the wave will need, run BEFORE the first model call (D-095).

    The rule this implements: a refusal about the OPERATOR'S CONFIGURATION floats and waits; a
    refusal about what enters the DATABASE stays hard and silent. Configuration refusals used to
    fire mid-run — `ensemble_k must be >= 2` killed six papers one at a time, and each had
    already paid for its extraction before the scoring stage rejected the setup. Nothing about
    that check needed a model call to run.

    Returns a LIST of problems rather than raising on the first, because an operator fixing a
    config wants all of them at once, not one per attempt.
    """
    problems: list[str] = []
    models = cfg.get("models") or []
    if not models:
        problems.append("`models` is empty — there is nothing to run.")
    elif len(models) > 1:
        try:
            required_agreement(len(models), cfg.get("ensemble_min_agreeing") or None)
        except ValueError as exc:
            problems.append(f"ensemble: {exc}")

    # k=1 must have something standing in for cross-pass agreement (D-100 / V-001). Checked
    # here so an operator who drops to one reading to save tokens is told BEFORE the run, not
    # after a night of it: without a completeness minimum, k=1 has neither of the two signals
    # the composite is built from.
    problems += single_pass_problems(
        len(models) if len(models) > 1 else 0, cfg.get("min_populated_fields", 0))

    wk = cfg.get("weights_key", "numeric")
    if wk not in DEFAULT_WEIGHTS:
        # `score_and_route` silently falls back to "numeric" on an unknown key, so a typo in a
        # ratified profile name would score the whole wave under the wrong weights and say
        # nothing. Caught here rather than discovered in the results.
        problems.append(f"weights_key {wk!r} is not a known profile "
                        f"(known: {sorted(DEFAULT_WEIGHTS)}) — scoring would silently fall "
                        f"back to 'numeric' and the wave would be scored under weights nobody "
                        f"chose.")

    for key in ("extract_prompt", "judge_prompt", "hunter_prompt"):
        f = cfg.get(key)
        if not f or not pathlib.Path(f).exists():
            problems.append(f"{key} not found: {f}")
        elif key == "extract_prompt":
            body = pathlib.Path(f).read_text(encoding="utf-8")
            for token in ("{STORE}", "{OUT_DIR}", "{WRITES}"):
                if token not in body:
                    problems.append(f"extract_prompt is missing the {token} placeholder — "
                                    f"the agent would be told to read a literal path.")

    stores = pathlib.Path(cfg.get("stores", ""))
    missing = [p for p in papers if not (stores / p / "full.txt").exists()]
    if missing:
        problems.append(f"{len(missing)} of {len(papers)} papers have no store on disk "
                        f"(first: {missing[:3]})")

    ids = cfg.get("identity_fields") or {}
    if not ids:
        problems.append("`identity_fields` is empty — passes cannot be aligned, so every "
                        "record would look like a singleton.")
    return problems



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="wave config JSON")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--start-hour", type=int, help="hold until this local hour (0-23)")
    a = ap.parse_args()

    cfg = json.loads(pathlib.Path(a.config).read_text())

    # THE AUDIT FRACTION HAS NO DEFAULT, ON PURPOSE (D-081). It is the share of turned-down
    # records that still gets an adversarial read, which is the only thing keeping the veto's
    # reject-side behaviour measurable — and it trades tokens against how sharply a false-reject
    # rate can be reported. That is a researcher's call, ratified per wave, exactly like the
    # accept threshold and the ensemble bar. A scaffold that silently picked 0.2 would be
    # originating substance; one that silently picked 0.0 would erase the measurement the
    # method's central claim rests on. Same rule as D-038's forbidden truncation default.
    if "judge_audit_fraction" not in cfg:
        log("REFUSING TO RUN: the wave config sets no `judge_audit_fraction`. This is the "
            "share of REJECTED records that still goes to the adversarial judge, so the "
            "reject side of the veto stays measurable. It has no default because it is a "
            "ratified decision, not a tuning constant. Set it (0.20 was ratified for the "
            "terpenoid pilot; 1.0 judges everything, as before D-079).")
        return 2
    if not 0.0 <= float(cfg["judge_audit_fraction"]) <= 1.0:
        log(f"REFUSING TO RUN: judge_audit_fraction={cfg['judge_audit_fraction']} is not a "
            f"fraction in 0..1")
        return 2

    out = pathlib.Path(cfg["out"]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    papers = cfg["papers"] if isinstance(cfg["papers"], list) else \
        json.loads(pathlib.Path(cfg["papers"]).read_text())
    if a.limit:
        papers = papers[:a.limit]

    problems = preflight(cfg, papers)
    if problems:
        log(f"REFUSING TO START — {len(problems)} configuration problem(s), none of which "
            f"needed a model call to find:")
        for i, why in enumerate(problems, 1):
            log(f"  {i}. {why}")
        log("Nothing was run and nothing was spent. Fix these and re-run.")
        return 2
    todo = [p for p in papers if not (out / p / "scored.json").exists()]

    # A RESUMED PAPER IS STILL A PAPER IN THIS WAVE. `todo` correctly skips work already on
    # disk, but the manifest was built only from `results`, so everything finished on an
    # earlier leg vanished from n_done / n_records / n_written / per_paper. A driver whose
    # headline feature is sleeping through a usage limit and resuming was reporting its LAST
    # LEG as though it were the wave. Observed: two papers complete on disk, manifest said
    # n_done 1. Carried forward from disk, marked so nobody mistakes it for fresh work.
    carried = [_recover_done(out, p) for p in papers if p not in todo]
    carried = [c for c in carried if c]

    account = RunAccount(label=f"wave:{out.name}")
    fuse = Fuse(label=f"wave:{out.name}", account=account)
    fuse.raise_ceiling(max_calls=cfg["fuse"]["max_calls"],
                       max_tokens_total=cfg["fuse"]["max_tokens"],
                       reason=f"{len(papers)}-paper wave")

    log(f"wave '{out.name}': {len(papers)} papers, {len(todo)} to run "
        f"({len(papers) - len(todo)} already done)")
    log(f"models {cfg['models']} | judge {cfg['judge_model']} | threshold "
        f"{cfg['auto_accept_threshold']} | db {cfg['db_path']}")
    log(f"judge = VETO after selection (D-079); audit slice {cfg['judge_audit_fraction']:.0%} "
        f"of turned-down records on evidence grounds")
    log(f"fuse {fuse.max_calls:,} calls / {fuse.max_tokens_total:,} tokens")
    if a.dry_run:
        log("DRY RUN — no model calls made")
        return 0

    wait_for_offpeak(a.start_hour)
    results, questions = list(carried), []
    # PAPER-LEVEL CONCURRENCY BUYS WALL-CLOCK, NOT TOKENS. Token cost is calls x per-call
    # context and does not change with how many run at once; 137 papers sequential is ~39
    # hours. Default stays 1: every extra paper in flight multiplies the peak rate at which
    # this hits a usage limit, and the driver's whole point is surviving one unattended.
    n_par = max(1, int(cfg.get("paper_concurrency", 1)))
    try:
        if n_par == 1:
            for n, p in enumerate(todo, 1):
                log(f"--- paper {n}/{len(todo)} ---")
                r = do_paper(p, cfg, out, fuse)
                results.append(r)
                questions.extend(r.get("questions", []))
                _write_manifest(out, cfg, papers, results, questions, fuse, complete=False)
        else:
            log(f"--- {len(todo)} papers, {n_par} at a time ---")
            with cf.ThreadPoolExecutor(max_workers=n_par) as ex:
                futs = {ex.submit(do_paper, p, cfg, out, fuse): p for p in todo}
                for fut in cf.as_completed(futs):
                    r = fut.result()
                    results.append(r)
                    questions.extend(r.get("questions", []))
                    _write_manifest(out, cfg, papers, results, questions, fuse,
                                    complete=False)
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


def _tokens_block(fuse) -> dict:
    """The four streams, kept apart, with the ratified headline named (D-065).

    `work` — input + cache_write + output (D-070) — is the headline: everything the model saw
    for the FIRST TIME, however it arrived, plus what it wrote. The distinction is first-time
    versus re-read, not input versus cache: with caching on a source document is never billed
    as `input`, it arrives as `cache_write`, so an input-only headline excludes the very paper
    being read (measured: input 138, cache_write 351,862 on one run). `cache_read` is reported
    beside it and never folded in — it was 92% of the collapsed total, and comparing THAT
    against an input-token projection is the mechanism behind an "8.0x over projection" figure
    that turned out not to correlate with document size at all.

    `total_all_streams` is retained because it is what the FUSE trips on and what plausibly
    tracks a usage limit — but it is named for what it is instead of being called "tokens".
    """
    acc = getattr(fuse, "account", None)
    if acc is None:
        return {"instrumented": False,
                "_why": "no RunAccount attached — only the collapsed total exists"}
    t = acc.totals()
    per_paper = acc.per_unit_mean()
    return {
        "instrumented": True,
        "headline_work_tokens": acc.work_tokens(),
        "reread_tokens": acc.reread_tokens(),
        "by_stream": dict(t),
        "total_all_streams": sum(t.values()),
        "by_stage": acc.by_stage(),
        "per_paper_mean": {"measured_on_papers": acc.n_units,
                           "work": round(per_paper["input"] + per_paper["cache_write"]
                                         + per_paper["output"]),
                           "by_stream": {k: round(v) for k, v in per_paper.items()}},
        "_headline": "work = input + cache_write + output (D-070): first-time content, however it arrived. cache_read is re-reads, reported beside it, never folded in.",
    }


_PRIOR_TOKENS: dict = {}


def _prior_token_blocks(out: pathlib.Path) -> list:
    """Token blocks measured by EARLIER PROCESSES, captured once per run and never rewritten.

    A MEASURED TOKEN BLOCK IS NEVER OVERWRITTEN BY AN EMPTIER ONE. `_recover_done` restores a
    resumed paper's counts but explicitly cannot restore its tokens — they were spent in a
    process whose account died with it. The manifest on disk, however, still HELD them, and
    every resumed leg overwrote that block with its own. A wave resumed for one last paper
    published a manifest reporting the cost of one paper, in the field a reader takes for the
    cost of the wave. An artifact that lies about what it measured is a thesis violation, not
    a bookkeeping slip — this pipeline's whole claim is that its numbers can be audited.

    Captured lazily on the first write of this process, which is why re-reading is safe: by
    then `manifest.json` is still the PREVIOUS leg's file. Caching it keeps the per-paper
    rewrites from appending this leg's own growing block to its own history.
    """
    key = str(out)
    if key not in _PRIOR_TOKENS:
        try:
            m = json.loads((out / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError):
            m = {}
        prior = list(m.get("tokens_prior_legs") or [])
        block = m.get("tokens") or {}
        if block.get("instrumented"):
            prior.append(block)
        _PRIOR_TOKENS[key] = prior
    return _PRIOR_TOKENS[key]


def _write_manifest(out, cfg, papers, results, questions, fuse, *, complete):
    done = [r for r in results if r["status"] == "done"]
    carried = [r for r in done if r.get("carried_from_disk")]
    prior = _prior_token_blocks(out)
    (out / "manifest.json").write_text(json.dumps({
        "complete": complete, "config": cfg,
        "n_papers": len(papers), "n_done": len(done),
        "n_records": sum(r["n_records"] for r in done),
        "n_written": sum(r["n_written"] for r in done),
        "n_questions": len(questions),
        # Counts above span every leg of the wave; tokens below span only the legs that ran in
        # THIS process. Stating both keeps a resumed wave from reading as a cheap one.
        "n_done_carried_from_earlier_legs": len(carried),
        "papers_unverified_left_for_retry": [r["paper"] for r in results
                                             if r["status"] == "incomplete"],
        "papers_with_failed_passes": [r["paper"] for r in results if r.get("failures")],
        "tokens": _tokens_block(fuse),
        # Kept beside, never summed: earlier legs may have paid for work this leg reused, so
        # adding them would report a total no single run ever spent.
        "tokens_prior_legs": prior,
        **({"_tokens_note": f"`tokens` covers THIS leg only; {len(prior)} earlier leg(s) are "
                            f"preserved in `tokens_prior_legs` and are deliberately not summed."}
           if prior else {}),
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
