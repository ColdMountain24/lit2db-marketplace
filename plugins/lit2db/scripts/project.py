#!/usr/bin/env python3
"""One lit2db project, from a ratified spec to three spreadsheets, in the current folder.

Run it through the launcher so the interpreter is one that can import the plugin:

    sh <plugin>/bin/lit2db-python <plugin>/scripts/project.py <step> [options]

    preview --query Q --limit N      what a query returns, nothing downloaded (before ratifying)
    fetch   --limit N [--abstracts]  run the RATIFIED query from lit2db/spec.json, store papers
    plan    --entity-type T --identity F --models M [M ...] --judge-model J --hunter-model H
            --audit-fraction X [--threshold 0.95] [--min-fields N]
            [--identity-chain A+B A ...] [--ordinal-within F] [--force]
                                     write lit2db/run.json and the prompts, then check them
    run     [--dry-run] [--limit N]  run the pipeline (long; resumable at the paper boundary)
    export                           lit2db/results/{papers,candidates,accepted}.csv
    where                            print the project layout

Every step prints JSON so the session driving it can act on the result, and exits nonzero on a
refusal. See `src/lit2db/project.py` for what each step will and will not do.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from lit2db import project                                     # noqa: E402


def _version() -> str:
    try:
        manifest = HERE.parent / ".claude-plugin" / "plugin.json"
        return json.loads(manifest.read_text(encoding="utf-8"))["version"]
    except Exception:                                          # noqa: BLE001
        return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=".", help="folder that holds (or will hold) lit2db/")
    sub = ap.add_subparsers(dest="step", required=True)

    s = sub.add_parser("preview")
    s.add_argument("--query", required=True)
    s.add_argument("--limit", type=int, default=100)

    s = sub.add_parser("fetch")
    s.add_argument("--limit", type=int, required=True)
    s.add_argument("--abstracts", action="store_true",
                   help="store the abstract when no open full text exists (only if the "
                        "ratified scope admits abstracts)")

    s = sub.add_parser("plan")
    s.add_argument("--entity-type", required=True)
    s.add_argument("--identity", required=True, help="the ratified field that identifies a record")
    s.add_argument("--identity-chain", nargs="+", metavar="TIER",
                   help="identity tiers tried in order, each one or more ratified fields joined "
                        "by '+': --identity-chain molecule+dose+metric molecule+metric molecule. "
                        "Use this whenever one named thing can carry several rows.")
    s.add_argument("--ordinal-within", nargs="+", default=[], metavar="FIELD",
                   help="scope inside which records nothing identified may be aligned by order "
                        "of appearance. Without it such records stay singletons.")
    s.add_argument("--force", action="store_true",
                   help="overwrite a run.json that was edited by hand (keeps run.json.bak)")
    s.add_argument("--models", nargs="+", required=True)
    s.add_argument("--judge-model", required=True)
    s.add_argument("--hunter-model", required=True)
    s.add_argument("--audit-fraction", type=float, required=True)
    s.add_argument("--threshold", type=float, default=0.95)
    s.add_argument("--min-fields", type=int, default=0)

    s = sub.add_parser("run")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--limit", type=int)

    sub.add_parser("export")
    sub.add_parser("where")

    a = ap.parse_args(argv)
    get = functools.partial(project.http_get, contact=os.environ.get("LIT2DB_CONTACT_EMAIL", ""))

    try:
        if a.step == "preview":
            out = project.preview(a.query, limit=a.limit, get=get)
        elif a.step == "fetch":
            out = project.fetch(a.root, limit=a.limit, allow_abstracts=a.abstracts, get=get)
        elif a.step == "plan":
            chain = [tier.split("+") for tier in (a.identity_chain or [])] or None
            out = project.plan(a.root, entity_type=a.entity_type, identity_field=a.identity,
                               models=a.models, judge_model=a.judge_model,
                               hunter_model=a.hunter_model,
                               judge_audit_fraction=a.audit_fraction,
                               auto_accept_threshold=a.threshold,
                               min_populated_fields=a.min_fields,
                               identity_chain=chain, ordinal_within=a.ordinal_within,
                               force=a.force, plugin_version=_version())
            dry = subprocess.run([sys.executable, str(HERE / "run_wave.py"), "--config",
                                  out["config"], "--dry-run"], capture_output=True, text=True)
            out["preflight_ok"] = dry.returncode == 0
            out["preflight"] = (dry.stdout + dry.stderr).strip().splitlines()[-25:]
        elif a.step == "run":
            cfg = project.paths(a.root)["config"]
            if not cfg.exists():
                raise ValueError(f"no run configuration at {cfg}; run plan first")
            cmd = [sys.executable, str(HERE / "run_wave.py"), "--config", str(cfg)]
            if a.dry_run:
                cmd.append("--dry-run")
            if a.limit:
                cmd += ["--limit", str(a.limit)]
            return subprocess.call(cmd)
        elif a.step == "export":
            out = project.export(a.root)
        else:
            out = {k: str(v) for k, v in project.paths(a.root).items()}
    except Exception as exc:                                   # noqa: BLE001
        print(json.dumps({"ok": False, "step": a.step,
                          "error": f"{type(exc).__name__}: {exc}"}, indent=1))
        return 1
    print(json.dumps({"ok": True, "step": a.step, **out}, indent=1, default=str))
    return 0 if out.get("preflight_ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
