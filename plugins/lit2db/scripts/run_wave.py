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
import json
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
from lit2db.store import find_spans, section_of               # noqa: E402
from lit2db.contracts.provenance import process_fingerprint   # noqa: E402

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
_log_lock = threading.Lock()
# The gate writes SQLite. With paper_concurrency > 1 several papers reach the gate at once,
# and concurrent writers to one SQLite file produce "database is locked" — which would surface
# as a gate DENIAL, i.e. a paper silently losing records to a plumbing fault while every
# artifact says the pipeline worked. Serializing the write is cheap: the gate is milliseconds
# against agent calls measured in minutes.
_gate_lock = threading.Lock()

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


_VERDICT = r"SUPPORTED|PARTIAL|UNSUPPORTED"


def _parse_verdicts(text: str, ids: list) -> dict:
    """Map record_id -> the judge's full verdict object, from a single- or multi-claim reply.

    Structured first, regex only as a last resort. The old code was regex-only against
    `"verdict": "..."`, which threw away `weakest_supported_claim`, `reasoning` and
    `overreach` even when it succeeded — the parts a human actually needs to audit a denial.

    Order matters for the fallback: a batched reply may carry several verdicts with no ids
    attached, and pairing the Nth verdict with the Nth requested id is a GUESS. It is labelled
    as one (`by_position: True`) rather than presented as the judge's answer, because a
    mis-paired verdict is worse than a missing one — it attributes a judgement to a record
    nobody made it about.
    """
    out = {}
    blob = _extract_json(text)
    objs = []
    if isinstance(blob, dict):
        objs = blob.get("verdicts") if isinstance(blob.get("verdicts"), list) else [blob]
    if not objs:
        arr = re.search(r"\[[\s\S]*\]", text)
        if arr:
            try:
                cand = json.loads(arr.group(0))
                objs = cand if isinstance(cand, list) else []
            except json.JSONDecodeError:
                objs = []
    for o in objs or []:
        if not isinstance(o, dict):
            continue
        v = str(o.get("verdict", "")).upper()
        if not re.fullmatch(_VERDICT, v):
            continue
        # A single-claim reply carries no `record_id` because it does not need one — the
        # prompt asked about exactly one claim. Attributing it to that claim is unambiguous,
        # and NOT doing so was silently dropping every unbatched judgement to the regex path,
        # discarding the reasoning this function exists to keep.
        rid = o.get("record_id") if o.get("record_id") in ids else (
            ids[0] if len(ids) == 1 else None)
        if rid is not None:
            out[rid] = {k: o.get(k) for k in
                        ("verdict", "weakest_supported_claim", "reasoning", "overreach")}
    if len(ids) == 1 and not out:
        m = re.search(rf'"verdict"\s*:\s*"({_VERDICT})"', text)
        if m:
            out[ids[0]] = {"verdict": m.group(1), "by_regex": True}
    if not out and len(ids) > 1:
        found = re.findall(rf'"verdict"\s*:\s*"({_VERDICT})"', text)
        if len(found) == len(ids):
            for rid, v in zip(ids, found):
                out[rid] = {"verdict": v, "by_position": True, "by_regex": True}
    return out


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
               fuse: Fuse, stage: str = "", unit: str = "", read_dirs: tuple = (),
               timeout: int = 1800, retries: int = 3, backoff: int = 900,
               deadline: float | None = None) -> dict:
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
        # A PER-PAPER DEADLINE THAT RETRIES MAY NOT CROSS. Measured: one Sonnet pass on the
        # largest store we had run hit the 1800s timeout, and with retries=3 that single pass
        # would have burned 90 MINUTES before the driver gave up — silently, because waiting is
        # indistinguishable from working. A flat per-call timeout multiplied by a retry count is
        # the wrong shape: it bounds each attempt and leaves the PAPER unbounded.
        if deadline is not None and time.time() >= deadline:
            last = f"paper deadline reached before attempt {attempt}"
            log(f"    {label}: {last} — giving up on this call")
            break
        # Never let one call run past the paper's own deadline either.
        call_timeout = timeout
        if deadline is not None:
            call_timeout = max(60, min(timeout, int(deadline - time.time())))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=call_timeout, cwd=str(cwd))
        except subprocess.TimeoutExpired:
            last = f"timeout after {call_timeout}s"
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
                             read_dirs=(store,), timeout=cfg.get("_call_timeout", 1800),
                             deadline=cfg.get("_deadline"))

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
            # A MULTI-VALUED FIELD MAY COME BACK WITH ONE QUOTE PER ELEMENT. Models mirror the
            # value's shape, so a `list[str]` field gets a list of quotes — which is arguably
            # the better evidence (D-061 already grounds lists per element) but reached
            # `find_spans` as a list and threw, killing the whole paper. Under paper isolation
            # that is worse than a crash: the paper is recorded as failed and silently lost.
            # Each element is anchored on its own; the first that resolves carries the offset,
            # and the joined text stays as the quote so nothing claims an anchor it lacks.
            if isinstance(quote, (list, tuple)):
                parts = [q for q in quote if isinstance(q, str) and q.strip()]
                hits = next((h for h in (find_spans(full, q) for q in parts) if h), [])
                quote = " | ".join(parts) if parts else None
            else:
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
            r_out = {"record_id": rec["record_id"], "entity_type": rec["entity_type"],
                     "fields": fields}
            # D-067: a record the ratified criteria say can never auto-accept is ROUTED, not
            # denied silently. `route` already blocks in `gate_reasons` — no new gate mechanism
            # was needed, only a way to declare the rule and carry it through the merge. The
            # record keeps every field and its full provenance so the reviewer sees what was
            # extracted, and the reasons say why it is in front of them.
            if rec.get("review_only"):
                r_out["route"] = "human_review"
                r_out["failure_reason"] = "; ".join(rec.get("review_reasons") or ["review-only"])
            out.append(r_out)
    return out, dropped


