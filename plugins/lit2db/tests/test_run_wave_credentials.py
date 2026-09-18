"""Which account a wave spends, and what it does when it cannot authenticate.

Both from the first BBB wave (2026-09-18). `subprocess.run` with no `env=` handed every spawned
`claude` whatever credential was ambient in the shell: `ANTHROPIC_API_KEY`, set for unrelated
tools. So a 20-paper run metered against a credit balance, died after one paper, and the
researcher's subscription sat unused, with nothing in any log naming the account being spent.

The failure then retried itself 171 times across 19 papers, recovering nothing, because the
driver treats a non-zero exit as transient. A credential failure is not transient, and it
misnames itself: "Credit balance is too low" reads as "top up your account", which would have
worked and left the real defect in place.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

run_wave = pytest.importorskip("run_wave")


def test_the_subscription_is_the_default_and_an_ambient_key_does_not_override_it():
    env = run_wave.child_env({}, {"ANTHROPIC_API_KEY": "sk-x", "ANTHROPIC_AUTH_TOKEN": "t",
                                  "PATH": "/usr/bin"})
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["PATH"] == "/usr/bin", "everything else must still reach the child"


def test_billing_to_an_api_key_is_available_but_has_to_be_asked_for_in_writing():
    env = run_wave.child_env({"bill_to_api_key": True}, {"ANTHROPIC_API_KEY": "sk-x"})
    assert env["ANTHROPIC_API_KEY"] == "sk-x"


def test_child_env_does_not_mutate_the_parent_environment():
    parent = {"ANTHROPIC_API_KEY": "sk-x"}
    run_wave.child_env({}, parent)
    assert parent["ANTHROPIC_API_KEY"] == "sk-x"


@pytest.mark.parametrize("blob", [
    "API Error: 400 {\"message\":\"Credit balance is too low\"}",
    "authentication_error: invalid x-api-key",
    "Your OAuth token has expired. Please run /login.",
])
def test_a_credential_failure_stops_the_wave_instead_of_retrying(monkeypatch, tmp_path, blob):
    class Result:
        returncode, stdout, stderr = 1, "", blob

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(run_wave.subprocess, "run", fake_run)
    fuse = run_wave.Fuse(label="test")
    with pytest.raises(run_wave.CredentialFailure):
        run_wave.call_agent("p", "sonnet", label="x", cwd=tmp_path, fuse=fuse, retries=3)
    assert len(calls) == 1, "a credential failure must not be retried: every retry fails the same"


def test_an_ordinary_non_zero_exit_is_still_retried(monkeypatch, tmp_path):
    """The retry exists for transient failures, and this must not have broken that."""
    class Result:
        returncode, stdout, stderr = 1, "", "some transient wobble"

    calls = []
    monkeypatch.setattr(run_wave.subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), Result())[1])
    monkeypatch.setattr(run_wave.time, "sleep", lambda *_: None)
    out = run_wave.call_agent("p", "sonnet", label="x", cwd=tmp_path,
                              fuse=run_wave.Fuse(label="test"), retries=3)
    assert out["ok"] is False and len(calls) == 3
