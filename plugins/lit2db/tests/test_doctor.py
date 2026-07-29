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
        f"the test environment has pydantic and mcp installed, so doctor should pass: {d}")
    assert d["restart_required"] is False


def test_a_missing_dependency_names_the_right_interpreter_and_demands_a_restart(monkeypatch):
    """Two things the researcher cannot work out alone. `pip install` on a PATH may install into
    a different Python than the one Claude Code launches the server with — which leaves the
    packages installed and the server still blind. And installing them does not start a server
    that already failed to boot."""
    real = importlib.util.find_spec
    monkeypatch.setattr(doctor.importlib.util, "find_spec",
                        lambda n, *a, **k: None if n == "pydantic" else real(n, *a, **k))
    d = doctor.diagnose()

    assert d["ok"] is False and d["remedy"] == "install_deps"
    assert sys.executable in d["fix"], (
        "the remedy must name THIS interpreter, not a bare `pip` that may belong to another "
        "Python — that mismatch is what makes the failure confusing rather than merely annoying")
    assert "pydantic>=2" in d["fix"]
    assert d["restart_required"] is True


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