def catalogue_questions(paper: str, merged: dict, failures: list, dropped: list,
                        unjudged: list | None = None, review_lane: tuple = ()) -> list:
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
        if not rep.get("ambiguous_modal"):
            continue
        # A REVIEW-LANE FIELD DISAGREEING IS NOT NEWS. `function` is free prose the researcher
        # already ratified as never-auto-acceptable (T11): three independent readings phrase it
        # three ways every single time, by construction. Measured: 31 of 75 questions in the v4
        # slice were this one field, burying the 12 scope_disagreements that genuinely need a
        # human. A queue that always fires trains the researcher to stop reading it, which
        # destroys the signal for the cases that matter — the same argument the hunter prompt
        # makes about manufacturing doubt.
        if any(name.endswith(f":{f}") or name == f for f in review_lane):
            continue
        qs.append({"paper": paper, "kind": "no_consensus_value",
                   "detail": f"{name} split with no majority", "groups": rep.get("groups")})
    for rid in (unjudged or []):
        qs.append({"paper": paper, "kind": "no_verdict",
                   "detail": f"{rid}: the adversarial judge returned no parseable verdict; "
                             f"the raw response is in judge/ — a record that skipped its check"})
    for f in failures:
        qs.append({"paper": paper, "kind": "pass_failed",
                   "detail": f"pass {f['pass']} ({f['model']}) did not complete: {f['why']}"})
    for d in dropped:
        if d["why"] != "no quote":
            qs.append({"paper": paper, "kind": "unanchorable_quote", "detail": str(d)})
    return qs


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


def _paper_budget(cfg: dict, paper: str) -> tuple:
    """(per_call_timeout, deadline) for one paper, scaled to the document.

    A flat 1800s was applied to a 1.7kB store and a 149kB store alike. Every store in this
    corpus fits in context several times over (largest ~37k tokens against a 200k window), so
    per-call time should track size, and the PAPER should carry the hard stop.
    """
    store = pathlib.Path(cfg["stores"]) / paper / "full.txt"
    kb = (store.stat().st_size / 1024) if store.exists() else 40.0
    per_call = int(cfg.get("call_timeout_base", 420) + kb * cfg.get("call_timeout_per_kb", 12))
    per_call = min(per_call, int(cfg.get("call_timeout_max", 1800)))
    budget = int(cfg.get("paper_timeout", 2400))
    return per_call, time.time() + budget


