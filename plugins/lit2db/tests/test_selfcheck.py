"""The loaded plugin must be able to say what it is, and drift must fail loudly.

Pins the fix for a bug that cost two sessions: a stale marketplace clone left v0.1.0 installed
against a v0.9.0 repo — 6 MCP tools instead of 13, an extractor-agent holding only `Read`, and
no error anywhere. The trap is that a version read from the repo you are editing says nothing
about the copy the session is running, so these tests exercise the CLAUDE_PLUGIN_ROOT path,
which is the only one that describes the live install.
"""
import json, pathlib, re, subprocess, sys

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
    assert SERVER_TOOL_COUNT == 22, (
        f"server declares {SERVER_TOOL_COUNT} MCP tools; INSTALL.md and "
        "commands/lit2db-status.md both say 22 — update them together or not at all")


def test_the_marketplace_manifest_advertises_the_version_that_is_here():
    """The file an INSTALLER reads must name the plugin that is actually in the repo.

    Found at v0.46.0: `marketplace.json` had been sitting at **0.22.0 while the plugin was at
    0.45.0** — twenty-three releases of drift in the one manifest a user's `/plugin install`
    consults. The standing rule "bump the version in BOTH manifests, then tag" was written down
    and depended on somebody remembering it, which is the same failure mode as every advisory
    check this project has had to make mechanical.

    Nothing referenced this file before this test: the tool count had a guard, the version an
    installer sees had none.
    """
    market = ROOT.parents[1] / ".claude-plugin" / "marketplace.json"
    if not market.exists():
        pytest.skip("not the marketplace repo layout (plugin installed standalone)")
    entries = [p for p in json.loads(market.read_text())["plugins"] if p["name"] == "lit2db"]
    assert entries, "marketplace.json lists no lit2db plugin"
    assert entries[0]["version"] == MANIFEST_VERSION, (
        f"marketplace.json advertises {entries[0]['version']} but the plugin is "
        f"{MANIFEST_VERSION} — an installer would get the version this file names")


def test_pyproject_names_the_same_version_as_the_manifests():
    """The THIRD version number, and it drifted for the same reason the second one did.

    `pyproject.toml` sat at **0.5.0 while the plugin was at 0.48.0** — forty-three releases —
    because nothing compared it to anything. That is exactly the shape the test above was written
    for at v0.46.0, one file over: a version guarded by nobody is not a version, it is a
    decoration, and the next person to read it is misled rather than merely uninformed.

    Parsed by hand rather than with tomllib so this keeps working on the 3.10 the project still
    supports; the line is unambiguous enough that a regex is honest here.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert m, "pyproject.toml declares no version"
    assert m.group(1) == MANIFEST_VERSION, (
        f"pyproject.toml says {m.group(1)} but the plugin manifest says {MANIFEST_VERSION} — "
        f"plugin.json is the version of record, so this one has drifted")


@pytest.mark.parametrize("missing", ["manifest", "server"])
def test_a_broken_install_is_reported_not_ignored(tmp_path, missing):
    root = _fake_plugin(tmp_path, "0.10.0", ["a"])
    target = ({"manifest": root / ".claude-plugin" / "plugin.json",
               "server": root / "mcp" / "lit2db_mcp" / "server.py"})[missing]
    target.unlink()
    r = run([], root=root)
    assert r.returncode == 1
    assert missing in r.stderr or "no " in r.stderr


# --- Licence parity ------------------------------------------------------------------
# The licence now lives in FOUR declaring locations and nothing compared them. That is the
# same defect the version numbers carried twice: `marketplace.json` sat at 0.22.0 against a
# 0.46.0 plugin, and `pyproject.toml` sat at 0.5.0 for forty-three releases. Both were found
# by a parity test, not by review — so the relicense to AGPL-3.0 (D-152) gets one before it
# has a chance to drift, rather than after.

LICENCE_SPDX = "AGPL-3.0-or-later"
_AGPL_TITLE = "GNU AFFERO GENERAL PUBLIC LICENSE"


def test_both_license_files_are_the_same_agpl_text():
    """A plugin whose two LICENSE files disagree has no licence anyone can rely on."""
    plugin_license = (ROOT / "LICENSE").read_text(encoding="utf-8")
    repo_license = (ROOT.parents[1] / "LICENSE").read_text(encoding="utf-8")
    assert _AGPL_TITLE in plugin_license, "the plugin's LICENSE is not the AGPL"
    assert plugin_license == repo_license, (
        "the marketplace LICENSE and the plugin LICENSE differ — an installer reads one "
        "and a forker reads the other")


def test_every_manifest_declares_the_same_licence():
    """`plugin.json` is the licence of record the way it is the version of record."""
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert manifest.get("license") == LICENCE_SPDX, (
        f"plugin.json declares {manifest.get('license')!r}, expected {LICENCE_SPDX!r}")

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "GNU Affero General Public License v3 or later" in pyproject, (
        "pyproject.toml no longer carries the AGPL classifier — it declared no licence at all "
        "until the relicense, which is how a fourth location goes unnoticed")


def test_no_document_still_advertises_the_old_mit_licence():
    """Releases through v0.53.0 shipped MIT and cannot be recalled (D-161), so the READMEs
    must SAY that — but neither may still present MIT as the current licence."""
    for readme in (ROOT / "README.md", ROOT.parents[1] / "README.md"):
        text = readme.read_text(encoding="utf-8")
        section = text.split("## License", 1)
        assert len(section) == 2, f"{readme.name} has no License section"
        body = section[1].split("\n## ", 1)[0]
        assert LICENCE_SPDX in body, f"{readme.name} does not name {LICENCE_SPDX}"
        assert "cannot" in body and "MIT" in body, (
            f"{readme.name} dropped the honest limit — the published MIT releases cannot be "
            "recalled, and a licence section that implies otherwise overclaims")
