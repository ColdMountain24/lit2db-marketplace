"""Tests for the instantiation template — the one file a researcher actually fills in.

`_TEMPLATE/instantiation.yaml` is the hand-off from Stage 0.5 to Stage 2. If it drifts from
`SchemaReadySpec`, the drift surfaces as a validation error in someone else's project, long
after the interview. These make that drift fail here instead.
"""
import re, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lit2db.contracts.spec import SchemaReadySpec

TEMPLATE = ROOT / "instantiation" / "_TEMPLATE" / "instantiation.yaml"

# Plugin-root-relative paths the template may point a reader at.
_REF = re.compile(r"\b(?:src|instantiation|examples|skills|hooks|mcp|tests|scripts)/[\w./-]+")


def test_template_covers_every_schema_ready_spec_key():
    """Every key SchemaReadySpec expects has a home in the template — including `ledger`,
    without which no filled-in template can validate at all."""
    yaml = pytest.importorskip("yaml")
    tpl = yaml.safe_load(TEMPLATE.read_text())
    missing = [k for k in SchemaReadySpec.model_fields if k not in tpl]
    assert not missing, f"_TEMPLATE is missing spec keys: {missing}"


def test_template_references_only_paths_that_exist():
    """FOLLOWUPS #2: the template pointed at a worked example that did not exist. Any in-repo
    path it names must resolve, or a researcher chases a broken link on day one."""
    refs = {r.rstrip(".,);") for r in _REF.findall(TEMPLATE.read_text())}
    missing = sorted(r for r in refs if not (ROOT / r).exists())
    assert not missing, f"_TEMPLATE points at paths that do not exist: {missing}"
