#!/usr/bin/env python3
"""Harvest hard facts from a finished (or abandoned) lit2db run.

The meta skill interprets; this script measures. Nothing here reads a model, makes a
network call, or needs a credential — a post-mortem must be runnable when the thing
being post-mortemed is the credential.

    python3 collect.py [project_root]        # default: cwd

Emits JSON on stdout. Exits 0 even on a broken run: a run that died is exactly the
case this is for, so a missing artifact is DATA, never an error.
"""
import collections
import json
import pathlib
import re
import sqlite3
import sys

TS = r"\[(\d\d:\d\d:\d\d)\]"
RE = {
    # "[00:22:05] --- paper 1/20 ---"
    "paper_start": re.compile(TS + r" --- paper (\d+)/(\d+) ---"),
    # "[23:23:55] PMC13532014: 3 extraction passes"
    "passes_begin": re.compile(TS + r" (\S+): (\d+) extraction passes"),
    # "    PMC... pass1/sonnet: reusing completed pass on disk"
    "reused": re.compile(r"(\S+) pass(\d+)/(\S+): reusing completed pass on disk"),
    # "    PMC... pass1/sonnet: exit 1 (attempt 2/3) <tail>"
    "attempt_fail": re.compile(r"(\S+) pass(\d+)/(\S+): exit (\d+) \(attempt (\d+)/(\d+)\)(.*)"),
    # "[00:22:05] PMC...: [3, 2, 3] -> 6 records {'fallback1': 5, 'ordinal': 1}"
    "merged": re.compile(TS + r" (\S+): \[([\d, ]+)\] -> (\d+) records (\{.*\})"),
    # "[23:32:49] PMC...: 3 scored -> 0 selected, 2 audited of 2 auditable (2 judge calls, was 3)"
    "scored": re.compile(TS + r" (\S+): (\d+) scored -> (\d+) selected, (\d+) audited "
                              r"of (\d+) auditable(?: \((\d+) judge calls, was (\d+)\))?"),
    "unscored": re.compile(TS + r" (\S+): every pass failed to run"),
    "wave_end": re.compile(TS + r" WAVE END: (\d+) papers, (\d+) records written, "
                                r"(\d+) questions catalogued"),
    "fuse": re.compile(TS + r" fuse ([\d,]+) calls / ([\d,]+) tokens"),
    "models": re.compile(TS + r" models (\[.*?\]) \| judge (\S+) \| threshold (\S+)"),
}

# Error signatures worth naming rather than counting as "exit 1". Ordered: first match wins.
SIGNATURES = [
    ("credit_balance_exhausted", re.compile(r"Credit balance is too low", re.I)),
    ("rate_limited", re.compile(r"rate.?limit", re.I)),
    ("overloaded", re.compile(r"overloaded|529", re.I)),
    ("auth_failure", re.compile(r"authentication|invalid.?api.?key|401", re.I)),
    ("timeout", re.compile(r"timeout|timed out", re.I)),
    ("bad_request_400", re.compile(r"api_error_status\"?:\s*400")),
]


def _secs(hms):
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def _signature(text):
    for name, rx in SIGNATURES:
        if rx.search(text):
            return name
    return "unclassified"


