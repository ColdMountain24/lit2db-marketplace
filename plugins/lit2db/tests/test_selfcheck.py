"""The loaded plugin must be able to say what it is, and drift must fail loudly.

Pins the fix for a bug that cost two sessions: a stale marketplace clone left v0.1.0 installed
against a v0.9.0 repo — 6 MCP tools instead of 13, an extractor-agent holding only `Read`, and
no error anywhere. The trap is that a version read from the repo you are editing says nothing
about the copy the session is running, so these tests exercise the CLAUDE_PLUGIN_ROOT path,
which is the only one that describes the live install.
"""
import json, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SELFCHECK = ROOT / "scripts" / "selfcheck.py"
MANIFEST_VERSION = json.loads(
    (ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
SERVER_TOOL_COUNT = (ROOT / "mcp" / "lit2db_mcp" / "server.py").read_text().count("@mcp.tool()")


def run(args, root=None):
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    if root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(root)
    return subprocess.run([sys.executable, str(SELFCHECK), *args],
                          capture_output=True, text=True, env=env)


def _fake_plugin(tmp_path, version, tool_names):
    """A minimal plugin tree, as a stale install on disk would look."""
    (tmp_path / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "lit2db", "version": version}))
    srv = tmp_path / "mcp" / "lit2db_mcp"
    srv.mkdir(parents=True)
    srv.joinpath("server.py").write_text(
        "".join(f"@mcp.tool()\ndef {n}():\n    pass\n\n" for n in tool_names))
    return tmp_path


def test_reports_this_checkout_when_env_is_unset():
    r = run([])
    assert r.returncode == 0, r.stderr
    assert MANIFEST_VERSION in r.stdout
    assert "env var unset" in r.stdout


def test_reports_the_loaded_install_when_env_is_set(tmp_path):
    root = _fake_plugin(tmp_path, "9.9.9", ["alpha", "beta"])
    r = run(["--json"], root=root)
    info = json.loads(r.stdout)
    assert info["version"] == "9.9.9"
    assert info["resolved_from"] == "CLAUDE_PLUGIN_ROOT"
    assert info["declared_mcp_tools"] == ["alpha", "beta"]


def test_the_real_bug_a_stale_version_fails_loudly(tmp_path):
    """The exact shape of the incident: v0.1.0 loaded, 6 tools, while 13 were expected."""
    root = _fake_plugin(tmp_path, "0.1.0", [
        "db_query", "gate_upsert", "ground_literature",
        "score_and_route", "validate_mapping", "validate_record"])
    r = run(["--expect-version", "0.10.0", "--expect-tools", "13"], root=root)
    assert r.returncode == 1
    assert "VERSION MISMATCH" in r.stderr
    assert "TOOL COUNT MISMATCH" in r.stderr
    assert "reload" in r.stderr.lower()


def test_a_matching_install_passes(tmp_path):
    root = _fake_plugin(tmp_path, "0.10.0", [f"t{i}" for i in range(13)])
    r = run(["--expect-version", "0.10.0", "--expect-tools", "13"], root=root)
    assert r.returncode == 0, r.stderr


def test_this_checkout_declares_every_tool_the_manifest_version_claims():
    """Guards the repo itself: the count the docs and the status command assert."""
    r = run(["--expect-version", MANIFEST_VERSION,
             "--expect-tools", str(SERVER_TOOL_COUNT)])
    assert r.returncode == 0, r.stderr
    assert SERVER_TOOL_COUNT == 20, (
        f"server declares {SERVER_TOOL_COUNT} MCP tools; INSTALL.md and "
        "commands/lit2db-status.md both say 20 — update them together or not at all")


@pytest.mark.parametrize("missing", ["manifest", "server"])
def test_a_broken_install_is_reported_not_ignored(tmp_path, missing):
    root = _fake_plugin(tmp_path, "0.10.0", ["a"])
    target = ({"manifest": root / ".claude-plugin" / "plugin.json",
               "server": root / "mcp" / "lit2db_mcp" / "server.py"})[missing]
    target.unlink()
    r = run([], root=root)
    assert r.returncode == 1
    assert missing in r.stderr or "no " in r.stderr
