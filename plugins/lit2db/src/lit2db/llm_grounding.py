"""The inversion (D-110): the model decides support, determinism CONSTRAINS it (D-112).

## What actually changes, and what must not

Grounding asks one question — *does this text support this value?* — and a lexical rule was always
worst at it. D-109 measured the cost: of the collaborator's 424 compounds in the 183-paper wave,
**87 were found and then denied**, and on full-text papers the gate denied more than it wrote.

Everything else stays exactly as hard as it was, because none of it was ever the problem. A span
must be cited. The offset must resolve in the store. The source must be active. The record-id
collision refusal stands. Provenance must be complete. The verdict is recorded and replayable.
Those are FACTS, checked by `gate.py`, and this module touches none of them.

## Why the model is asked more than once

D-112 pre-registered the bar before the measurement existed: under 2% of judgements changing
between runs meant the model could decide directly; at or above 2% the wrapper had to ask more
than once and require the answers to match. The singleton arm measured **2.78%**, so the bar
fired and repeat-and-agree is required — **on every field, then narrowed**, rather than scoped to
where instability happened to appear. Narrowing on the same data that fired the rule would be
re-cutting a pre-registration after seeing it.

## Three states, not two

`not_run` != `unsupported` != `unstable`, and collapsing them is a mistake this project has now
made and fixed three times (`not_run` != `clean` for the hunter, `not_run` != `supported` for the
judge, `cant_tell` != wrong for adjudication). All three fail closed at the gate, but they are
different facts and a question catalogue that cannot tell them apart cannot be acted on:

- `supported`   — every answer said yes. The only state that scores 1.0.
- `unsupported` — every answer said no. The value is not in its quote; that is a real result.
- `unstable`    — the answers disagreed. **Not resolved here.** The grounder does not get to
  break its own tie, because a tie-break is exactly the judgement the repeat was measuring.
- `not_run`     — fewer than two usable answers. A call that never executed, or whose reply could
  not be read, is not evidence of anything.

## The prompt is frozen on purpose

`GROUNDING_PROMPT` is byte-identical to the one D-112's flip rate was measured with. The stability
figure is a property of *that* prompt; reword it and the 2.78% no longer describes what is
running. Changing it means re-running the arm.
"""
from __future__ import annotations

import json
import re

# Byte-identical to analysis/calibration-2026-07-28/shadow_grounding.py. Do not edit without
# re-running the stability arm — see the module docstring.
GROUNDING_PROMPT = """You are checking whether a quoted sentence from a scientific paper SUPPORTS an
extracted value. This is a grounding check, not a plausibility check.

Answer YES only if a careful reader would agree the quote asserts that value — including when the
paper writes it differently (a series list, an abbreviation, different punctuation, an
interposed word, a typo). Answer NO if the quote does not assert it, even if the value seems
likely to be true.

Return ONLY a JSON array, one object per item: {{"i": <index>, "supports": true|false}}

Items:
{items}"""

STATES = ("supported", "unsupported", "unstable", "not_run")


def build_prompt(value: object, quote: str, spec_context: str | None = None) -> str:
    """One pair per call — the condition D-112 measured as strictly better than batching.

    All three pairs where the batched and singleton arms disagreed went `batched=NO ->
    singleton=YES`: sharing a question with eleven neighbours SUPPRESSED support. So the
    production path asks about one pair, and the item list has exactly one entry.

    `spec_context` is D-111's ratified scope, prepended rather than interpolated into the frozen
    prompt so the measured text stays byte-identical below it.
    """
    items = f"0. value={str(value)!r}\n   quote={str(quote)!r}"
    body = GROUNDING_PROMPT.format(items=items)
    if spec_context:
        return f"{spec_context}\n\n---\n\n{body}"
    return body


def parse_verdict(text: str) -> bool | None:
    """The single verdict in a reply, or None if it cannot be read as exactly one.

    Strictness is the point: an unreadable reply must become `not_run`, never a guess. A guess
    would be indistinguishable from a real answer and would silently enter the agreement check
    as though the model had spoken.

    Accepts the one-element array the prompt asks for, and the bare object the model sometimes
    returns when asked about a single item — a format variation D-112 hit on a real record whose
    verdict was stable across all six attempts. Anything else, including two verdicts where one
    was asked for, is unreadable.
    """
    if not text:
        return None
    m = re.search(r"\[.*\]", text, re.S)
    payload = None
    if m:
        try:
            arr = json.loads(m.group(0))
        except Exception:
            return None
        if not isinstance(arr, list) or len(arr) != 1:
            return None
        payload = arr[0]
    else:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            payload = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(payload, dict) or "supports" not in payload:
        return None
    if "i" in payload:
        try:
            if int(payload["i"]) != 0:
                return None
        except Exception:
            return None
    v = payload["supports"]
    return v if isinstance(v, bool) else None


def resolve(verdicts, *, min_answers: int = 2) -> dict:
    """Repeat-and-agree (D-112). Pure: no model, no I/O, so replay reruns it for free.

    `verdicts` is what the repeats returned — `True`, `False`, or `None` for a call that did not
    execute or could not be read. Nones are DISCARDED rather than counted as dissent: a failed
    call is not the model disagreeing with itself.
    """
    answers = [v for v in verdicts or [] if isinstance(v, bool)]
    n = len(answers)
    if n < min_answers:
        # Fails closed, and deliberately does not fall back to a single answer. One reading is
        # the condition the pre-registered bar refused.
        return {"state": "not_run", "c_grounded": 0.0, "n_answers": n,
                "verdicts": list(verdicts or [])}
    if all(answers):
        return {"state": "supported", "c_grounded": 1.0, "n_answers": n,
                "verdicts": list(verdicts)}
    if not any(answers):
        return {"state": "unsupported", "c_grounded": 0.0, "n_answers": n,
                "verdicts": list(verdicts)}
    return {"state": "unstable", "c_grounded": 0.0, "n_answers": n, "verdicts": list(verdicts)}