def parse_log(path):
    """Everything the run said about itself, per paper and in aggregate."""
    if not path.exists():
        return {"log_present": False}
    lines = path.read_text(errors="replace").splitlines()
    papers = collections.OrderedDict()

    def P(name):
        return papers.setdefault(name, {
            "paper": name, "passes_expected": None, "passes_reused": 0,
            "pass_record_counts": None, "merged_records": None, "identity_tiers": None,
            "scored": None, "selected": None, "audited": None, "auditable": None,
            "judge_calls": None, "judge_calls_without_audit": None,
            "failed_attempts": 0, "left_unscored": False,
            "first_seen_s": None, "last_seen_s": None, "error_signatures": {},
        })

    out = {"log_present": True, "lines": len(lines), "wave_end": None,
           "fuse_caps": None, "models_line": None, "order": []}
    first_ts = last_ts = None

    for ln in lines:
        m = RE["paper_start"].search(ln)
        if m:
            out["order"].append(int(m.group(2)))
        for key in ("passes_begin", "merged", "scored", "unscored"):
            m = RE[key].search(ln)
            if not m:
                continue
            p = P(m.group(2))
            t = _secs(m.group(1))
            p["first_seen_s"] = t if p["first_seen_s"] is None else p["first_seen_s"]
            p["last_seen_s"] = t
            if key == "passes_begin":
                p["passes_expected"] = int(m.group(3))
            elif key == "merged":
                p["pass_record_counts"] = [int(x) for x in m.group(3).split(",")]
                p["merged_records"] = int(m.group(4))
                p["identity_tiers"] = m.group(5)
            elif key == "scored":
                p.update(scored=int(m.group(3)), selected=int(m.group(4)),
                         audited=int(m.group(5)), auditable=int(m.group(6)))
                if m.group(7):
                    p["judge_calls"] = int(m.group(7))
                    p["judge_calls_without_audit"] = int(m.group(8))
            else:
                p["left_unscored"] = True

        m = RE["reused"].search(ln)
        if m:
            P(m.group(1))["passes_reused"] += 1
        m = RE["attempt_fail"].search(ln)
        if m:
            p = P(m.group(1))
            p["failed_attempts"] += 1
            sig = _signature(m.group(7) or ln)
            p["error_signatures"][sig] = p["error_signatures"].get(sig, 0) + 1

        m = RE["wave_end"].search(ln)
        if m:
            out["wave_end"] = {"papers": int(m.group(2)), "records_written": int(m.group(3)),
                               "questions": int(m.group(4))}
        m = RE["fuse"].search(ln)
        if m:
            out["fuse_caps"] = {"max_calls": int(m.group(2).replace(",", "")),
                                "max_tokens": int(m.group(3).replace(",", ""))}
        m = RE["models"].search(ln)
        if m:
            out["models_line"] = {"models": m.group(2), "judge": m.group(3),
                                  "threshold": m.group(4)}
        m = re.match(TS, ln)
        if m:
            first_ts = first_ts or _secs(m.group(1))
            last_ts = _secs(m.group(1))

    if first_ts is not None and last_ts is not None:
        out["wall_clock_s"] = last_ts - first_ts + (86400 if last_ts < first_ts else 0)
    out["papers"] = list(papers.values())

    agg = collections.Counter()
    for p in out["papers"]:
        for sig, n in p["error_signatures"].items():
            agg[sig] += n
    out["error_signatures_total"] = dict(agg)
    out["total_failed_attempts"] = sum(p["failed_attempts"] for p in out["papers"])
    out["total_passes_reused"] = sum(p["passes_reused"] for p in out["papers"])
    return out


def parse_artifacts(root):
    """What is actually on disk, which may disagree with what the log claims."""
    run = root / "lit2db" / "run"
    if not run.is_dir():
        return {"run_dir_present": False}
    dirs = sorted(d for d in run.iterdir() if d.is_dir())
    out = {"run_dir_present": True, "paper_dirs": len(dirs), "scored": [], "unscored": [],
           "orphan_pass_dirs": [], "stage_artifacts": collections.Counter()}
    for d in dirs:
        (out["scored"] if (d / "scored.json").exists() else out["unscored"]).append(d.name)
        for name in ("merged.json", "hunter.json", "scored.json"):
            if (d / name).exists():
                out["stage_artifacts"][name] += 1
        passes = list(d.glob("pass*/pass*.json"))
        out["stage_artifacts"]["pass_json"] += len(passes)
        if (d / "judge").is_dir():
            out["stage_artifacts"]["judge_dir"] += 1
        # Passes paid for but never merged: the recoverable-waste signal.
        if passes and not (d / "scored.json").exists():
            out["orphan_pass_dirs"].append({"paper": d.name, "passes_on_disk": len(passes)})
    out["stage_artifacts"] = dict(out["stage_artifacts"])
    return out


def parse_questions(root):
    p = root / "lit2db" / "run" / "QUESTIONS.jsonl"
    if not p.exists():
        return {"present": False, "count": 0, "by_kind": {}, "items": []}
    items, kinds = [], collections.Counter()
    for ln in p.read_text(errors="replace").splitlines():
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        items.append(d)
        kinds[d.get("kind", "unstated")] += 1
    return {"present": True, "count": len(items), "by_kind": dict(kinds), "items": items}


def parse_db(root):
    p = root / "lit2db" / "database.db"
    if not p.exists():
        return {"present": False}
    out = {"present": True, "tables": {}}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        have = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")]
        for t in have:
            out["tables"][t] = con.execute(f"select count(*) from {t}").fetchone()[0]
        if "candidates" in have:
            out["denial_reasons"] = collections.Counter()
            for r in con.execute("select reasons_json from candidates"):
                try:
                    for reason in json.loads(r[0] or "[]"):
                        out["denial_reasons"][str(reason)[:120]] += 1
                except json.JSONDecodeError:
                    pass
            out["denial_reasons"] = dict(out["denial_reasons"])
        con.close()
    except sqlite3.Error as e:
        out["error"] = str(e)
    return out


