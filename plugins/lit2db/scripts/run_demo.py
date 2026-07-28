#!/usr/bin/env python3
"""End-to-end demo of the lit2db deterministic spine — no network, no external services.

Runs the three demo records through validate -> ground -> (simulated judge) -> score/route ->
gate, exactly the tools the MCP server exposes, and prints the routing outcome for each.

The adversarial judge is a DIFFERENT model in production (verifier-judge-agent) — by default a
different model in the SAME family, never described as cross-family verification (D-041). Here
its verdict is carried on each fixture as `judge_verdict` so the demo is deterministic and
offline: A/C supported, B unsupported.

The judge is a VETO, not a score (D-079): it is applied AFTER the confidence composite decides
what survives, and it can only strike a record out. B is denied twice over, and both denials are
printed — that is the point rather than a redundancy.

Usage:  python3 scripts/run_demo.py
"""
import json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "mcp"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db_mcp import server as S  # the MCP server module; call its tool functions directly

def _fn(tool):
    # FastMCP wraps functions; unwrap to the plain callable for offline calls.
    return getattr(tool, "fn", getattr(tool, "__wrapped__", tool))

def main():
    recs = json.load(open(ROOT / "examples" / "demo_records.json"))
    dbpath = os.path.join(tempfile.mkdtemp(), "demo.db")
    print(f"lit2db demo — deterministic verify/route/gate spine\nDB: {dbpath}\n" + "=" * 66)

    validate      = _fn(S.validate_record)
    ground        = _fn(S.ground_literature)
    score_route   = _fn(S.score_and_route)
    gate          = _fn(S.gate_upsert)
    query         = _fn(S.db_query)

    for name, rec in recs.items():
        print(f"\n### {name}  (record_id={rec['record_id']})")
        v = validate(rec)
        print(f"  1. validate_record : ok={v['ok']}")
        if not v["ok"]:
            print("     -> QUARANTINE (unparseable)"); continue
        # grounding: fill c_grounded from the naive lexical/numeric check
        for fv in rec["fields"]:
            q = fv["provenance"].get("verbatim_quote", "")
            g = ground(fv["value"], q)
            fv["confidence_components"]["c_grounded"] = g["c_grounded"]
            print(f"  2. ground '{fv['field_name']}' : c_grounded={g['c_grounded']} ({g['mode']})")
        scored = score_route(rec)
        comp = scored["_composite_confidence"]
        print(f"  3. score_and_route : composite={comp:.3f}  routing={scored['_routing_summary']}")
        print(f"       (grounding + cross-pass agreement only — the judge is not a term here)")
        verdict = rec.get("judge_verdict", "not_run")
        print(f"  4. adversarial judge (diff. model, same family) : {verdict}"
              + ("  <- clears the veto" if verdict == "supported" else "  <- VETO, struck out"))
        # rebuild a record dict carrying the per-field routes score_and_route assigned
        gated = gate(scored, comp, db_path=dbpath)
        if gated["written"]:
            print(f"  5. gate_upsert : ALLOW -> WRITTEN")
        else:
            print(f"  5. gate_upsert : DENY -> {gated['route_to']}")
            for r in gated["reasons"]:
                print(f"        - {r}")

    print("\n" + "=" * 66)
    ml = query(db_path=dbpath)
    print(f"ML-ready view (auto-accepted, active-source only): {ml['n']} record(s)")
    for r in ml["records"]:
        print(f"   {r['record_id']}  {r['entity_type']}  conf={r['composite_confidence']:.3f}")
    print("\nExpected: only demoA is written. demoB denied (thin agreement AND struck out by the "
          "judge);\ndemoC denied (retracted source, despite a supported verdict). Nothing wrong "
          "is silently in the DB.")

if __name__ == "__main__":
    main()
