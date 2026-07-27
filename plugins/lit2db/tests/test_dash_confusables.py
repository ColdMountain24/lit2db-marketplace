"""Typeset dashes must not split one entity into two rows.

Found in the wave-1 calibration slice, not by a test. One paper rendered
"neodolabella-1(14),2,7-triene synthase" with U+2010 HYPHEN in one place and plain U+002D in
another; the two aligned as different enzymes and produced two rows for one compound.

NFKC will not save you here. U+2010 is a distinct character rather than a compatibility form,
so `unicodedata.normalize` leaves it alone — the fold has to be explicit. That makes this
exactly the case D-035 reserves for normalization: the difference is typographic, so dissent
between passes carries no information and must not reach the agreement score.
"""
import pytest

from lit2db.ensemble import normalize, values_agree

# The real pair, from PMC12723471.
U2010 = "Chitinophaga japonensis Neodolabella‐1(14),2,7‐triene Synthase"
ASCII_ = "Chitinophaga japonensis Neodolabella-1(14),2,7-triene Synthase"


def test_the_entity_that_was_actually_split():
    assert values_agree(U2010, ASCII_), "one enzyme must not align as two"
    assert normalize(U2010) == normalize(ASCII_)


@pytest.mark.parametrize("dash,name", [
    ("‐", "HYPHEN"),
    ("‑", "NON-BREAKING HYPHEN"),
    ("‒", "FIGURE DASH"),
    ("–", "EN DASH"),
    ("—", "EM DASH"),
    ("−", "MINUS SIGN"),
])
def test_every_dash_a_typesetter_emits_folds_to_ascii(dash, name):
    assert values_agree(f"epi{dash}isozizaene", "epi-isozizaene"), name


def test_soft_hyphen_is_deleted_not_folded_to_a_hyphen():
    """A soft hyphen is a line-break HINT, not punctuation. It appears mid-word when a
    typesetter may break there, so folding it to '-' would invent a hyphen the chemistry
    never had — turning `epiisozizaene` into a different compound name."""
    assert normalize("epi­isozizaene") == "epiisozizaene"


def test_quotes_and_primes_fold_too():
    assert values_agree("3′-phosphate", "3'-phosphate")
    assert values_agree("“novel” synthase", '"novel" synthase')


def test_normalization_still_refuses_to_bridge_actual_content():
    """The boundary D-035 draws, and D-058 reaffirmed: typography yes, semantics never.
    A strain suffix is content, so these must stay different."""
    assert not values_agree("Kutzneria kofuensis DSM 43851", "Kutzneria kofuensis")
    assert not values_agree("2-MIB", "2-methylisoborneol")
