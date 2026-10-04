"""Uncertainty beside a measured value: spread type, spread values, sample size (D-203).

Why it exists. A value with no error bar cannot enter a roll-up across papers: weighting by
inverse variance needs a standard error, or something that converts to one exactly (a standard
deviation with its n, a confidence interval with its level). A schema that never asks for these
produces a database whose every value is a bare number, and a bare number can be listed but never
pooled. So a project can declare, for a numeric field, a group of seven companion fields that say
what the source printed beside it.

Three states, never two. A companion that is ABSENT means the reading did not answer. A companion
that says `not reported` means the reading looked and the source does not give it, which a judge
can check against the whole paper. A companion that says `0` is a reported zero. Collapsing any two
of these is how "nobody looked" ends up stored as "there was nothing", so the gate tells them apart
and a blank, `NR` or `n/a` is refused as unreadable rather than guessed at.

The vocabulary follows ClinicalTrials.gov's Measure of Dispersion/Precision (`MeasureDispersionType`
in API v2, checked against `/api/v2/studies/enums` on 2026-10-03): standard deviation, standard
error, inter-quartile range, full range, and confidence interval at a stated level. Its shape is
followed too: one `spread` for SD and SE, a `lowerLimit`/`upperLimit` pair for the others, and a
count per group. Two members are left out on purpose, `Not Applicable` and `Geometric Coefficient
of Variation`; a registry can demand a spread, a paper cannot, so `not reported` takes their place.
The confidence level is carried as its own number rather than as six enum members, so 95 and 97.5
are values, not vocabulary.

Domain-INVARIANT and STDLIB-ONLY, like `gate.py`, which imports it: the PreToolUse hook runs under
whatever interpreter the machine has. Nothing here knows what is being measured; the group is keyed
on field names the project ratified.
"""
from __future__ import annotations

import math
import re

NOT_REPORTED = "not reported"

STANDARD_DEVIATION = "standard deviation"
STANDARD_ERROR = "standard error"
CONFIDENCE_INTERVAL = "confidence interval"
INTERQUARTILE_RANGE = "interquartile range"
FULL_RANGE = "full range"

SPREAD_TYPES = (STANDARD_DEVIATION, STANDARD_ERROR, CONFIDENCE_INTERVAL, INTERQUARTILE_RANGE,
                FULL_RANGE, NOT_REPORTED)

ROLES = ("spread_type", "spread", "spread_lower", "spread_upper", "spread_level",
         "n_group", "n_total")

# Present whenever the measured value is, every time.
ALWAYS = ("spread_type", "n_group", "n_total")

# Which value companions each spread type uses. The rest must be absent for that type, with one
# exception: under `not reported`, a spread the source prints WITHOUT naming it (a bare ±, or a
# bracketed pair) may be carried, because it is what the paper printed and dropping it loses a fact.
USES = {
    STANDARD_DEVIATION: ("spread",),
    STANDARD_ERROR: ("spread",),
    CONFIDENCE_INTERVAL: ("spread_lower", "spread_upper", "spread_level"),
    INTERQUARTILE_RANGE: ("spread_lower", "spread_upper"),
    FULL_RANGE: ("spread_lower", "spread_upper"),
    NOT_REPORTED: (),
}
VALUE_ROLES = ("spread", "spread_lower", "spread_upper", "spread_level")
UNLABELLED_OK = ("spread", "spread_lower", "spread_upper")

_NUMBER = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")


def _norm(v) -> str:
    return re.sub(r"\s+", " ", v.strip().lower()) if isinstance(v, str) else ""


def is_not_reported(v) -> bool:
    """Exactly the sentinel, forgiving only case and spacing (typography, D-035)."""
    return _norm(v) == NOT_REPORTED


def companion_name(measured: str, role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"unknown uncertainty role {role!r} (have: {ROLES})")
    return f"{measured}_{role}"


def companion_names(groups) -> set:
    """Every field name any declared group uses."""
    out = set()
    for roles in (groups or {}).values():
        if isinstance(roles, dict):
            out.update(v for v in roles.values() if isinstance(v, str))
    return out


