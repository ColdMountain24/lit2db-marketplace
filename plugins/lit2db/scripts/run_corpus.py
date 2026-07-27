#!/usr/bin/env python3
"""Drive a whole corpus through the spine, resumably, under a fuse, and report what happened.

`/lit2db-extract` drives ONE source. Nothing drove N — so a 382-paper run was 382 manual
invocations with no resume, no aggregate output, and no way to notice at paper 200 that the yield
was zero. This is that missing layer, and it is ORCHESTRATION ONLY: every verification decision
still belongs to the spine (`validate_record` -> `ground_literature` -> `score_and_route` ->
`gate_upsert`) and to the agents. Nothing here adjudicates anything.

Three properties it exists to guarantee:

  RESUMABLE — a paper whose artifacts already exist is skipped. A run killed at paper 300 does
  not redo 299. There is no cache of model OUTPUT here, deliberately: the k passes must stay
  independent, and a cache keyed on (source, prompt, model) would hand all three passes the same
  answer and manufacture unanimity out of a cache hit. Resume works at the PAPER boundary only.

  FUSED — the run carries a `Fuse` (a safety device, not a budget). A runaway loop trips it and
  the run stops loudly with the paper and the ceiling named, instead of discovering the overrun
  in a bill.

  HONEST ABOUT YIELD — the manifest reports the projected auto-accept rate and, when it is low,
  WHICH FIELD blocked. A run that writes almost nothing is a legitimate result; it is
  indistinguishable from a broken schema unless you can see the blocking field.

The extraction itself is agent work: this script prepares each paper's task, records the outcome,
and aggregates. Run it with `--dry-run` first — that costs nothing and still produces the
per-paper plan, the token projection, and the fuse budget.

    python3 scripts/run_corpus.py --spec SPEC.json --stores DIR --papers LIST.json --out RUN_DIR
    python3 scripts/run_corpus.py ... --dry-run        # plan + budget, no model calls
    python3 scripts/run_corpus.py ... --limit 2        # the smoke test
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from lit2db.accounting import RunAccount            # noqa: E402
from lit2db.dedup import dedupe                     # noqa: E402
from lit2db.fuse import Fuse, FuseExceeded          # noqa: E402
from lit2db.yield_projection import (               # noqa: E402
    explain, project, review_lane_from_spec)

PASS_FILES = ("pass1.json", "pass2.json", "pass3.json")


def load_papers(stores: pathlib.Path, papers_arg: str | None) -> list[str]:
    """Explicit list wins; otherwise every store on disk, sorted for a stable order."""
    if papers_arg:
        p = pathlib.Path(papers_arg)
        data = json.loads(p.read_text())
        if isinstance(data, dict):                       # e.g. extraction_waves.json
            for key in ("wave1", "papers", "pmcids"):
                if key in data:
                    d = data[key]
                    return list(d["pmcids"] if isinstance(d, dict) else d)
            raise SystemExit(f"{p}: no 'wave1'/'papers'/'pmcids' key")
        return list(data)
    return sorted(d.name for d in stores.iterdir() if (d / "full.txt").exists())


def prose_tokens(store: pathlib.Path) -> int:
    f = store / "full.txt"
    return len(f.read_text(encoding="utf-8")) // 4 if f.exists() else 0


def project_cost(total_tokens: int, n_papers: int, *, k: int, records_per_paper: int,
                 judge_prompt: int, extract_prompt: int) -> dict:
    """The D-036 configuration: judge PER RECORD, hunter PER PAPER.

    `n_papers` is a separate argument on purpose. Every prompt overhead here is charged PER
    PAPER, so it multiplies by the paper count — folding the corpus into one `total_tokens` and
    adding the overhead once undercounts a 382-paper run by ~13M tokens. This function had that
    exact bug on its first draft.

    Which is the third instance of one mistake in two days, in one formula: `judge_prompt=1200`
    declared and unused (Claude Science), then inherited into D-036's 9.68M (us, D-050), then a
    per-paper constant applied per-corpus (here). **The formula is the hazard, not the author.**
    That is the argument for `--dry-run` existing at all: print the budget and read it before the
    run, because a projection nobody checks is how all three survived.
    """
    ext = k * (total_tokens + n_papers * extract_prompt)
    jud = records_per_paper * (total_tokens + n_papers * judge_prompt)
    hun = total_tokens + n_papers * records_per_paper * judge_prompt
    overhead = n_papers * 4000
    return {"extract": ext, "judge": jud, "hunter": hun, "overhead": overhead,
            "total": ext + jud + hun + overhead,
            "per_paper_mean": (ext + jud + hun + overhead) // max(1, n_papers)}


def paper_done(pdir: pathlib.Path) -> bool:
    return (pdir / "scored.json").exists()


def read_scored(pdir: pathlib.Path) -> list:
    f = pdir / "scored.json"
    if not f.exists():
        return []
    d = json.loads(f.read_text())
    return d.get("records", d) if isinstance(d, dict) else d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="frozen SchemaReadySpec JSON")
    ap.add_argument("--stores", required=True, help="directory of offset-anchored stores")
    ap.add_argument("--papers", help="JSON list of PMCIDs, or extraction_waves.json")
    ap.add_argument("--out", required=True, help="run directory (per-paper artifacts + manifest)")
    ap.add_argument("--dry-run", action="store_true", help="plan and budget only; no model calls")
    ap.add_argument("--limit", type=int, help="first N papers — use 2 for the smoke test")
    ap.add_argument("--k", type=int, default=3, help="extraction passes (D-032)")
    ap.add_argument("--records-per-paper", type=int, default=9,
                    help="MEASURED per domain, never a default (D-037); 9 is terpenoid's n=1 value")
    ap.add_argument("--judge-prompt", type=int, default=1200)
    ap.add_argument("--extract-prompt", type=int, default=3000)
    ap.add_argument("--bar", type=float, default=1.0, help="agreement bar; ratified (D-034)")
    a = ap.parse_args()

    spec = json.loads(pathlib.Path(a.spec).read_text())
    stores = pathlib.Path(a.stores).resolve()
    out = pathlib.Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    papers = load_papers(stores, a.papers)
    if a.limit:
        papers = papers[:a.limit]

    review_lane = review_lane_from_spec(spec)
    tokens = {p: prose_tokens(stores / p) for p in papers}
    missing = [p for p, t in tokens.items() if t == 0]
    corpus_tokens = sum(tokens.values())
    n_with_store = len(papers) - len(missing)
    budget = project_cost(corpus_tokens, n_with_store, k=a.k,
                          records_per_paper=a.records_per_paper,
                          judge_prompt=a.judge_prompt, extract_prompt=a.extract_prompt)

    print(f"corpus     : {len(papers)} papers, {corpus_tokens:,} prose tokens"
          + (f"  ({len(missing)} with no store — SKIPPED)" if missing else ""))
    print(f"config     : k={a.k}, {a.records_per_paper} records/paper (D-037), bar={a.bar}, "
          f"judge per record + hunter per paper (D-036)")
    print(f"projection : {budget['total']:,} input tokens "
          f"(extract {budget['extract']:,} / judge {budget['judge']:,} / "
          f"hunter {budget['hunter']:,})")
    print(f"review lane: {sorted(review_lane) or '(none)'}")

    # The fuse is sized for THIS run and raised explicitly, never silently widened.
    fuse = Fuse(label=f"corpus:{out.name}")
    fuse.raise_ceiling(max_calls=len(papers) * (a.k + a.records_per_paper + 2),
                       max_tokens_total=int(budget["total"] * 1.5),
                       reason=f"{len(papers)}-paper corpus run")
    print(f"fuse       : {fuse.max_calls:,} calls, {fuse.max_tokens_total:,} tokens")

    if a.dry_run:
        print("\n--- DRY RUN: no model calls made ---")

    account = RunAccount(out.name)
    todo, skipped = [], []
    for p in papers:
        if tokens[p] == 0:
            continue
        (out / p).mkdir(exist_ok=True)
        (skipped if paper_done(out / p) else todo).append(p)

    print(f"\nresume     : {len(skipped)} already done, {len(todo)} to run")
    if todo and not a.dry_run:
        print("\nThis script prepares and aggregates; the k passes, judge and hunter are AGENT "
              "work. Per-paper task files are written below — drive them with /lit2db-extract, "
              "then re-run this script to aggregate.")
        for p in todo:
            (out / p / "TASK.json").write_text(json.dumps({
                "source_id": p, "store": str(stores / p),
                "prose_tokens": tokens[p], "k": a.k,
                "spec": str(pathlib.Path(a.spec).resolve()),
                "review_lane": sorted(review_lane),
                "expects": list(PASS_FILES) + ["merged.json", "scored.json"],
            }, indent=1) + "\n")

    # --- aggregate whatever exists ------------------------------------------------------
    all_records, per_paper = [], []
    for p in papers:
        recs = read_scored(out / p)
        if not recs:
            continue
        pr = project(recs, review_lane=review_lane, bar=a.bar)
        per_paper.append({"pmcid": p, "n_records": pr["n_records"],
                          "n_auto_accept": pr["n_auto_accept"],
                          "blocking_fields": pr["blocking_fields"]})
        all_records.extend(recs)

    manifest = {
        "generated_unix": int(time.time()),
        "spec": str(pathlib.Path(a.spec).resolve()),
        "spec_version": spec.get("spec_version"),
        "stores": str(stores),
        "config": {"k": a.k, "records_per_paper": a.records_per_paper, "bar": a.bar,
                   "judge_prompt": a.judge_prompt, "extract_prompt": a.extract_prompt,
                   "batching": "judge per record, hunter per paper (D-036)"},
        "corpus": {"n_papers": len(papers), "prose_tokens": corpus_tokens,
                   "no_store": missing},
        "projection_tokens": budget,
        "progress": {"done": len(skipped), "todo": len(todo)},
        "fuse": fuse.snapshot(),
        "accounting": account.totals(),
        "per_paper": per_paper,
    }

    if all_records:
        overall = project(all_records, review_lane=review_lane, bar=a.bar)
        manifest["yield"] = {k: v for k, v in overall.items() if k != "per_record"}
        print("\n" + explain(overall))
        # Records are the unit here, but papers can still duplicate — reuse the ladder.
        d = dedupe([{"pmcid": p} for p in papers])
        manifest["corpus_dedup"] = {"n_in": d["n_in"], "unique": d["unique"],
                                    "flagged": d["flagged"]}
    else:
        print("\nno scored records yet — nothing to project")

    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"\nWROTE {out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FuseExceeded as exc:
        print(f"\nRUN STOPPED — {exc}", file=sys.stderr)
        sys.exit(2)
