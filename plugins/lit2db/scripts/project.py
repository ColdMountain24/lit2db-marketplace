#!/usr/bin/env python3
"""One lit2db project, from a ratified spec to three spreadsheets, in the current folder.

Run it through the launcher so the interpreter is one that can import the plugin:

    sh <plugin>/bin/lit2db-python <plugin>/scripts/project.py <step> [options]

    preview --query Q --limit N      what a query returns, nothing downloaded (before ratifying)
    fetch   --limit N [--abstracts]  run the RATIFIED query from lit2db/spec.json, store papers
    add-pdf --pdf FILE (--doi D | --source-id S) [--title T] [--how-found H]
    add-pdf --manifest FILE          store PDFs you already hold (no JATS anywhere) as sources;
                                     the manifest is a JSON list of {pdf, source_id, doi, title,
                                     how_found}. A later fetch keeps them.
    plan    --entity-type T --identity F --models M [M ...] --judge-model J --hunter-model H
            --audit-fraction X [--threshold 0.95] [--min-fields N]
            [--identity-chain A+B A ...] [--ordinal-within F] [--force]
                                     write lit2db/run.json and the prompts, then check them
    run     [--dry-run] [--limit N]  run the pipeline (long; resumable at the paper boundary)
    review  [--focus judge-supported] open the review page on this project, beside the papers
    calibrate                        what your own verdicts say about where the bar could sit
    review-spec --question Q --query Q2 [--include k=v ...] [--ratify]
                                     a review's starting spec: claims, direction, design,
                                     population, plus their question. PROPOSED until they rule.
    themes  --propose | --freeze | --table
                                     the coding frame: proposed from the sample, ratified by you,
                                     frozen into the spec as a field the normal gate then checks
    prisma                           the flow counts, emitted from the record not counted by hand
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

from lit2db import project, themes                             # noqa: E402


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

    s = sub.add_parser("add-pdf")
    s.add_argument("--pdf", help="one PDF to add")
    s.add_argument("--manifest", help="a JSON list of {pdf, source_id, doi, title, how_found}")
    s.add_argument("--doi", default="", help="the paper's DOI; the source id becomes DOI:<doi>")
    s.add_argument("--source-id", default="", help="used only when there is no DOI")
    s.add_argument("--title", default="")
    s.add_argument("--how-found", default="",
                   help="how this paper was found (cited by X, suggested by Y), for the record")

    s = sub.add_parser("plan")
    s.add_argument("--entity-type", required=True)
    s.add_argument("--identity", required=True, help="the ratified field that identifies a record")
    s.add_argument("--identity-chain", nargs="+", metavar="TIER",
                   help="identity tiers tried in order, each one or more ratified fields joined "
                        "by '+': --identity-chain molecule+dose+metric molecule+metric molecule. "
                        "Use this whenever one named thing can carry several rows.")
    s.add_argument("--run-mode", choices=["fat", "lean"], default="fat",
                   help="fat: k readers, hunter, judge, full harness (the original design). "
                        "lean: one read and one check per paper, stripped harness, source "
                        "pasted in (D-208)")
    s.add_argument("--ordinal-within", nargs="+", default=[], metavar="FIELD",
                   help="scope inside which records nothing identified may be aligned by order "
                        "of appearance. Without it such records stay singletons.")
    s.add_argument("--evidence-grounded", nargs="+", default=[], metavar="FIELD",
                   help="ratified fields whose value is NORMALIZED rather than copied from the "
                        "paper (a unit, a key, a species name): they are grounded on their "
                        "anchored quote instead of by a substring test, which scores a correct "
                        "normalized value 0. Enum fields are included automatically.")
    s.add_argument("--grounding-mode", choices=("lexical", "llm"), default="lexical",
                   help="lexical: the value must appear in its quote. llm: a model reads the "
                        "quote and says whether it supports the value (costs a call per value).")
    s.add_argument("--grounding-model", default="sonnet")
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

    s = sub.add_parser("review")
    s.add_argument("--focus", default="judge-supported",
                   choices=["judge-supported", "all"],
                   help="judge-supported (the default): only records the fact-checker backed "
                        "and the gate still refused, which is the sharpest calibration seed")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--all", action="store_true", help="include records already ruled on")

    s = sub.add_parser("review-spec")
    s.add_argument("--question", required=True, help="the review's question, in their words")
    s.add_argument("--query", required=True, help="the Europe PMC query for the corpus")
    s.add_argument("--include", nargs="*", default=[], metavar="NAME=RULE",
                   help="inclusion and exclusion rules, e.g. design='RCTs only'")
    s.add_argument("--ratify", action="store_true",
                   help="the researcher has ruled on every item above. Without this the spec is "
                        "written PROPOSED and no wave can run on it, which is the point.")

    s = sub.add_parser("themes")
    s.add_argument("--propose", action="store_true",
                   help="read the sample wave's claims and propose a theme set to rule on")
    s.add_argument("--freeze", action="store_true",
                   help="write the RATIFIED set into the spec as an enum field, versioned")
    s.add_argument("--from-file", default="",
                   help="the ruled set to freeze (default: lit2db/themes.json)")
    s.add_argument("--question", default="",
                   help="the review question, for --propose (default: the spec's own)")
    s.add_argument("--model", default="opus")
    s.add_argument("--n", type=int, default=12, help="at most this many themes")
    s.add_argument("--table", action="store_true",
                   help="write lit2db/results/themes.md from the claims as they stand")

    sub.add_parser("calibrate")
    sub.add_parser("prisma")
    sub.add_parser("export")
    sub.add_parser("where")

    a = ap.parse_args(argv)
    get = functools.partial(project.http_get, contact=os.environ.get("LIT2DB_CONTACT_EMAIL", ""))

    try:
        if a.step == "preview":
            out = project.preview(a.query, limit=a.limit, get=get)
        elif a.step == "fetch":
            out = project.fetch(a.root, limit=a.limit, allow_abstracts=a.abstracts, get=get)
        elif a.step == "add-pdf":
            if bool(a.pdf) == bool(a.manifest):
                raise ValueError("give exactly one of --pdf or --manifest")
            if a.manifest:
                out = project.add_pdfs(a.root, a.manifest)     # a failed entry exits nonzero
            else:
                out = project.add_pdf(a.root, a.pdf, source_id=a.source_id, doi=a.doi,
                                      title=a.title, how_found=a.how_found)
        elif a.step == "plan":
            chain = [tier.split("+") for tier in (a.identity_chain or [])] or None
            out = project.plan(a.root, entity_type=a.entity_type, identity_field=a.identity,
                               models=a.models, judge_model=a.judge_model,
                               hunter_model=a.hunter_model,
                               judge_audit_fraction=a.audit_fraction,
                               auto_accept_threshold=a.threshold,
                               min_populated_fields=a.min_fields,
                               identity_chain=chain, ordinal_within=a.ordinal_within, run_mode=a.run_mode,
                               evidence_grounded=a.evidence_grounded,
                               grounding_mode=a.grounding_mode,
                               grounding_model=a.grounding_model,
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
        elif a.step == "review":
            pa = project.paths(a.root)
            cmd = [sys.executable, str(HERE / "review_ui.py"), "--db", str(pa["db"]),
                   "--sources", str(pa["sources"]), "--port", str(a.port),
                   "--focus", a.focus]
            if a.all:
                cmd.append("--all")
            return subprocess.call(cmd)
        elif a.step == "calibrate":
            out = project.calibrate(a.root)
            for name, block in (out.pop("tables", None) or {}).items():
                print(f"\n{name.replace('_', ' ')}\n{block}")
            print()
        elif a.step == "review-spec":
            pa = project.paths(a.root)
            project.init(a.root)
            inclusion = dict(kv.split("=", 1) for kv in a.include if "=" in kv)
            spec = themes.claim_spec(a.question, query=a.query, inclusion=inclusion,
                                     ratified=a.ratify)
            if pa["spec"].exists() and not a.ratify:
                raise ValueError(f"{pa['spec']} already exists; ratifying a new one would "
                                 f"replace a spec a wave may already have run on")
            pa["spec"].write_text(json.dumps(spec, indent=1), encoding="utf-8")
            out = {"spec": str(pa["spec"]), "spec_version": spec["spec_version"],
                   "ratified": a.ratify,
                   "items": [{"id": i["item_id"], "summary": i["summary"], "status": i["status"]}
                             for i in spec["ledger"]["items"]]}
            if not a.ratify:
                out["next"] = ("Nothing can run on this yet. Put every item above to the "
                               "researcher in their own words, one at a time, then re-run with "
                               "--ratify. A spec whose fields trace to PROPOSED items is refused "
                               "by the contract, not by convention.")
        elif a.step == "themes":
            pa = project.paths(a.root)
            if a.table:
                body = themes.markdown(str(pa["db"]), str(pa["spec"]))
                pa["results"].mkdir(parents=True, exist_ok=True)
                (pa["results"] / "themes.md").write_text(body, encoding="utf-8")
                out = {"written": str(pa["results"] / "themes.md"),
                       "themes": list(themes.ratified_set(str(pa["spec"])))}
            elif a.propose:
                spec = json.loads(pa["spec"].read_text(encoding="utf-8"))
                question = a.question or spec.get("research_question") or ""
                # The model call goes through the same headless driver the wave uses, so a
                # proposal is billed and fused exactly like every other call (run_wave.child_env:
                # the subscription unless the config says otherwise).
                sys.path.insert(0, str(HERE))
                import run_wave
                run_wave._CHILD_ENV = run_wave.child_env({}, os.environ)
                fuse = run_wave.Fuse(label="themes")

                def ask(prompt: str) -> str:
                    r = run_wave.call_agent(prompt, a.model, label="themes", cwd=pa["base"],
                                            fuse=fuse, stage="themes", retries=2)
                    if not r["ok"]:
                        raise RuntimeError(f"the theme proposal call failed: {r['text'][:200]}")
                    return r["text"]

                out = themes.propose(str(pa["db"]), question, ask, n_themes=a.n)
                dest = pa["base"] / "themes.proposed.json"
                dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
                out = {**out, "written": str(dest),
                       "next": f"Rule on each theme, save the ones you accept (with any edits to "
                               f"their definitions) as {pa['base'] / 'themes.json'}, then run "
                               f"`themes --freeze`."}
            elif a.freeze:
                src = pathlib.Path(a.from_file) if a.from_file else pa["base"] / "themes.json"
                if not src.exists():
                    raise ValueError(f"no ratified set at {src}; --propose first, then save the "
                                     f"themes you accepted there")
                ruled = json.loads(src.read_text(encoding="utf-8"))
                out = themes.freeze(str(pa["spec"]),
                                    ruled["themes"] if isinstance(ruled, dict) else ruled)
            else:
                raise ValueError("say which: --propose, --freeze or --table")
        elif a.step == "prisma":
            out = project.prisma(a.root)
        elif a.step == "export":
            out = project.export(a.root)
        else:
            out = {k: str(v) for k, v in project.paths(a.root).items()}
    except Exception as exc:                                   # noqa: BLE001
        print(json.dumps({"ok": False, "step": a.step,
                          "error": f"{type(exc).__name__}: {exc}"}, indent=1))
        return 1
    print(json.dumps({"ok": True, "step": a.step, **out}, indent=1, default=str))
    return 0 if out.get("preflight_ok", True) and not out.get("failed") else 1


if __name__ == "__main__":
    sys.exit(main())
