#!/usr/bin/env python3
"""A two-pane review page on localhost: the candidate on the left, its paper on the right.

`/lit2db-review` already collects the labels this project spent weeks calling a blocker, and it
collects them one `AskUserQuestion` at a time — which can show a reviewer the quote and nothing
around it. That is the wrong constraint to put on the answer. `commands/lit2db-review.md`
requires the quote be shown before a verdict is asked for, and a sentence with no paragraph
around it is exactly where a careful reader says "can't tell" for want of CONTEXT rather than
for want of ACCESS. Those two are not the same measurement, and only one of them is about the
extractor.

The store makes the fix cheap. `full.txt` IS the coordinate system, so the paper renders as text
with the quote highlighted in place — no PDF, no viewer, no iframe, and nothing to install.

WHAT THIS SERVER MAY DO, AND THE ONE THING IT MAY NOT. Its only write is
`lit2db.output.record_adjudication`. It never touches `records`, and `lit2db.gate.WRITE_TOOLS`
is a tuple it stays off. A human saying "this one is right" is a measurement of whether the GATE
was right; spending it to write a row destroys the only thing it could have told us. There is a
test that reads this file's source and asserts it.

It also refuses to take a verdict it cannot show the evidence for. When a quote is not at its
recorded offset, only "can't tell from this" is offered — and the REFUSAL LIVES HERE, not in the
page, because a disabled button is a suggestion to whoever is at the keyboard.

Usage:
    python3 scripts/review_ui.py                       # your project, or a demo if there is none
    python3 scripts/review_ui.py --db p.db --sources ./sources
    python3 scripts/review_ui.py --demo                # always the throwaway demo
    python3 scripts/review_ui.py --all --port 9000     # include what you already ruled on

Binds 127.0.0.1 only.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

from lit2db.gate import resolve_threshold  # noqa: E402
from lit2db.output import (record_adjudication, record_candidate,  # noqa: E402
                           review_queue, tag_adjudicator)
from lit2db.review import ALL_VERDICTS, may_record, record_view  # noqa: E402
from lit2db.store import build_from_abstract, find_spans, write_store  # noqa: E402

PAGE = pathlib.Path(__file__).resolve().parent / "review_ui.html"


# --- the demo project ----------------------------------------------------------------------
# `examples/demo_paper/` described three snippets in prose and never contained them, so there
# has never been a store to point anything at. These are those three, written out — synthetic,
# no real paper is redistributed. `examples/demo_records.json` is READ but never modified:
# `run_demo.py` iterates it and CLAUDE.md pins its output as a release check.
DEMO_PAPERS = {
    "PMC_demo_A": (
        "Kinetic characterization of a bacterial hydrolase",
        "Purified enzyme was assayed under steady-state conditions in 50 mM phosphate buffer. "
        "The Michaelis constant Km was determined to be 4.2 uM at pH 7.4, 25 C. "
        "Activity fell sharply above pH 8.5, consistent with titration of the active-site "
        "histidine."),
    "PMC_demo_B": (
        "Acid-dependent turnover in a two-condition assay",
        "Turnover was measured at two acetic acid concentrations. "
        "Turnover numbers of 73.6 and 40.8 s-1 were measured at 0.3% and 0.75% acetic acid "
        "respectively. No single-condition rate is reported for the combined system."),
    "PMC_demo_C": (
        "Substrate affinity of a related hydrolase (retracted)",
        "Binding was characterized by steady-state kinetics. "
        "Km was 12.0 uM under standard assay conditions. "
        "This article has since been retracted by the publisher."),
    "PMC_demo_D": (
        "A paper whose quoted sentence is not in it",
        "This short abstract reports a melting point and nothing else. "
        "The material melted at 141-143 C after recrystallization from ethanol."),
}

# The fourth record exists only here, and only to make the can't-tell-only path VISIBLE. Every
# other demo record anchors cleanly, and a rule that can only be seen in a test is a rule most
# people reading this will take on faith. Its quote is not in PMC_demo_D, and its offset lands
# INSIDE that paper's text — so the demo shows a plausible-looking record whose evidence simply
# is not there, rather than the easier case of an offset obviously off the end.
DEMO_BROKEN = {
    "record_id": "demoD",
    "entity_type": "enzyme_substrate_pair",
    "judge_verdict": "supported",
    "fields": [{
        "field_name": "km_value",
        "value": 8.8,
        "provenance": {
            "kind": "literature", "source_id": "PMC_demo_D",
            "retrieval_timestamp": "2026-07-28T00:00:00Z",
            "producing_process": "extractor@demo", "source_status": "active",
            "section": "Results",
            "verbatim_quote": "The Michaelis constant was 8.8 uM in the presence of cofactor.",
            "char_offset": 60},
        "confidence_components": {"c_grounded": 1.0, "c_ensemble": 1.0},
    }],
}


def seed_demo() -> tuple:
    """Build a throwaway project — four candidates, four stored papers — and return its paths.

    Offsets are RECOMPUTED from the built store with `find_spans`, never carried over from the
    fixture's hand-written `char_offset`. The spine derives offsets and the extractor is
    forbidden from computing one; a demo that hand-wrote one would teach the opposite of what
    the store is for. The one deliberately-broken record keeps its bad offset, which is its job.
    """
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="lit2db-review-demo-"))
    sources, db = tmp / "sources", tmp / "demo.db"

    stores = {}
    for sid, (title, body) in DEMO_PAPERS.items():
        store = build_from_abstract(title, body, sid)
        write_store(store, sources)
        stores[sid] = store["full_text"]

    records = json.loads((ROOT / "examples" / "demo_records.json").read_text(encoding="utf-8"))
    for rec in list(records.values()) + [DEMO_BROKEN]:
        sid = ""
        for fv in rec.get("fields", []):
            prov = fv.get("provenance") or {}
            sid = prov.get("source_id", "") or sid
            text = stores.get(sid)
            if text is None or rec is DEMO_BROKEN:
                continue
            hits = find_spans(text, prov.get("verbatim_quote", ""))
            if hits:
                prov["char_offset"] = hits[0]["start"]
        gate = {"decision": "deny",
                "reasons": ["held back for a human to confirm (demo project)"]}
        record_candidate(rec, 0.90, gate, str(db), source_id=sid)
    return str(db), str(sources), str(tmp)


# --- the server ----------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    cfg: dict = {}

    def log_message(self, fmt, *args):
        """Silent by default. A reviewer working through 40 records does not need 200 lines of
        access log between them and the URL they were told to open."""
        if self.cfg.get("verbose"):
            super().log_message(fmt, *args)

    # -- plumbing
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _query(self) -> dict:
        q = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}

    # -- routes
    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's spelling
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/queue":
            return self._json(200, self._queue())
        if path == "/api/record":
            q = self._query()
            if not q.get("record_id") or not q.get("source_id"):
                return self._json(400, {"error": "record_id and source_id are both required"})
            return self._json(200, self._view(q["record_id"], q["source_id"]))
        return self._json(404, {"error": f"no route {path}"})

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/adjudicate":
            return self._json(404, {"error": f"no route {path}"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (TypeError, ValueError):
            return self._json(400, {"error": "body must be JSON"})

        rid, sid = str(body.get("record_id", "")), str(body.get("source_id", ""))
        verdict, note = str(body.get("verdict", "")), str(body.get("note", "") or "")
        if not rid or not sid:
            return self._json(400, {"error": "record_id and source_id are both required"})
        if verdict.strip().lower() not in ALL_VERDICTS:
            # A verdict outside the vocabulary is a malformed request, not a refused one — the
            # library fails closed on it too, and the two failures are worth telling apart.
            return self._json(400, {"recorded": False,
                                    "reason": f"{verdict!r} is not one of {list(ALL_VERDICTS)}."})

        # Re-derived from the DATABASE AND THE STORE, never taken from the request. The page
        # already hides the buttons; this is the half that holds when the page is wrong, stale,
        # or bypassed — and it is the half the rule actually rests on.
        refusal = may_record(self._view(rid, sid), verdict)
        if refusal:
            return self._json(409, {"recorded": False, "reason": refusal})

        # Tagged here rather than left to the CLI default, so `--adjudicator alice` still records
        # the surface. Which path a verdict came from is a property of the path, not a setting.
        result = record_adjudication(rid, sid, verdict, self.cfg["db"], note=note,
                                     adjudicator=tag_adjudicator(self.cfg["adjudicator"],
                                                                 "browser"))
        return self._json(200 if result.get("recorded") else 400, result)

    # -- the two reads
    def _queue(self) -> dict:
        q = review_queue(self.cfg["db"], limit=self.cfg["limit"],
                         unadjudicated_only=not self.cfg["show_all"])
        return {**q, "sources": self.cfg["sources"], "db": self.cfg["db"],
                "showing_all": self.cfg["show_all"]}

    def _view(self, record_id: str, source_id: str) -> dict:
        row = next((r for r in self._queue()["queue"]
                    if r["record_id"] == record_id and r["source_id"] == source_id), {})
        return record_view(self.cfg["db"], self.cfg["sources"], record_id, source_id,
                           row=row, autoaccept=self.cfg["autoaccept"])


def serve(cfg: dict, port: int = 8765) -> ThreadingHTTPServer:
    """Bound to 127.0.0.1 — a review page is for the person at this machine. `port=0` picks a
    free one, which is what the tests use."""
    handler = type("Handler", (_Handler,), {"cfg": cfg})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Review lit2db candidates against their papers.")
    p.add_argument("--db", default="", help="the project database (env: LIT2DB_DB_PATH)")
    p.add_argument("--sources", default="", help="the store root (default: <plugin>/sources)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--all", action="store_true",
                   help="include records you have already ruled on")
    p.add_argument("--demo", action="store_true", help="always build the throwaway demo")
    p.add_argument("--adjudicator", default="researcher")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args(argv)

    db = a.db or os.environ.get("LIT2DB_DB_PATH", "")
    sources = a.sources or os.environ.get("LIT2DB_SOURCES", "") or str(ROOT / "sources")

    if a.demo or not db or not pathlib.Path(db).is_file():
        if not a.demo:
            print(f"No database at {db or '(none given)'}.")
        db, sources, tmp = seed_demo()
        print(f"Built a demo project in {tmp}\n  4 candidates, 4 stored papers. One of them "
              f"quotes a sentence that is not in its paper. That one can only be answered "
              f"'can't tell', which is the point.")

    cfg = {"db": db, "sources": sources, "limit": a.limit, "show_all": a.all,
           "adjudicator": a.adjudicator, "autoaccept": resolve_threshold(env=os.environ),
           "verbose": a.verbose}

    try:
        httpd = serve(cfg, a.port)
    except OSError as e:
        print(f"Could not bind port {a.port}: {e}\nTry --port {a.port + 1}.", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    n = review_queue(db, limit=a.limit, unadjudicated_only=not a.all)
    print(f"\n{n['n']} candidate(s) waiting · {n['adjudicated_total']} already ruled on · "
          f"{n['ml_ready_total']} confirmed rows in the database")
    print(f"papers: {sources}\ndatabase: {db}\n\n  {url}\n\nCtrl-C to stop.")

    if not a.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
