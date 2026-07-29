"""The docs a NEW USER follows must describe the plugin that is actually here.

Why this file exists. `test_selfcheck` already asserted the MCP tool count, and its message said
"INSTALL.md and commands/lit2db-status.md both say 22 — update them together or not at all". It
never opened either file. So INSTALL.md sat with **22 in one paragraph and 13 in another**, the
expected test count was **330** against a real 744, and the demo's printed final line — the one
you read out loud in front of a room — had both the wrong record id and the wrong confidence.

A guard that names what it protects without reading it is a comment. This one reads the files.

The rule these encode: a doc may UNDERSTATE (a floor that ages into being conservative) but may
never MISSTATE. A number that is merely old is a nuisance; a number that is wrong in front of a
PI is the demo failing.
"""
import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SERVER_TOOL_COUNT = (ROOT / "mcp" / "lit2db_mcp" / "server.py").read_text().count("@mcp.tool()")
COMMANDS = sorted(p.stem for p in (ROOT / "commands").glob("lit2db-*.md"))


@pytest.mark.parametrize("name,doc", [("INSTALL.md", INSTALL), ("README.md", README)])
def test_every_shipped_command_is_documented(name, doc):
    """A command a user cannot discover may as well not ship. `/lit2db-start` is the intended
    front door and was missing from README's command list entirely."""
    # Word-boundary, NOT substring. `/lit2db-start` is a prefix of `/lit2db-startx`, so a plain
    # `in` check passed a doc whose command name had been corrupted — this guard had the same
    # claims-to-check-but-doesn't flaw it was written to fix, and only injecting the defect
    # showed it.
    missing = [c for c in COMMANDS if not re.search(rf"/{re.escape(c)}(?![\w-])", doc)]
    assert not missing, f"{name} never mentions {missing} (ships {len(COMMANDS)} commands)"


@pytest.mark.parametrize("name,doc", [("INSTALL.md", INSTALL), ("README.md", README)])
def test_no_doc_states_a_wrong_mcp_tool_count(name, doc):
    """Any 'N MCP tools' / 'N tools' claim must equal what the server declares."""
    claims = [int(n) for n in re.findall(r"(\d+)\s+MCP tools", doc)]
    claims += [int(n) for n in re.findall(r"all (\d+) MCP tools", doc)]
    assert claims, f"{name} makes no tool-count claim — expected at least one"
    wrong = [c for c in claims if c != SERVER_TOOL_COUNT]
    assert not wrong, (
        f"{name} claims {wrong} MCP tools; the server declares {SERVER_TOOL_COUNT}. "
        f"This is the exact drift that shipped 22-in-one-paragraph-and-13-in-another.")


def test_the_documented_command_count_matches_what_ships():
    for name, doc in (("INSTALL.md", INSTALL), ("README.md", README)):
        for stated in re.findall(r"\*\*(\w+)\*\* lit2db commands", doc):
            words = {"five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
            assert words.get(stated.lower()) == len(COMMANDS), (
                f"{name} says '{stated}' commands; {len(COMMANDS)} ship: {COMMANDS}")
        for stated in re.findall(r"\*\*(\d+) commands\*\*", doc):
            assert int(stated) == len(COMMANDS), f"{name} says {stated}, ships {len(COMMANDS)}"


def test_install_never_overstates_the_test_count():
    """Phrased as a FLOOR ('at least N'), so it ages into being conservative rather than false.
    Overstating it makes a skeptical reviewer's first action look like a failure."""
    # Match the phrasing the doc actually uses, in either asterisk placement — the first version
    # of this regex matched neither, and the guard only passed because a second pattern happened
    # to catch the number somewhere else in the file.
    stated = [int(n) for n in re.findall(r"at least\s+\**\s*(\d+)\s+tests", INSTALL)]
    stated += [int(n) for n in re.findall(r"\*\*at least (\d+) tests\*\*", INSTALL)]
    stated += [int(n) for n in re.findall(r"the (\d+) tests", INSTALL)]
    assert stated, "INSTALL.md no longer states a test count — the floor claim was removed"
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                         cwd=ROOT, capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                              "PYTHONPATH": str(ROOT / "src")})
    m = re.search(r"(\d+) tests collected", out.stdout)
    if not m:
        pytest.skip(f"could not collect: {out.stdout[-300:]} {out.stderr[-300:]}")
    real = int(m.group(1))
    over = [s for s in stated if s > real]
    assert not over, f"INSTALL.md claims {over} tests; only {real} exist"


