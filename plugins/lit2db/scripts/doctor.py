#!/usr/bin/env python3
"""Check the ENVIRONMENT the plugin has to run in, and say exactly how to fix it.

`selfcheck.py` answers "is the loaded install internally consistent". This answers the question
that comes before it: "can this install run at all". They are different failures and only one of
them is visible to the researcher.

WHY THIS FILE EXISTS. The plugin's MCP server imports `pydantic` and `mcp` at boot. If either is
missing the server does not start, and Claude Code reports that the way it reports any absent
server: the `mcp__plugin_lit2db_lit2db__*` tools simply are not there. No error, no traceback,
nothing that names the cause. A researcher following the install guide sees two slash commands
succeed and then a tool that does not exist, and has no way to work out why. It was the single
most likely thing to break a live demo, and it broke silently.

The fix is not more documentation. It is that `/lit2db-start` runs this first, so the friction
is detected and repaired by the agent instead of being a prerequisite the researcher has to
have got right on their own.

STDLIB ONLY, AND THAT IS LOAD-BEARING. This script runs precisely when the dependencies are
missing. Importing anything it is checking for would make it fail in the one case it exists to
diagnose. Every import below is from the standard library, and `tests/test_doctor.py` asserts
it stays that way.

IT REPORTS THE INTERPRETER'S OWN pip. `pip install` on a PATH may install into a different
Python than the one Claude Code launches the server with, which produces the confusing state
where the packages are installed and the server still cannot see them. The remedy string always
names `sys.executable`.

Usage:
    python3 scripts/doctor.py           # human-readable
    python3 scripts/doctor.py --json    # for the agent to act on
"""
import argparse
import importlib.util
import json
import os
import pathlib
import sys

MIN_PYTHON = (3, 10)
REQUIRED = (("pydantic", "pydantic>=2", 2), ("mcp", "mcp>=1.0", None))


def _root() -> pathlib.Path:
    """The loaded install if the harness set it, else this checkout."""
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env and pathlib.Path(env).is_dir():
        return pathlib.Path(env)
    return pathlib.Path(__file__).resolve().parent.parent


def _dep(mod: str, spec: str, min_major):
    """Is it importable, and new enough. Never imports it if it is not there."""
    if importlib.util.find_spec(mod) is None:
        return {"name": mod, "ok": False, "found": None,
                "why": f"not installed for {sys.executable}"}
    try:
        version = getattr(__import__(mod), "VERSION", None) or \
                  getattr(__import__(mod), "__version__", None)
    except Exception as e:                                    # importable but broken
        return {"name": mod, "ok": False, "found": None, "why": f"import failed: {e}"}
    if min_major and version:
        try:
            if int(str(version).split(".")[0]) < min_major:
                return {"name": mod, "ok": False, "found": str(version),
                        "why": f"need major version {min_major}+, found {version}"}
        except ValueError:
            pass
    return {"name": mod, "ok": True, "found": str(version) if version else "present", "why": ""}


def diagnose() -> dict:
    checks, missing = [], []

    py_ok = sys.version_info >= MIN_PYTHON
    checks.append({"name": "python", "ok": py_ok,
                   "found": ".".join(str(x) for x in sys.version_info[:3]),
                   "why": "" if py_ok else
                          f"need {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ ({sys.executable})"})

    for mod, spec, major in REQUIRED:
        c = _dep(mod, spec, major)
        checks.append(c)
        if not c["ok"]:
            missing.append(spec)

    root = _root()
    src = root / "src" / "lit2db"
    src_ok = src.is_dir()
    checks.append({"name": "plugin source", "ok": src_ok, "found": str(root),
                   "why": "" if src_ok else f"no src/lit2db under {root}"})

    # Which copy is this? A checkout answers about the checkout; the harness-set root answers
    # about the live install, and the difference between those two answers is its own bug.
    installed = bool(os.environ.get("CLAUDE_PLUGIN_ROOT"))

    if not py_ok:
        remedy, human = "upgrade_python", (
            f"This plugin needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer. "
            f"Claude Code is using {sys.executable}, which is "
            f"{'.'.join(str(x) for x in sys.version_info[:3])}.")
    elif missing:
        remedy, human = "install_deps", (
            f"{sys.executable} -m pip install " + " ".join(f'"{m}"' for m in missing))
    elif not src_ok:
        remedy, human = "reinstall_plugin", (
            "The plugin files are not where they should be. Re-run "
            "`/plugin install lit2db@lit2db-marketplace`.")
    else:
        remedy, human = "none", "Environment is ready."

    return {"ok": remedy == "none", "remedy": remedy, "fix": human,
            "checks": checks, "plugin_root": str(root), "running_as_install": installed,
            "python": sys.executable,
            # Even a clean environment needs this said once: installing the packages does not
            # start the server that failed to boot without them.
            "restart_required": remedy in ("install_deps", "reinstall_plugin")}


def main() -> int:
    p = argparse.ArgumentParser(description="Check the plugin can run in this environment.")
    p.add_argument("--json", action="store_true", help="machine-readable, for the agent")
    a = p.parse_args()

    d = diagnose()
    if a.json:
        print(json.dumps(d, indent=2))
        return 0 if d["ok"] else 1

    print(f"lit2db environment check\n  interpreter: {d['python']}\n"
          f"  plugin root: {d['plugin_root']}"
          f"{'  (live install)' if d['running_as_install'] else '  (checkout)'}\n")
    for c in d["checks"]:
        mark = "ok  " if c["ok"] else "FAIL"
        detail = c["found"] or ""
        print(f"  [{mark}] {c['name']:<15} {detail}{'  <- ' + c['why'] if c['why'] else ''}")

    if d["ok"]:
        print("\nEnvironment is ready.")
        return 0
    print(f"\nFix:\n  {d['fix']}")
    if d["restart_required"]:
        print("\nThen QUIT AND RESTART Claude Code. Installing the packages does not start the\n"
              "server that failed to boot without them, and /reload-plugins does not restart it\n"
              "either — it refreshes commands, hooks and agents only.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