def _number(v):
    """A finite number, or None. A string counts only if it is a bare number: `0.12 %` is not."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str) and _NUMBER.match(v.strip()):
        f = float(v.strip())
    else:
        return None
    return f if math.isfinite(f) else None


def _whole(v):
    f = _number(v)
    return int(f) if f is not None and f == int(f) else None


def _count(v):
    """A sample size as printed: `("n", 6)`, `("range", (3, 5))`, or None if it is neither."""
    if isinstance(v, (list, tuple)):
        if len(v) != 2:
            return None
        a, b = _whole(v[0]), _whole(v[1])
        return ("range", (a, b)) if a is not None and b is not None and 1 <= a < b else None
    n = _whole(v)
    return ("n", n) if n is not None and n >= 1 else None


def companion_fields(measured: str, ledger_item_id: str, unit=None) -> list[dict]:
    """The seven companions for one numeric field, as FieldSpec-shaped dicts.

    Proposes STRUCTURE only. The definitions are domain-blind; a project ratifies them through its
    ledger like any other field, and `SchemaReadySpec` refuses them until it has.
    """
    m = measured
    cite = "ClinicalTrials.gov's Measure of Dispersion/Precision"
    same = f"same span as {m}, or the sentence that states it for the whole study"
    rows = [
        ("spread_type", "enum", None, list(SPREAD_TYPES),
         f"Which measure of spread the source prints beside {m}. 'not reported' when it prints "
         f"none, or prints a spread without saying which (a bare plus-or-minus). Given whenever "
         f"{m} is given. Vocabulary from {cite}, without its 'not applicable' and 'geometric "
         f"coefficient of variation' members."),
        ("spread", "float", unit, None,
         f"The one spread value for a standard deviation or standard error, in the unit of {m}; "
         f"also the number of an unlabelled plus-or-minus. 'not reported' when the type is known "
         f"and the number is not printed. Absent for the other types."),
        ("spread_lower", "float", unit, None,
         f"Lower limit of a confidence interval, interquartile range or full range, in the unit "
         f"of {m}. 'not reported' when the type is known and the limit is not printed."),
        ("spread_upper", "float", unit, None,
         f"Upper limit of a confidence interval, interquartile range or full range, in the unit "
         f"of {m}. 'not reported' when the type is known and the limit is not printed."),
        ("spread_level", "float", "%", None,
         f"The level of a confidence interval as a percentage (95 for a 95% interval). 'not "
         f"reported' when the source does not state it; never assumed to be 95. Present only for "
         f"a confidence interval."),
        ("n_group", "int", None, None,
         f"How many independent units (subjects, animals, samples) stand behind this one value of "
         f"{m}, as printed: a whole number of at least 1, [smallest, largest] when the source "
         f"gives only a range, or 'not reported'. Never 0 for unknown. Given whenever {m} is."),
        ("n_total", "int", None, None,
         f"How many units the experiment behind this value of {m} used across all its groups, in "
         f"the same forms as n_group. Given whenever {m} is."),
    ]
    return [{"name": companion_name(m, role), "type": typ, "unit": u, "enum": enum,
             "valid_range": None, "definition": definition, "provenance_granularity": same,
             "ledger_item_id": ledger_item_id, "auto_acceptable": True, "required": False,
             "uncertainty_of": m, "uncertainty_role": role}
            for role, typ, u, enum, definition in rows]


def uncertainty_reasons(record, groups, review_lane=()) -> list:
    """Every reason this record's declared uncertainty is unusable. Empty list means it is whole.

    `groups` is `{measured_field: {role: companion_field_name}}`, as `SchemaReadySpec.
    uncertainty_groups()` writes it. Empty means no condition, and the gate behaves exactly as it
    did before this existed. A group applies only to a record that carries its measured value: a
    row with nothing measured has nothing to qualify.

    Review-lane fields read as absent, because `output.upsert` strips them from the row.
    Fails CLOSED on a declaration it cannot read.
    """
    if not groups:
        return []
    if not isinstance(groups, dict) or not all(isinstance(r, dict) for r in groups.values()):
        return [f"uncertainty declaration unreadable ({type(groups).__name__}); a condition "
                f"this gate cannot read must never be read as satisfied"]
    lane = set(review_lane or ())
    present = {}
    for fv in (record or {}).get("fields") or []:
        if isinstance(fv, dict) and fv.get("value") is not None \
                and fv.get("field_name") not in lane:
            present[fv.get("field_name")] = fv.get("value")

    reasons = []
    for measured in sorted(groups):
        if measured not in present:
            continue
        roles = groups[measured]
        tag = f"uncertainty of '{measured}'"

        def absent(role, why=""):
            reasons.append(f"{tag}: '{roles.get(role, role)}' is absent{why}. A source that "
                           f"gives none is recorded as 'not reported', which is a different fact "
                           f"from a reading that did not answer")

        def unreadable(role, v):
            reasons.append(f"{tag}: '{roles.get(role, role)}' = {v!r} is neither a number nor "
                           f"exactly 'not reported'. A blank, 'NR' or 'n/a' cannot be told apart "
                           f"from a value nobody read")

        missing_roles = [r for r in ROLES if r not in roles]
        if missing_roles:
            reasons.append(f"{tag}: the declaration names no field for {missing_roles}; a "
                           f"partial group cannot be checked")
            continue

        # The three that are always required.
        t = None
        raw_t = present.get(roles["spread_type"])
        if roles["spread_type"] not in present:
            absent("spread_type")
        elif _norm(raw_t) not in SPREAD_TYPES:
            reasons.append(f"{tag}: '{roles['spread_type']}' = {raw_t!r} is not one of "
                           f"{list(SPREAD_TYPES)}")
        else:
            t = _norm(raw_t)

        counts = {}
        for role in ("n_group", "n_total"):
            name = roles[role]
            if name not in present:
                absent(role)
                continue
            v = present[name]
            if is_not_reported(v):
                continue
            if _whole(v) == 0:
                reasons.append(f"{tag}: '{name}' = 0 is not a sample size. When the source "
                               f"gives none the answer is 'not reported', never 0")
                continue
            c = _count(v)
            if c is None:
                reasons.append(f"{tag}: '{name}' = {v!r} is not a whole number of at least 1, "
                               f"a [smallest, largest] range, or 'not reported'")
                continue
            counts[role] = c
        g, tot = counts.get("n_group"), counts.get("n_total")
        if g and tot and g[0] == tot[0] == "n" and g[1] > tot[1]:
            reasons.append(f"{tag}: n_group {g[1]} is larger than n_total {tot[1]}")

        if t is None:
            continue     # the value companions are read against the type; without it, no rule

        # The value companions, read against the type.
        uses = USES[t]
        nums = {}
        for role in VALUE_ROLES:
            name = roles[role]
            here = name in present
            if role in uses:
                if not here:
                    reasons.append(f"{tag}: a '{t}' needs '{name}', which is absent. Write 'not "
                                   f"reported' if the source does not print it")
                    continue
            elif here and not (t == NOT_REPORTED and role in UNLABELLED_OK):
                reasons.append(f"{tag}: '{name}' is given, but a '{t}' has no such value. A "
                               f"spread whose type and values disagree cannot be converted to a "
                               f"standard error, so it is held")
                continue
            elif not here:
                continue
            v = present[name]
            if is_not_reported(v):
                continue
            f = _number(v)
            if f is None:
                unreadable(role, v)
                continue
            nums[role] = f

        if "spread" in nums and nums["spread"] < 0:
            reasons.append(f"{tag}: '{roles['spread']}' = {nums['spread']} is negative; a "
                           f"spread cannot be")
        lo, hi = nums.get("spread_lower"), nums.get("spread_upper")
        if lo is not None and hi is not None and lo > hi:
            reasons.append(f"{tag}: lower limit {lo} is above upper limit {hi}")
        if t == NOT_REPORTED and ((roles["spread_lower"] in present)
                                  != (roles["spread_upper"] in present)):
            reasons.append(f"{tag}: an unlabelled interval needs both '{roles['spread_lower']}' "
                           f"and '{roles['spread_upper']}', or neither")
        lv = nums.get("spread_level")
        if lv is not None and not 0 < lv < 100:
            reasons.append(f"{tag}: confidence level {lv} is not a percentage between 0 and 100")
    return reasons