def test_the_demo_line_install_tells_you_to_expect_is_the_line_it_prints():
    """The strongest of these. INSTALL.md is written to be followed LIVE in front of a PI, and
    its 'expected final line' block had drifted to a record id and a confidence the demo has not
    printed for many releases. Nothing compared the two until now."""
    block = re.search(r"ML-ready view \(auto-accepted, active-source only\): 1 record\(s\)\n"
                      r"\s*(\S+)\s+(\S+)\s+conf=([\d.]+)", INSTALL)
    assert block, "INSTALL.md no longer shows an expected demo output line"
    doc_id, doc_entity, doc_conf = block.groups()

    out = subprocess.run([sys.executable, "scripts/run_demo.py"], cwd=ROOT,
                         capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                              "PYTHONPATH": str(ROOT / "src")})
    assert out.returncode == 0, out.stderr[-500:]
    live = re.search(r"ML-ready view \(auto-accepted, active-source only\): 1 record\(s\)\n"
                     r"\s*(\S+)\s+(\S+)\s+conf=([\d.]+)", out.stdout)
    assert live, f"the demo no longer prints that shape:\n{out.stdout[-500:]}"
    assert (doc_id, doc_entity, doc_conf) == live.groups(), (
        f"INSTALL.md promises {block.groups()}, the demo prints {live.groups()}")


def test_the_judge_verdict_in_the_demo_table_is_the_one_the_demo_returns():
    """INSTALL.md's walkthrough table said record B comes back AMBIGUOUS; it comes back
    UNSUPPORTED. A reader following along live would have contradicted their own screen."""
    out = subprocess.run([sys.executable, "scripts/run_demo.py"], cwd=ROOT,
                         capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                              "PYTHONPATH": str(ROOT / "src")})
    assert out.returncode == 0, out.stderr[-500:]
    section = out.stdout.split("record_id=demoB")[1].split("record_id=demoC")[0]
    verdict = re.search(r"adversarial judge[^:]*:\s*(\w+)", section)
    assert verdict, section[:400]
    assert verdict.group(1).upper() in INSTALL, (
        f"the demo returns {verdict.group(1).upper()} for record B; INSTALL.md never says it")


def test_the_install_path_points_at_something_that_exists():
    """It told users to `tar xzf /path/to/lit2db-plugin.tar.gz` — a file this project has never
    published. The repo is public; the first instruction must be the one that works."""
    assert "/plugin marketplace add ColdMountain24/lit2db-marketplace" in INSTALL
    assert "lit2db-plugin.tar.gz" not in INSTALL


@pytest.mark.parametrize("name,doc", [("INSTALL.md", INSTALL), ("README.md", README)])
def test_the_stale_mcp_server_trap_is_documented(name, doc):
    """Measured twice and reproducible: `/reload-plugins` refreshes commands and hooks but does
    NOT re-exec the MCP server, so a user follows the docs and still holds an old tool set with
    no error shown. A new user hits this before anything else works."""
    assert "reload-plugins" in doc, f"{name} does not warn about the stale-server trap"
    assert re.search(r"(new session|restart the session|fresh session)", doc), name


def test_the_marketplace_readme_agrees_with_the_plugin_version():
    market = ROOT.parents[1] / ".claude-plugin" / "marketplace.json"
    if not market.exists():
        pytest.skip("not the marketplace repo layout")
    version = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
    entries = [p for p in json.loads(market.read_text())["plugins"] if p["name"] == "lit2db"]
    assert entries and entries[0]["version"] == version
