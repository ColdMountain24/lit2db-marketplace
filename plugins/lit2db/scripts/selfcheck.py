#!/usr/bin/env python3
"""Report what is ACTUALLY LOADED — version and declared MCP tools — and fail loudly on drift.

This exists because of a bug that cost two sessions and is invisible when it bites. A stale
marketplace clone left v0.1.0 installed against a v0.9.0 repo. The session exposed 6 MCP tools
instead of 13 and an extractor-agent holding only `Read`, and nothing anywhere said so. Every
capability claim was untestable and every probe was silently measuring the wrong artifact.

The trap is that a version number read from the repo you are editing tells you nothing about
the copy the session is running. So this script reads `${CLAUDE_PLUGIN_ROOT}` — the directory
the harness actually launched the plugin from — and reports that. Run it from the repo and it
describes the repo; run it via `${CLAUDE_PLUGIN_ROOT}` and it describes the live install. The
difference between those two answers IS the bug.

It cannot see which tools the *session* ended up exposing; only the agent can. So it prints the
declared tool names for the caller to compare against what it actually holds — see
`commands/lit2db-status.md`.

Usage:
    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/selfcheck.py
    python3 scripts/selfcheck.py --expect-version 0.10.0   # nonzero exit on mismatch
    python3 scripts/selfcheck.py --json
"""
import argparse, json, os, pathlib, re, sys


def plugin_root() -> pathlib.Path:
    """The loaded install dir if the harness set it, else this checkout."""
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env and pathlib.Path(env).is_dir():
        return pathlib.Path(env).resolve()
    return pathlib.Path(__file__).resolve().parent.parent


def inspect(root: pathlib.Path) -> dict:
    manifest = root / ".claude-plugin" / "plugin.json"
    server = root / "mcp" / "lit2db_mcp" / "server.py"

    version, name = None, None
    if manifest.is_file():
        m = json.loads(manifest.read_text())
        version, name = m.get("version"), m.get("name")

    # Tool names as the loaded server declares them. Matches `@mcp.tool()` followed by a def.
    tools = []
    if server.is_file():
        src = server.read_text()
        tools = re.findall(r"@mcp\.tool\(\)[^\n]*\n(?:\s*(?:async\s+)?def\s+(\w+))", src)
        if not tools:  # decorator/def separated by type hints or comments
            tools = re.findall(r"@mcp\.tool\(\)(?:.|\n)*?def\s+(\w+)", src)

    agents = sorted(p.stem for p in (root / "agents").glob("*.md")) \
        if (root / "agents").is_dir() else []

    return {
        "plugin_root": str(root),
        "resolved_from": "CLAUDE_PLUGIN_ROOT" if os.environ.get("CLAUDE_PLUGIN_ROOT")
                         else "this checkout (env var unset)",
        "name": name,
        "version": version,
        "manifest_present": manifest.is_file(),
        "server_present": server.is_file(),
        "declared_mcp_tools": sorted(tools),
        "declared_mcp_tool_count": len(tools),
        "agents": agents,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-version", help="fail if the loaded version differs")
    ap.add_argument("--expect-tools", type=int, help="fail if the declared tool count differs")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    info = inspect(plugin_root())
    if a.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"lit2db selfcheck")
        print(f"  plugin root   : {info['plugin_root']}")
        print(f"  resolved from : {info['resolved_from']}")
        print(f"  name/version  : {info['name']} {info['version']}")
        print(f"  MCP tools declared by the LOADED server: {info['declared_mcp_tool_count']}")
        for t in info["declared_mcp_tools"]:
            print(f"      - {t}")
        print(f"  agents ({len(info['agents'])}): {', '.join(info['agents'])}")

    problems = []
    if not info["manifest_present"]:
        problems.append("no .claude-plugin/plugin.json under the plugin root")
    if not info["server_present"]:
        problems.append("no mcp/lit2db_mcp/server.py under the plugin root")
    if a.expect_version and info["version"] != a.expect_version:
        problems.append(
            f"VERSION MISMATCH: loaded {info['version']!r}, expected {a.expect_version!r}. "
            "The session is running a different copy from the one you are editing — "
            "run /plugin marketplace update, reinstall, then /reload-plugins.")
    if a.expect_tools is not None and info["declared_mcp_tool_count"] != a.expect_tools:
        problems.append(
            f"TOOL COUNT MISMATCH: loaded server declares "
            f"{info['declared_mcp_tool_count']}, expected {a.expect_tools}")

    if problems:
        print("\nFAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    if not a.json:  # stdout must stay parseable in --json mode
        print("\nOK — the loaded plugin is internally consistent.")
        print("NOTE: this cannot see which tools the SESSION exposes. Compare the list above "
              "against the mcp__plugin_lit2db_lit2db__* tools you actually hold; if the session "
              "has fewer, the plugin did not reload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
