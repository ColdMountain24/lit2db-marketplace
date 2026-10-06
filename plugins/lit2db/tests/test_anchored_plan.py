"""plan() can state a claim's identity as an anchor alone (review packs, D-201/D-206)."""
import pytest
def test_identity_declaration_accepts_anchor_levels_and_an_empty_chain():
    from lit2db.project import _identity_declaration
    decl = _identity_declaration([], ["@sentence"], {"claim_text", "direction"})
    assert decl == {"chain": [], "ordinal_within": ["@sentence"]}
    with pytest.raises(ValueError):
        _identity_declaration([], ["not_a_field"], {"claim_text"})
