"""`lit2db`: the one command a researcher types (D-211).

    lit2db              open lit2db in the browser (straight into this folder's database, if it has one)
    lit2db status       where each database stands, in sentences
    lit2db help         this list
    lit2db version      which lit2db this is

Everything else happens on the page. The step-by-step commands an agent drives are still
`scripts/project.py`; this file never prints JSON at a person.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys

from . import app, project

HELP = """lit2db turns a research question into a small database built from published papers,
where every value carries the sentence it came from.

  lit2db            open lit2db in your browser
  lit2db status     where each of your databases stands
  lit2db help       show this
  lit2db version    which lit2db this is

Your databases are kept in {home}.
"""


def _home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("LIT2DB_HOME") or app.DEFAULT_HOME).expanduser()


def status() -> int:
    a = app.App(home=_home(), here=_here())
    rows = a.projects()
    if not rows:
        print(f"You have no databases yet. Type lit2db to start one.\n(They will be kept in {a.home}.)")
        return 0
    for r in rows:
        pr = app.Project(pathlib.Path(r["id"]))
        st = pr.state()
        pg = st["progress"]
        line = {"question": "waiting for its question",
                "collect": "drafted; waiting for you to tick its columns",
                "papers": "waiting for you to approve its papers",
                "reading": (f"reading now: {pg['done']} of {pg['total']} papers done" if pg["running"]
                            else f"paused after {pg['done']} of {pg['total']} papers" if pg["done"]
                            else f"ready to read {st['readable']} papers"),
                "checking": "read; values are waiting for you in Checking",
                "spreadsheets": "done; spreadsheets are ready"}[st["current"]]
        if st["current"] == "spreadsheets" and st["results"]:
            acc = st["results"].get("accepted", {}).get("rows", 0)
            line += f" ({acc} accepted rows)"
        print(f"  {r['name']}: {line}")
    print("\nType lit2db to open any of them.")
    return 0


def _here() -> pathlib.Path | None:
    cwd = pathlib.Path.cwd()
    return cwd if (cwd / "lit2db").is_dir() else None


def _claude_note() -> str:
    if shutil.which("claude"):
        return ""
    return ("\nNote: lit2db reads papers with Claude Code, which is not installed on this Mac.\n"
            "Install it from https://claude.com/claude-code and type claude once to sign in.\n"
            "You can still look around lit2db until then.\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lit2db", add_help=False)
    ap.add_argument("what", nargs="?", default="open")
    ap.add_argument("--port", type=int, default=int(os.environ.get("LIT2DB_PORT") or 8770))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    a = ap.parse_args(argv)
    what = "help" if a.help else a.what.lower()
    if what in ("help", "-h", "--help"):
        print(HELP.format(home=_home()))
        return 0
    if what == "version":
        m = json.loads((project.PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
        print(f"lit2db {m.get('version', '')}  ({project.PLUGIN_ROOT})")
        return 0
    if what == "status":
        return status()
    if what in ("open", "start", "app"):
        note = _claude_note()
        if note:
            print(note)
        app.serve(here=_here(), home=_home(), port=a.port, open_browser=not a.no_browser)
        return 0
    print(f"lit2db does not know “{a.what}”.\n")
    print(HELP.format(home=_home()))
    return 2


if __name__ == "__main__":
    sys.exit(main())
