"""Render a ratified spec into the context an extraction or grounding call can be given (D-111).

## Why this exists

Every grounding call in the 183-paper wave saw a value, a quote, and nothing else. It did not
know the database collects novel bacterial terpenoids, that a re-isolation does not earn an
entry, or what `evidence_basis` is allowed to contain. The extractor saw only its prompt. Both
were working blind against a spec the researcher had already written down.

## The boundary, and it is the whole point

D-111 draws it explicitly: context assembled **from the ratified spec** is the researcher's own
words carried forward — plumbing something that already exists. Context an agent **researches for
itself** is new domain substance and must be ratified before it shapes extraction; that is a
Stage-0.5 extension, not a pipeline change.

This module does only the first. It formats; it never adds. The guard is not a convention but a
type: input is routed through `SchemaReadySpec`, whose validators refuse to build a spec whose
fields do not trace to ACCEPTED ledger items, or whose literature corpus has no ratified query.
So an unratified field structurally cannot reach a prompt through this function.

## What an EMPTY section means, and why it is printed rather than omitted

If `controlled_vocab_bindings` is empty this renders "none ratified for this project" instead of
dropping the heading. Silence reads as "this project has no naming conventions"; the truth is
usually "nobody has ratified any yet", and those are different facts. Measured 2026-07-29
(D-112): the single unstable name judgement across both shadow-grounding arms was `sp. RJA2961`
against *"the Streptomyces strain RJA2961"* — a `sp.`/`strain` equivalence that is **not** in the
frozen compound spec. Printing the absence is what makes that gap visible instead of inferrable.
"""
from __future__ import annotations

import json
import pathlib
from typing import Union

from .contracts.spec import SchemaReadySpec

SpecLike = Union[SchemaReadySpec, dict, str, pathlib.Path]


def load_spec(spec: SpecLike) -> SchemaReadySpec:
    """Coerce to a validated `SchemaReadySpec`, or raise.

    A path or dict goes through the model rather than around it. That is the enforcement point:
    `_every_field_ratified` and `_corpus_is_defined_not_just_named` run here, so a spec carrying
    an unratified field cannot be rendered into a prompt at all.
    """
    if isinstance(spec, SchemaReadySpec):
        return spec
    if isinstance(spec, (str, pathlib.Path)):
        spec = json.loads(pathlib.Path(spec).read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise TypeError(f"cannot read a spec from {type(spec).__name__}")
    return SchemaReadySpec.model_validate(spec)


def _field_line(f) -> list[str]:
    head = f"- {f.name} ({f.type}"
    if f.unit:
        head += f", in {f.unit}"
    head += f"): {f.definition}"
    out = [head]
    if f.enum:
        out.append(f"    exactly one of: {' | '.join(f.enum)}")
    if f.valid_range:
        lo, hi = f.valid_range
        out.append(f"    valid range: {lo} to {hi}")
    if f.provenance_granularity:
        out.append(f"    what distinguishes two records: {f.provenance_granularity}")
    return out


def spec_context(spec: SpecLike, *, only_fields: list[str] | None = None) -> str:
    """The ratified spec, rendered for a model. Formats only; adds nothing.

    `only_fields` narrows the field list for a per-field call (a grounding check needs the one
    field's definition, not all ten). Unknown names raise rather than silently returning less
    context than the caller asked for.
    """
    s = load_spec(spec)
    fields = s.fields
    if only_fields is not None:
        by_name = {f.name: f for f in s.fields}
        unknown = [n for n in only_fields if n not in by_name]
        if unknown:
            raise KeyError(f"not fields of this spec: {unknown} "
                           f"(has: {sorted(by_name)})")
        fields = [by_name[n] for n in only_fields]

    L: list[str] = []
    L.append(f"# What this database is collecting  (ratified spec {s.spec_version})")
    L.append("")
    L.append(s.research_question)
    L.append("")
    L.append(f"Unit of analysis — what one row is: {s.unit_of_analysis}")
    L.append("")
    L.append(f"Sources reporting nothing that qualifies: {s.negative_data_policy}")

    L.append("")
    L.append("# What counts as in scope")
    if s.inclusion_exclusion:
        for k, v in s.inclusion_exclusion.items():
            L.append(f"- {k}: {v}")
    else:
        L.append("- No inclusion or exclusion rules are ratified for this project.")

    L.append("")
    L.append("# The fields, as ratified")
    for f in fields:
        L.extend(_field_line(f))

    L.append("")
    L.append("# Naming conventions ratified for this project")
    if s.controlled_vocab_bindings:
        for k, v in s.controlled_vocab_bindings.items():
            L.append(f"- {k}: {v}")
    else:
        L.append("- None ratified for this project. Where the source writes a name differently "
                 "from the value you are checking, judge it on the source's own wording; do not "
                 "assume an equivalence this project has not ratified.")

    L.append("")
    L.append("Everything above is the researcher's ratified specification. It tells you what is "
             "being collected and why; it does not tell you what any particular source says. "
             "Do not treat it as evidence about a source.")
    return "\n".join(L)
