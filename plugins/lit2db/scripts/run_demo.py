#!/usr/bin/env python3
"""End-to-end demo of the lit2db deterministic spine — no network, no external services.

Runs the three demo records through validate -> ground -> (simulated judge) -> score/route ->
gate, exactly the tools the MCP server exposes, and prints the routing outcome for each.

The adversarial judge is a DIFFERENT model family in production (verifier-judge-agent). Here
its verdict is carried in each fixture's `c_judge` component so the demo is deterministic and
offline: A/C judge-supported (1.0), B judge-ambiguous (0.0).

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
        judge = rec["fields"][0]["confidence_components"].get("c_judge")
        print(f"  3. adversarial judge (diff. family) : c_judge={judge}"
              + ("  <- SUPPORTED" if judge and judge >= 0.5 else "  <- AMBIGUOUS (flag)"))
        scored = score_route(rec)
        comp = scored["_composite_confidence"]
        print(f"  4. score_and_route : composite={comp:.3f}  routing={scored['_routing_summary']}")
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
    print("\nExpected: only demoA is written. demoB denied (judge-ambiguous -> human_review); "
          "demoC denied (retracted source). Nothing wrong is silently in the DB.")

if __name__ == "__main__":
    main()
