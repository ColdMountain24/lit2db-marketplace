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

IT ASKS THE LAUNCHER, NOT python3. The server starts through `bin/lit2db-python`, which prefers
uv (its own Python, fetched on first use) and falls back to a python3 that already qualifies.
macOS ships python3 3.9.6, so on a researcher's Mac the python3 running this script is usually
too old, and that is fine as long as uv is there (D-199). The verdict comes from running the
launcher; the python3 lines are context. The remedy used to be `pip install` into
`sys.executable`, and on current Homebrew and python.org installs that is refused outright
(PEP 668), so it now installs uv, which leaves the researcher's own Python untouched.

RUNNING THE PROBE ALSO WARMS THE CACHE. The first uv run downloads Python and three packages:
measured at 3.1 s from an empty cache, well inside the MCP startup timeout, but doing it here
means the server's own first start is not the one paying for it.

Usage:
    python3 scripts/doctor.py           # human-readable
    python3 scripts/doctor.py --json    # for the agent to act on
"""
import argparse
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
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


UV_INSTALL = "curl -LsSf https://astral.sh/uv/install.sh | sh"
# Same order as bin/lit2db-python. A GUI-launched Claude Code does not always inherit the PATH
# uv's installer edited, so the install locations are checked by path as well.
UV_CANDIDATES = ("~/.local/bin/uv", "~/.cargo/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv")


def _find_uv():
    found = shutil.which("uv")
    if found:
        return found
    for c in UV_CANDIDATES:
        path = os.path.expanduser(c)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _probe_launcher(root: pathlib.Path, timeout: int = 600) -> dict:
    """Run the launcher exactly as the MCP config does, and import what the server imports."""
    launcher = root / "bin" / "lit2db-python"
    if not launcher.is_file():
        return {"ok": False, "found": None, "why": f"no launcher at {launcher}"}
    code = "import sys, mcp, pydantic; print('%d.%d.%d' % sys.version_info[:3])"
    try:
        r = subprocess.run(["sh", str(launcher), "-c", code], capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "found": None, "why": f"launcher did not finish in {timeout}s"}
    if r.returncode == 0:
        return {"ok": True, "found": "python " + r.stdout.strip(), "why": ""}
    tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["no output"]
    return {"ok": False, "found": None, "why": tail[0].strip()}


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

    uv = _find_uv()
    checks.append({"name": "uv", "ok": bool(uv), "found": uv,
                   "why": "" if uv else "not installed"})

    root = _root()
    src = root / "src" / "lit2db"
    src_ok = src.is_dir()
    checks.append({"name": "plugin source", "ok": src_ok, "found": str(root),
                   "why": "" if src_ok else f"no src/lit2db under {root}"})

    # Which copy is this? A checkout answers about the checkout; the harness-set root answers
    # about the live install, and the difference between those two answers is its own bug.
    installed = bool(os.environ.get("CLAUDE_PLUGIN_ROOT"))

    # The python3 and package lines above describe THIS interpreter, which the launcher only
    # uses when uv is absent. They are context; the launcher's result is the verdict.
    # `ok: None` means not used, which is neither a pass nor a failure.
    for c in checks:
        if c["name"] in ("python", "pydantic", "mcp") and uv:
            c["ok"], c["why"] = None, "not used: uv supplies its own Python"
    launch = _probe_launcher(root) if src_ok else {"ok": False, "found": None, "why": "skipped"}
    checks.append({"name": "launcher", **launch})

    if not src_ok:
        remedy, human = "reinstall_plugin", (
            "The plugin files are not where they should be. Re-run "
            "`/plugin install lit2db@lit2db-marketplace`.")
    elif launch["ok"]:
        remedy, human = "none", "Environment is ready."
    elif not uv:
        why = (f"Python {'.'.join(str(x) for x in sys.version_info[:3])} is too old"
               if not py_ok else "packages are missing (" + ", ".join(missing) + ")"
               if missing else launch["why"])
        remedy, human = "install_uv", (
            f"{UV_INSTALL}   # installs uv, which fetches its own Python for this plugin "
            f"and leaves {sys.executable} untouched. Reason: {why}.")
    else:
        remedy, human = "uv_failed", (
            f"uv is installed ({uv}) but could not start the plugin: {launch['why']}. "
            "The first run downloads Python and three packages, so it needs the internet once.")

    return {"ok": remedy == "none", "remedy": remedy, "fix": human,
            "checks": checks, "plugin_root": str(root), "running_as_install": installed,
            "python": sys.executable,
            # Even a clean environment needs this said once: installing the packages does not
            # start the server that failed to boot without them.
            "restart_required": remedy in ("install_uv", "uv_failed", "reinstall_plugin")}


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
        mark = "--  " if c["ok"] is None else "ok  " if c["ok"] else "FAIL"
        detail = c["found"] or ""
        print(f"  [{mark}] {c['name']:<15} {detail}{'  <- ' + c['why'] if c['why'] else ''}")

    if d["ok"]:
        print("\nEnvironment is ready.")
        return 0
    print(f"\nFix:\n  {d['fix']}")
    if d["restart_required"]:
        print("\nRun this check again once that is done (it warms the cache), then QUIT AND\n"
              "RESTART Claude Code. A server that failed to boot does not start by itself, and\n"
              "/reload-plugins does not restart it: it refreshes commands, hooks and agents only.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