def parse_config(root):
    out = {}
    for name in ("spec.json", "run.json"):
        p = root / "lit2db" / name
        if not p.exists():
            out[name] = {"present": False}
            continue
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            out[name] = {"present": True, "unparseable": str(e)}
            continue
        if name == "spec.json":
            statuses = collections.Counter(
                i.get("status") for i in d.get("ledger", {}).get("items", []))
            out[name] = {
                "present": True, "spec_version": d.get("spec_version"),
                "ml_task": d.get("ml_task"), "fields": len(d.get("fields", [])),
                "ledger_statuses": dict(statuses),
                "required_fields": sorted(f["name"] for f in d.get("fields", [])
                                          if f.get("required")),
                "gold_set": d.get("gold_set"),
                "threshold_constants": list((d.get("threshold_constants") or {}).keys()),
            }
        else:
            out[name] = {
                "present": True, "papers_planned": len(d.get("papers", [])),
                "models": d.get("models"), "judge_model": d.get("judge_model"),
                "hunter_model": d.get("hunter_model"),
                "judge_audit_fraction": d.get("judge_audit_fraction"),
                "auto_accept_threshold": d.get("auto_accept_threshold"),
                "identity_fields": d.get("identity_fields"),
                "identity_primary": d.get("identity_primary"),
                "producing_process": d.get("producing_process"),
                "min_populated_fields": d.get("min_populated_fields"),
            }
    return out


def derive(log, art, db, cfg, q):
    """Signals worth a human's attention. Facts only — the skill does the arguing."""
    s = {}
    planned = (cfg.get("run.json") or {}).get("papers_planned")
    scored = len(art.get("scored") or [])
    s["papers_planned"] = planned
    s["papers_scored"] = scored
    if planned:
        s["completion_fraction"] = round(scored / planned, 3)
    s["papers_never_scored"] = sorted(art.get("unscored") or [])
    s["recoverable_waste"] = art.get("orphan_pass_dirs") or []
    s["passes_reused_free"] = log.get("total_passes_reused", 0)
    s["retry_attempts_burned"] = log.get("total_failed_attempts", 0)
    s["error_signatures"] = log.get("error_signatures_total", {})

    yields, zero_yield = [], []
    for p in log.get("papers", []):
        if p.get("merged_records") is not None:
            yields.append({"paper": p["paper"], "passes": p["pass_record_counts"],
                           "merged": p["merged_records"], "selected": p.get("selected"),
                           "tiers": p.get("identity_tiers")})
            if p.get("scored") and not p.get("selected"):
                zero_yield.append(p["paper"])
        if p.get("pass_record_counts") and len(set(p["pass_record_counts"])) > 1:
            s.setdefault("passes_disagreed_on_count", []).append(
                {"paper": p["paper"], "counts": p["pass_record_counts"]})
    s["per_paper_yield"] = yields
    s["papers_scoring_nothing"] = zero_yield
    s["accepted_total"] = (db.get("tables") or {}).get("records")
    s["candidates_total"] = (db.get("tables") or {}).get("candidates")
    if s["accepted_total"] == 0 and s["candidates_total"]:
        s["everything_routed_to_review"] = True
    spec = cfg.get("spec.json") or {}
    if spec.get("gold_set") is None:
        s["threshold_is_uncalibrated"] = True
        s["uncalibrated_threshold_value"] = (
            cfg.get("run.json") or {}).get("auto_accept_threshold")
    s["questions_by_kind"] = q.get("by_kind", {})
    durations = [{"paper": p["paper"], "seconds": p["last_seen_s"] - p["first_seen_s"]}
                 for p in log.get("papers", [])
                 if p.get("first_seen_s") is not None and p.get("last_seen_s") is not None
                 and p["last_seen_s"] >= p["first_seen_s"]]
    if durations:
        durations.sort(key=lambda d: -d["seconds"])
        s["slowest_papers"] = durations[:5]
        s["median_paper_seconds"] = sorted(d["seconds"] for d in durations)[len(durations) // 2]
    s["wall_clock_s"] = log.get("wall_clock_s")
    return s


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    log = parse_log(root / "lit2db" / "run.log")
    art = parse_artifacts(root)
    dbi = parse_db(root)
    cfg = parse_config(root)
    q = parse_questions(root)
    print(json.dumps({
        "project_root": str(root), "log": log, "artifacts": art, "database": dbi,
        "config": cfg, "questions": q, "signals": derive(log, art, dbi, cfg, q),
    }, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
