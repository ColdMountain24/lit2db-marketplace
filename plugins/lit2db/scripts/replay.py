#!/usr/bin/env python3
"""Re-run the deterministic spine over extraction output already on disk. ZERO model calls.

WHY THIS EXISTS. Every defect found in this project's first week of real runs was a SPINE
defect, not an extraction defect: the identity chain refusing a paper, a review-lane field
vetoing its own row, a list grounding 0.0 where the scalar grounded 1.0, a U+2010 splitting one
enzyme into two, a judge verdict lost to a regex, token streams collapsed into one integer. Each
was found by spending ~20 minutes and millions of tokens re-extracting papers that had ALREADY
been extracted — using the most expensive part of the pipeline as a debugger for the cheapest.

Extraction passes are the expensive, non-deterministic half. Everything downstream — merge,
ground, judge assembly, score, route, gate — is deterministic and free. So saved passes are a
regression corpus: replay them and a spine change is validated against every awkward real paper
in seconds, before a single token is spent.

WHAT IT CANNOT TELL YOU, and this is the whole boundary:
  - whether the EXTRACTOR obeys a changed prompt. Saved passes were produced under the prompt
    of their day; replaying them re-tests the spine, never the instruction.
  - what a run COSTS. There are no calls, so there is nothing to measure.
Those two need fresh runs. Nothing else does.

    python3 scripts/replay.py --runs ../../analysis                 # everything found under it
    python3 scripts/replay.py --runs <dir> --config wave.json       # with a wave's settings
    python3 scripts/replay.py --runs <dir> --fail-on-error          # CI gate
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from lit2db.ensemble import merge_passes                       # noqa: E402

# The driver's own assembly step, reused rather than reimplemented: it resolves each quote to a
# character offset and builds the provenance the gate requires. Replaying through the REAL path
# means a change to it is covered too — a parallel copy here would drift and validate nothing.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_wave                                                # noqa: E402

PLUGIN = pathlib.Path(__file__).resolve().parent.parent

# Settings a replay needs but that are not IN the saved passes. Deliberately mirrors the wave
# config's shape so `--config` can supply the real ones; these are only a usable default for
# poking at a directory of artifacts.
DEFAULTS = {
    "models": ["opus", "sonnet", "haiku"],
    "auto_accept_threshold": 0.95,
    "weights_key": "numeric",
    "review_lane": [],
    "identity_fields": {},
    "stores": "",
    "evidence_grounded_fields": [],
    "run_timestamp": "replay",
    "producing_process": "replay/spine-only",
    "extract_prompt": "",
}


def find_runs(root: pathlib.Path) -> list:
    """Any directory holding pass*/pass*.json — the shape every runner writes."""
    seen = set()
    for f in sorted(root.rglob("pass*/pass*.json")):
        seen.add(f.parent.parent)
    return sorted(seen)


def load_passes(pdir: pathlib.Path) -> list:
    out = []
    for d in sorted(pdir.glob("pass*")):
        f = d / f"{d.name}.json"
        if not f.exists():
            continue
        try:
            out.append(json.loads(f.read_text())["records"])
        except (json.JSONDecodeError, KeyError, OSError):
            out.append([])          # an unreadable pass is an empty pass, never a crash
    return out


def replay_one(pdir: pathlib.Path, cfg: dict, srv) -> dict:
    """Merge -> score -> gate for one paper's saved passes. Never raises."""
    name = pdir.name
    passes = load_passes(pdir)
    row = {"paper": name, "passes": [len(p) for p in passes]}
    if not any(passes):
        row["status"] = "empty"
        return row
    try:
        merged = merge_passes(passes, identity_fields=cfg["identity_fields"])
    except Exception as exc:                                   # noqa: BLE001
        row.update(status="MERGE FAILED", error=f"{type(exc).__name__}: {exc}")
        return row

    merged["_passes"] = passes          # assemble reads the raw passes back off the merge
    row["merged"] = len(merged["records"])
    row["tiers"] = merged.get("identity_tiers", {})

    # No saved judge or hunter output is REQUIRED: replay tests the spine's arithmetic, not the
    # verification verdicts. Where a run saved them they are reused, so a paper that was really
    # judged replays with its real verdicts rather than a blank.
    judge, hunt = {}, {"state_by_record": {}, "contradictions": []}
    hf = pdir / "hunter.json"
    if hf.exists():
        with contextlib.suppress(Exception):
            hunt = json.loads(hf.read_text())
    jd = pdir / "judge"
    if jd.is_dir():
        for f in jd.glob("*.json"):
            with contextlib.suppress(Exception):
                for rid, v in (json.loads(f.read_text()).get("parsed") or {}).items():
                    if v.get("verdict"):
                        judge[rid] = v["verdict"]
    try:
        records, dropped = run_wave.assemble(name, cfg, merged, judge, hunt)
    except Exception as exc:                                   # noqa: BLE001
        row.update(status="ASSEMBLE FAILED", error=f"{type(exc).__name__}: {exc}")
        return row
    row["assembled"] = len(records)
    row["dropped"] = len(dropped)

    # Score and gate against a throwaway database. A replay must never touch the real one:
    # these records were already gated once, and re-writing them would double-count a yield.
    written, denials = 0, {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db = str(pathlib.Path(tmp) / "replay.db")
            for rec in records:
                sr = srv.score_and_route(record=rec, weights_key=cfg["weights_key"],
                                         ensemble_k=len(cfg["models"]),
                                         ensemble_min_agreeing=0,
                                         review_lane=cfg["review_lane"])
                comp = sr.get("_composite_confidence") or 0.0
                g = srv.gate_upsert(record=sr, composite_confidence=comp, db_path=db,
                                    autoaccept=cfg["auto_accept_threshold"],
                                    require_contradiction_search=False,
                                    review_lane=cfg["review_lane"])
                if g.get("written"):
                    written += 1
                for reason in (g.get("reasons") or []):
                    key = str(reason).split("(")[0].strip()[:58]
                    denials[key] = denials.get(key, 0) + 1
    except Exception as exc:                                   # noqa: BLE001
        row.update(status="GATE FAILED", error=f"{type(exc).__name__}: {exc}")
        return row

    row.update(status="ok", written=written, denials=denials,
               had_verdicts=bool(judge))
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, help="directory to search for saved passes")
    ap.add_argument("--config", help="wave config JSON, for the real identity/lane settings")
    ap.add_argument("--fail-on-error", action="store_true",
                    help="exit non-zero if any paper fails to merge or gate")
    a = ap.parse_args()

    # Whole config through, defaults only filling gaps. Whitelisting keys silently dropped
    # `extract_prompt`, which `assemble` needs for the process fingerprint — a replay must run
    # the real settings, not a subset somebody remembered to list.
    cfg = dict(DEFAULTS)
    if a.config:
        cfg.update(json.loads(pathlib.Path(a.config).read_text()))

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lit2db_mcp_server", PLUGIN / "mcp" / "lit2db_mcp" / "server.py")
    srv = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):            # the server chatters on import
        spec.loader.exec_module(srv)

    runs = find_runs(pathlib.Path(a.runs).resolve())
    print(f"replaying {len(runs)} saved paper-runs — no model calls\n")

    rows, failures = [], 0
    for pdir in runs:
        row = replay_one(pdir, cfg, srv)
        rows.append(row)
        if "FAILED" in row["status"]:
            failures += 1
            print(f"  {row['paper']:<16} {str(row['passes']):<14} {row['status']}: "
                  f"{row['error'][:80]}")
        elif row["status"] == "empty":
            print(f"  {row['paper']:<16} {str(row['passes']):<14} empty")
        else:
            tiers = ",".join(f"{k}:{v}" for k, v in row["tiers"].items() if v)
            print(f"  {row['paper']:<16} {str(row['passes']):<14} -> {row['merged']:>3} merged, "
                  f"{row['assembled']:>3} assembled, {row['written']:>2} written  [{tiers}]")

    ok = [r for r in rows if r["status"] == "ok"]
    judged = sum(1 for r in ok if r.get("had_verdicts"))
    print(f"\n{len(ok)} replayed, {failures} failed, "
          f"{sum(r['merged'] for r in ok)} records, {sum(r['written'] for r in ok)} would write")
    if judged < len(ok):
        print(f"\nNOTE: {len(ok) - judged} of {len(ok)} runs saved no judge verdicts, so their "
              f"records\ncarry no c_judge and route to human_review. A LOW 'would write' here is "
              f"an\nartifact of replaying un-judged artifacts, NOT a gate regression. Runs from\n"
              f"v0.24.0 onward persist verdicts and replay with them.")

    agg: dict = {}
    for r in ok:
        for k, v in r["denials"].items():
            agg[k] = agg.get(k, 0) + v
    if agg:
        print("\ntop denial reasons:")
        for k, v in sorted(agg.items(), key=lambda x: -x[1])[:8]:
            print(f"  {v:>4}  {k}")
    return 1 if (failures and a.fail_on_error) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
