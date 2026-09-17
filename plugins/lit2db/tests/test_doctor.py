"""The environment check must work in the environment it exists to diagnose.

`doctor.py` runs precisely when `pydantic` or `mcp` are missing — that is the failure it was
written for, and the one that used to present as "the tools just are not there". So the thing
most worth guarding is not its output but its import list: a single non-stdlib import would make
it die in the only case it matters, and it would die the same silent way.
"""
import ast
import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCTOR = ROOT / "scripts" / "doctor.py"
sys.path.insert(0, str(ROOT / "scripts"))

import doctor  # noqa: E402


def test_doctor_imports_nothing_outside_the_standard_library():
    """The load-bearing property. `doctor.py` is the diagnosis for missing packages; importing
    a third-party package would make it fail exactly when it is needed, and fail silently."""
    tree = ast.parse(DOCTOR.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    outside = sorted(n for n in imported if n not in sys.stdlib_module_names)
    assert not outside, (
        f"doctor.py imports {outside}, which is not in the standard library. It runs when "
        f"dependencies are missing, so a third-party import breaks it in the one case it exists "
        f"for — and breaks it the same invisible way the bug it diagnoses breaks the server.")


def test_a_healthy_environment_reports_ready():
    d = doctor.diagnose()
    assert d["ok"] is True and d["remedy"] == "none", (
        f"the test environment can run the launcher, so doctor should pass: {d}")
    assert d["restart_required"] is False
    assert any(c["name"] == "launcher" and c["ok"] for c in d["checks"]), (
        "the verdict must come from running the launcher the MCP config uses, not from "
        "inspecting whichever python3 happens to be running this script")


def _launcher_fails(monkeypatch):
    monkeypatch.setattr(doctor, "_probe_launcher",
                        lambda root, timeout=600: {"ok": False, "found": None,
                                                   "why": "ModuleNotFoundError: No module named 'mcp'"})


def test_a_stock_mac_without_uv_is_told_to_install_uv_not_pip(monkeypatch):
    """D-199. macOS ships python3 3.9.6 and the server needs 3.10+. The old remedy was `pip
    install` into sys.executable, which cannot fix an interpreter that is too old and is refused
    outright by current Homebrew and python.org installs (PEP 668). uv fetches its own Python and
    leaves the researcher's alone."""
    monkeypatch.setattr(doctor, "_find_uv", lambda: None)
    _launcher_fails(monkeypatch)
    monkeypatch.setattr(doctor, "MIN_PYTHON", (99, 0))      # this interpreter is "too old"
    d = doctor.diagnose()

    assert d["ok"] is False and d["remedy"] == "install_uv"
    assert doctor.UV_INSTALL in d["fix"]
    assert "pip install" not in d["fix"]
    assert d["restart_required"] is True, (
        "installing uv does not start a server that already failed to boot without it")


def test_missing_packages_on_a_new_enough_python_also_route_to_uv(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(doctor.importlib.util, "find_spec",
                        lambda n, *a, **k: None if n == "pydantic" else real(n, *a, **k))
    monkeypatch.setattr(doctor, "_find_uv", lambda: None)
    _launcher_fails(monkeypatch)
    d = doctor.diagnose()

    assert d["remedy"] == "install_uv"
    assert "pydantic>=2" in d["fix"], "the reason must name what is actually missing"
    assert d["restart_required"] is True


def test_uv_present_but_failing_says_so_rather_than_blaming_python(monkeypatch):
    """If uv is there, the python3 running this script is irrelevant, and reporting it as the
    fault would send the researcher to fix the wrong thing."""
    monkeypatch.setattr(doctor, "_find_uv", lambda: "/opt/homebrew/bin/uv")
    _launcher_fails(monkeypatch)
    d = doctor.diagnose()

    assert d["remedy"] == "uv_failed"
    assert "No module named 'mcp'" in d["fix"]
    assert all(c["ok"] is None for c in d["checks"] if c["name"] in ("python", "pydantic", "mcp"))


def test_the_launcher_is_what_the_mcp_config_runs():
    """A doctor that probes one launch path while the config uses another is a check of the
    wrong thing, which passes."""
    import json
    cfg = json.loads((ROOT / "mcp-config.json").read_text(encoding="utf-8"))
    args = cfg["mcpServers"]["lit2db"]["args"]
    assert cfg["mcpServers"]["lit2db"]["command"] == "sh"
    assert args[0] == "${CLAUDE_PLUGIN_ROOT}/bin/lit2db-python"
    assert (ROOT / "bin" / "lit2db-python").is_file()


def test_the_launcher_is_plain_posix_sh():
    """It runs as `sh`, which is dash on some Linux machines, so a bashism fails there only."""
    out = subprocess.run(["sh", "-n", str(ROOT / "bin" / "lit2db-python")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_it_exits_nonzero_so_a_caller_can_branch_on_it():
    """`/lit2db-start` runs this before anything else and acts on the result, so the exit code
    has to carry the verdict even when nobody reads the text."""
    out = subprocess.run([sys.executable, str(DOCTOR), "--json"],
                         capture_output=True, text=True)
    assert out.returncode == 0, f"healthy environment should exit 0: {out.stdout}{out.stderr}"
    assert '"remedy": "none"' in out.stdout


def test_the_start_command_actually_runs_the_check():
    """A doctor nothing calls is documentation. The whole point of this change is that the
    researcher types one slash command and the friction is handled for them."""
    start = (ROOT / "commands" / "lit2db-start.md").read_text(encoding="utf-8")
    assert "doctor.py" in start, (
        "commands/lit2db-start.md must run scripts/doctor.py before anything else, or the "
        "environment failure is back to being the researcher's problem to diagnose")
