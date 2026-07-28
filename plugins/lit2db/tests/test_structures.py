"""Structures are RESOLVED, never generated (D-084).

The extractor pulls a compound name — quotable, in the text, grounds normally — and the pipeline
asks a public authority what that name is. A model that never writes a SMILES cannot hallucinate
one, which is why this is a resolution problem rather than a verification problem.

Network is injected, so every case here runs offline and none of it mocks urllib internals.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lit2db.structures import resolve_structure, structure_fields  # noqa: E402

PINENE = {"PropertyTable": {"Properties": [{
    "CID": 6654, "CanonicalSMILES": "CC1=CCC2CC1C2(C)C",
    "InChI": "InChI=1S/C10H16/c1-7-4-5-8-6-9(7)10(8,2)3/h4,8-9H,5-6H2,1-3H3",
    "InChIKey": "GRWFGVWFFZKLTI-UHFFFAOYSA-N", "MolecularFormula": "C10H16",
    "MolecularWeight": 136.23, "ExactMass": 136.125}]}}


def _fetch(payload):
    return lambda url, timeout_s=20.0: payload


# --- the happy path -----------------------------------------------------------------------
def test_a_name_resolves_to_a_structure_the_model_never_wrote():
    r = resolve_structure("alpha-pinene", fetch=_fetch(PINENE))
    assert r["resolved"] is True
    assert r["inchikey"] == "GRWFGVWFFZKLTI-UHFFFAOYSA-N"
    assert r["authority"] == "pubchem" and r["authority_id"] == "6654"


def test_resolved_fields_carry_the_authority_as_their_evidence():
    """A structure's provenance is the lookup, not a span. That is the whole point: a SMILES
    cannot cite a sentence, so it cites the database record instead."""
    fields = structure_fields(resolve_structure("alpha-pinene", fetch=_fetch(PINENE)),
                              source_id="PMC1", producing_process="test@1")
    by_name = {f["field_name"]: f for f in fields}
    assert "smiles" in by_name and "inchikey" in by_name
    prov = by_name["smiles"]["provenance"]
    assert prov["kind"] == "structured"
    assert prov["database"] == "pubchem" and prov["record_id"] == "6654"
    assert by_name["smiles"]["confidence_components"]["_grounding_mode"] == "authority_resolved"


def test_the_pin_is_a_retrieval_date_and_says_so():
    """PubChem publishes no release version. `db_version` exists because an unpinned value is
    unreproducible, so the field states what it actually is rather than inventing a version."""
    fields = structure_fields(resolve_structure("alpha-pinene", fetch=_fetch(PINENE)),
                              source_id="PMC1", producing_process="test@1")
    assert fields[0]["provenance"]["db_version"].startswith("retrieved:")


# --- the refusals, which are the load-bearing half ----------------------------------------
def test_an_ambiguous_name_resolves_to_nothing():
    """Names lose stereochemistry, and for terpenoids that is the difference that matters. A
    confident wrong identifier would discredit the whole database, so ambiguity is a non-answer.
    """
    two = {"PropertyTable": {"Properties": [
        {"CID": 1, "InChIKey": "AAAAAAAAAAAAAA-UHFFFAOYSA-N"},
        {"CID": 2, "InChIKey": "BBBBBBBBBBBBBB-UHFFFAOYSA-N"}]}}
    r = resolve_structure("cadinene", fetch=_fetch(two))
    assert r["resolved"] is False and r["ambiguous"] is True
    assert r["candidates"] == [1, 2], "the candidates are reported, not silently discarded"


def test_an_unreachable_authority_fails_closed():
    assert resolve_structure("anything", fetch=_fetch(None))["resolved"] is False


def test_no_match_is_a_finding_not_an_error():
    """A name absent from every public database is very likely a NOVEL compound — which is the
    interesting case, and the paper's actual contribution."""
    r = resolve_structure("hypothetical-novel-terpenoid", fetch=_fetch({"PropertyTable": {}}))
    assert r["resolved"] is False and "no match" in r["why"]


def test_a_result_without_an_inchikey_is_refused():
    """Without an InChIKey there is nothing to compare structures on later."""
    partial = {"PropertyTable": {"Properties": [{"CID": 9, "MolecularFormula": "C15H24"}]}}
    assert resolve_structure("x", fetch=_fetch(partial))["resolved"] is False


def test_an_unresolved_name_contributes_no_fields_and_blocks_nothing():
    """D-083: not every entry needs a structure. Attempting a resolution must never be worse
    than skipping it, or the extractor is incentivised never to try."""
    assert structure_fields({"resolved": False}, "PMC1", "test@1") == []
    assert structure_fields({}, "PMC1", "test@1") == []


def test_formula_is_never_used_as_identity_evidence():
    """Measured on the collaborator's own 1,062 compounds: 642 distinct formulas, and 44 of them
    share C15H24. A formula agreement is close to no evidence in this domain, so nothing here
    resolves on one."""
    formula_only = {"PropertyTable": {"Properties": [
        {"CID": 1, "MolecularFormula": "C15H24"}, {"CID": 2, "MolecularFormula": "C15H24"}]}}
    assert resolve_structure("a sesquiterpene", fetch=_fetch(formula_only))["resolved"] is False


# --- "we never asked" is not "we asked and it does not know" (D-094) -------------------------

def test_a_transport_failure_is_not_reported_as_a_non_match():
    """The bug this pins was total and silent: a python.org build with no system trust store
    failed TLS on every call, and the fetcher's fail-closed `None` made that indistinguishable
    from PubChem not knowing the name. A whole wave would have read as "none of these compounds
    are in PubChem" — plausible for a novel-compound database, and completely wrong."""
    from lit2db.structures import _TRANSPORT, resolve_structure
    r = resolve_structure("pentalenene", fetch=lambda url, t=20.0: _TRANSPORT)
    assert r["resolved"] is False
    assert r.get("unreachable") is True
    assert "not asked" in r["why"]


def test_a_real_non_match_is_still_a_real_finding():
    """The authority answered and does not know the name — which for a database of NEW compounds
    is often the interesting outcome, not a failure."""
    from lit2db.structures import resolve_structure
    r = resolve_structure("pentalenene", fetch=lambda url, t=20.0: None)
    assert r["resolved"] is False
    assert not r.get("unreachable")
    assert r["why"] == "no match in authority"


def test_the_default_fetcher_trusts_a_real_ca_bundle():
    """Not a network test — just that a context is constructible, which is what failed."""
    from lit2db.structures import _ssl_context
    assert _ssl_context() is not None