def _do_paper(paper: str, cfg: dict, out: pathlib.Path, fuse: Fuse) -> dict:
    pdir = out / paper
    pdir.mkdir(parents=True, exist_ok=True)
    if (pdir / "scored.json").exists():
        return {"paper": paper, "status": "skipped"}

    t0 = time.time()
    call_timeout, deadline = _paper_budget(cfg, paper)
    cfg = {**cfg, "_call_timeout": call_timeout, "_deadline": deadline}
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

    jdir = pdir / "judge"
    jdir.mkdir(exist_ok=True)
    batch_n = max(1, int(cfg.get("judge_batch_size", 1)))

    def _claim(rec):
        return "; ".join(f"{f['field_name']} = {f.get('value')}" for f in rec["fields"]
                         if f.get("value") is not None)

    def judge_batch(batch):
        """One judge call over `batch` records. Returns [(record_id, verdict|None)].

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
                       timeout=cfg.get("_call_timeout", 1800), deadline=cfg.get("_deadline"))
        (jdir / f"{'_'.join(ids)}.raw.txt").write_text(r["text"] or "")

        parsed = _parse_verdicts(r["text"] or "", ids)
        (jdir / f"{'_'.join(ids)}.json").write_text(json.dumps(
            {"record_ids": ids, "ok": r["ok"], "parsed": parsed}, indent=1))
        return [(rid, parsed.get(rid, {}).get("verdict")) for rid in ids]

    recs = merged["records"]
    batches = [recs[i:i + batch_n] for i in range(0, len(recs), batch_n)]
    verdicts, unjudged = {}, []
    with cf.ThreadPoolExecutor(max_workers=cfg.get("judge_concurrency", 4)) as ex:
        for pairs in ex.map(judge_batch, batches):
            for rid, v in pairs:
                if v:
                    verdicts[rid] = v
                else:
                    unjudged.append(rid)
    # A MISSING VERDICT IS A FAILURE, NOT AN ABSENCE. Silently treating "the judge did not
    # answer" as "there was nothing to say" lets a record skip the adversarial check that is
    # the point of the pipeline. Measured: 7 of 45 records in the v4 slice, invisible.
    if unjudged:
        log(f"{paper}: NO VERDICT for {len(unjudged)}/{len(recs)} — raw responses in judge/")
    log(f"{paper}: judged {len(verdicts)}/{len(recs)}"
        + (f" (batches of {batch_n})" if batch_n > 1 else ""))

    hp = pathlib.Path(cfg["hunter_prompt"]).read_text(encoding="utf-8")
    values = json.dumps([{ "record_id": r["record_id"],
                           "fields": {f["field_name"]: f.get("value") for f in r["fields"]}}
                         for r in merged["records"]], indent=1)
    hr = call_agent(hp.replace("{STORE}", str(store)).replace("{VALUES}", values),
                    cfg["hunter_model"], label=f"{paper} hunter", cwd=pdir, fuse=fuse,
                    stage="hunter", unit=paper, read_dirs=(store,),
                    timeout=cfg.get("_call_timeout", 1800), deadline=cfg.get("_deadline"))
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
                                 ensemble_k=len(cfg["models"]), ensemble_min_agreeing=0,
                                 review_lane=cfg.get("review_lane", []))
        comp = sr.get("_composite_confidence") or 0.0
        with _gate_lock:
            g = srv.gate_upsert(record=sr, composite_confidence=comp, db_path=cfg["db_path"],
                                autoaccept=cfg["auto_accept_threshold"],
                                require_contradiction_search=True,
                                review_lane=cfg.get("review_lane", []))
        written += bool(g.get("written"))
        scored.append({"record": sr, "composite": comp, "gate": g})

    (pdir / "scored.json").write_text(json.dumps({"records": [s["record"] for s in scored],
                                                  "gate": [{"record_id": s["record"]["record_id"],
                                                            "composite": s["composite"],
                                                            "gate": s["gate"]} for s in scored]},
                                                 indent=1))
    qs = catalogue_questions(paper, merged, failures, dropped, unjudged=unjudged,
                             review_lane=tuple(cfg.get("review_lane", [])))
    log(f"{paper}: {written}/{len(scored)} written, {len(qs)} question(s), "
        f"{time.time() - t0:.0f}s")
    return {"paper": paper, "status": "done", "n_records": len(scored), "n_written": written,
            "failures": failures, "dropped": dropped, "questions": qs}


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


def _write_manifest(out, cfg, papers, results, questions, fuse, *, complete):
    done = [r for r in results if r["status"] == "done"]
    carried = [r for r in done if r.get("carried_from_disk")]
    (out / "manifest.json").write_text(json.dumps({
        "complete": complete, "config": cfg,
        "n_papers": len(papers), "n_done": len(done),
        "n_records": sum(r["n_records"] for r in done),
        "n_written": sum(r["n_written"] for r in done),
        "n_questions": len(questions),
        # Counts above span every leg of the wave; tokens below span only the legs that ran in
        # THIS process. Stating both keeps a resumed wave from reading as a cheap one.
        "n_done_carried_from_earlier_legs": len(carried),
        "papers_with_failed_passes": [r["paper"] for r in results if r.get("failures")],
        "tokens": _tokens_block(fuse),
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
