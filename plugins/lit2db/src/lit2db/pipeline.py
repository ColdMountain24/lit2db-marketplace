"""The pipeline — assemble, select, schedule the judge, catalogue what a human must answer.

WHY THIS MODULE EXISTS, and it is the point of the whole cleanup.

`src/lit2db/stages/` used to declare this pipeline: nine functions named for the nine stages,
under a docstring reading "This is the domain-INVARIANT control flow." **Eight had empty bodies
and nothing imported the package.** The control flow actually lived in `scripts/run_wave.py` —
1208 lines holding the real assembly, the real ordering, the real provenance construction — and
it reached scoring and gating by loading the MCP *server file* as a module to borrow functions
out of it.

So the library was a specification and the script was the system, and every defect this project
found in its first week was the gap between them: an agent declaring tools it did not hold,
weights for signals nothing produced, a stage recorded as "found nothing" that never ran, an
audit slice reporting three having judged two. One bug class, twelve instances, recurring because
each fix re-described the specification to match the script instead of making them one artifact.

**The half that declared was also the half that shipped.** A researcher installing the plugin got
the specification; we hardened the script. What runs is now what installs.

Model invocation arrives as an INJECTED CALLABLE, so nothing here spawns a process and the
pipeline is testable without a model. Process concerns — resume, manifests, token accounting,
sleeping through usage limits — stay in the driver, which is what a driver is for.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re

from .contracts.provenance import process_fingerprint
from .grounding import ground_literature
from .gate import selection_reasons
from .store import find_spans, section_of


# A judge verdict is an ordinal STATE, never a probability (D-079). It used to be mapped onto
# {1.0, 0.5, 0.0} and averaged into the confidence composite, which described it as one signal
# among six; it never behaved like one, because it could only ever lower a score. It is now a
# veto carried on the record and applied at the gate. PARTIAL still blocks — under the old mean
# it scored 0.885 against a 0.95 bar, so tolerating it here would have quietly loosened the gate
# while the change was being described as behaviour-preserving.
VERDICT_TO_STATE = {"SUPPORTED": "supported", "PARTIAL": "partial",
                    "UNSUPPORTED": "unsupported"}



def _extract_json(text: str) -> dict | None:
    """Pull one JSON object out of a model reply.

    Tried in order: a fenced ```json block, the widest brace span, then each brace span from
    the outside in. A single greedy `{...}` fails whenever the reply also contains prose with
    braces, and a bare `json.loads` fails on the fenced form models most often produce.
    """
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    candidates = ([fenced.group(1)] if fenced else [])
    span = re.search(r"\{[\s\S]*\}", text)
    if span:
        candidates.append(span.group(0))
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    return None


_VERDICT = r"SUPPORTED|PARTIAL|UNSUPPORTED"


def _parse_verdicts(text: str, ids: list) -> dict:
    """Map record_id -> the judge's full verdict object, from a single- or multi-claim reply.

    Structured first, regex only as a last resort. The old code was regex-only against
    `"verdict": "..."`, which threw away `weakest_supported_claim`, `reasoning` and
    `overreach` even when it succeeded — the parts a human actually needs to audit a denial.

    Order matters for the fallback: a batched reply may carry several verdicts with no ids
    attached, and pairing the Nth verdict with the Nth requested id is a GUESS. It is labelled
    as one (`by_position: True`) rather than presented as the judge's answer, because a
    mis-paired verdict is worse than a missing one — it attributes a judgement to a record
    nobody made it about.
    """
    out = {}
    blob = _extract_json(text)
    objs = []
    if isinstance(blob, dict):
        objs = blob.get("verdicts") if isinstance(blob.get("verdicts"), list) else [blob]
    if not objs:
        arr = re.search(r"\[[\s\S]*\]", text)
        if arr:
            try:
                cand = json.loads(arr.group(0))
                objs = cand if isinstance(cand, list) else []
            except json.JSONDecodeError:
                objs = []
    for o in objs or []:
        if not isinstance(o, dict):
            continue
        v = str(o.get("verdict", "")).upper()
        if not re.fullmatch(_VERDICT, v):
            continue
        # A single-claim reply carries no `record_id` because it does not need one — the
        # prompt asked about exactly one claim. Attributing it to that claim is unambiguous,
        # and NOT doing so was silently dropping every unbatched judgement to the regex path,
        # discarding the reasoning this function exists to keep.
        rid = o.get("record_id") if o.get("record_id") in ids else (
            ids[0] if len(ids) == 1 else None)
        if rid is not None:
            out[rid] = {k: o.get(k) for k in
                        ("verdict", "weakest_supported_claim", "reasoning", "overreach")}
    if len(ids) == 1 and not out:
        m = re.search(rf'"verdict"\s*:\s*"({_VERDICT})"', text)
        if m:
            out[ids[0]] = {"verdict": m.group(1), "by_regex": True}
    if not out and len(ids) > 1:
        found = re.findall(rf'"verdict"\s*:\s*"({_VERDICT})"', text)
        if len(found) == len(ids):
            for rid, v in zip(ids, found):
                out[rid] = {"verdict": v, "by_position": True, "by_regex": True}
    return out



def assemble(paper: str, cfg: dict, merged: dict, hunt: dict) -> tuple[list, list]:
    """Merged values + provenance + hunter -> records the spine can score.

    `merge_passes` returns values WITHOUT provenance — it computes agreement, not evidence — so
    each modal value's quote is re-attached from whichever pass produced it, and the offset is
    resolved from the store rather than trusted from an agent.

    **No judge argument (D-079.)** Assembly now runs BEFORE the judge, because scoring is what
    decides who is worth judging. Verdicts are stamped on afterwards by `apply_verdicts`.
    """
    store = pathlib.Path(cfg["stores"]) / paper
    full = (store / "full.txt").read_text(encoding="utf-8")
    sdict = {"full_text": full, "sections": json.loads((store / "sections.json").read_text())}
    fingerprint = process_fingerprint(
        pathlib.Path(cfg["extract_prompt"]).read_text(encoding="utf-8"))
    passes = merged["_passes"]

    def quote_for(acc, name, value):
        for p in passes:
            for rec in p or []:
                a = next((f.get("value") for f in rec["fields"]
                          if f["field_name"] == cfg["identity_primary"]), None)
                if (a or "").lower() != (acc or "").lower():
                    continue
                for f in rec["fields"]:
                    if f["field_name"] == name and f.get("value") == value:
                        return f.get("verbatim_quote")
        return None

    out, dropped = [], []
    for rec in merged["records"]:
        acc = next((f.get("value") for f in rec["fields"]
                    if f["field_name"] == cfg["identity_primary"]), None)
        fields = []
        for f in rec["fields"]:
            name, value = f["field_name"], f.get("value")
            if value is None:
                continue
            quote = quote_for(acc, name, value)
            # A MULTI-VALUED FIELD MAY COME BACK WITH ONE QUOTE PER ELEMENT. Models mirror the
            # value's shape, so a `list[str]` field gets a list of quotes — which is arguably
            # the better evidence (D-061 already grounds lists per element) but reached
            # `find_spans` as a list and threw, killing the whole paper. Under paper isolation
            # that is worse than a crash: the paper is recorded as failed and silently lost.
            # Each element is anchored on its own; the first that resolves carries the offset,
            # and the joined text stays as the quote so nothing claims an anchor it lacks.
            if isinstance(quote, (list, tuple)):
                parts = [q for q in quote if isinstance(q, str) and q.strip()]
                hits = next((h for h in (find_spans(full, q) for q in parts) if h), [])
                quote = " | ".join(parts) if parts else None
            else:
                hits = find_spans(full, quote) if quote else []
            if not hits:
                # An unanchorable quote is a real outcome: the value does not get written on it.
                dropped.append({"record": rec["record_id"], "field": name,
                                "why": "no quote" if not quote else "quote not in full.txt"})
                continue
            off = hits[0]["start"]
            cc = dict(f.get("confidence_components") or {})
            # k=1 HAS NO AGREEMENT, and must not report one. `merge_passes` computes agreement
            # over whatever passes it was given, so a single pass agrees with itself and emits
            # c_ensemble=1.0 — a free full mark on the signal the accept bar leans on hardest.
            # `required_agreement` already refuses k<2 for exactly this reason; that guard fires
            # on the BAR while this is the SIGNAL, and stopping the exception without dropping
            # the value left the flattery in place. Removing the key routes the field on the
            # signals actually measured, which is what "leave c_ensemble unset" meant.
            if len(merged.get("_passes") or []) < 2:
                cc.pop("c_ensemble", None)
            if name in cfg.get("evidence_grounded_fields", []):
                # D-061: a controlled-vocabulary value is never verbatim in a paper — no source
                # contains the string "biochemically_characterized". Grounding it lexically
                # scores a CORRECT value 0.0. So ground the evidence instead: reaching this
                # line means the quote anchored in full.txt, and whether that quote supports
                # the classification is the judge's call, not a substring test's.
                cc["c_grounded"] = 1.0
                cc["_grounding_mode"] = "evidence_anchored"
            else:
                cc["c_grounded"] = ground_literature(value=value, quote=quote)["c_grounded"]
            # No `c_judge` is written here any more. The verdict is a property of the RECORD
            # (D-036) and a veto rather than a score (D-079); copying it onto every field gave a
            # record-level fact a field-level shape and put it inside a mean it never behaved like.
            fv = {"field_name": name, "value": value, "confidence_components": cc,
                  "provenance": {
                      "kind": "literature", "source_id": paper,
                      "retrieval_timestamp": cfg["run_timestamp"],
                      "producing_process": cfg["producing_process"],
                      "process_fingerprint": fingerprint,
                      "verbatim_quote": quote, "char_offset": off,
                      "section": section_of(sdict, off) or "unknown",
                      "source_status": "active",
                      "source_chars_total": len(full), "source_chars_read": len(full)}}
            # PER FIELD, not per paper. `contradiction_search` describes THIS value: `found`
            # only when a span argues against this field, `clean` when the hunter searched and
            # nothing did. Propagating the paper-level verdict to every field made one real
            # contradiction mark all ten fields `found`, which the gate then blocked with
            # "not searched is not clean" — a message that is simply untrue of a field the
            # hunter did read. `not_run` stays reserved for a hunter that never completed.
            searched = hunt["state_by_record"].get(rec["record_id"], "not_run")
            evid = []
            for c in hunt.get("contradictions", []):
                if c.get("applies_to") != [rec["record_id"], name]:
                    continue
                h = find_spans(full, c.get("verbatim_quote", ""))
                if not h:                      # never fabricate an anchor
                    dropped.append({"record": rec["record_id"], "field": name,
                                    "why": "hunter quote unanchorable"})
                    continue
                evid.append({"verbatim_quote": c["verbatim_quote"],
                             "char_offset": h[0]["start"], "kind": c["kind"],
                             "explanation": c["explanation"]})
            if evid:
                fv["contradictions"] = evid
            fv["contradiction_search"] = (
                "not_run" if searched == "not_run" else ("found" if evid else "clean"))
            fields.append(fv)
        if fields:
            r_out = {"record_id": rec["record_id"], "entity_type": rec["entity_type"],
                     "fields": fields}
            # D-067: a record the ratified criteria say can never auto-accept is ROUTED, not
            # denied silently. `route` already blocks in `gate_reasons` — no new gate mechanism
            # was needed, only a way to declare the rule and carry it through the merge. The
            # record keeps every field and its full provenance so the reviewer sees what was
            # extracted, and the reasons say why it is in front of them.
            if rec.get("review_only"):
                # `route` is what BLOCKS (gate.BLOCKING_ROUTES); the reasons are for the human
                # holding the record. They go in `review_reasons`, not `failure_reason` — that
                # is a five-value enum, and joining free text into it raised on every
                # review-lane record, taking the whole paper down with it.
                r_out["route"] = "human_review"
                r_out["review_reasons"] = [str(x) for x in
                                           (rec.get("review_reasons") or ["review-only"])]
            out.append(r_out)
    return out, dropped



# --- who is worth an adversarial read (D-079 / D-081) -----------------------------------
# A rejected record is AUDITABLE only if it was turned down on evidence. The other three
# rejection classes are turned down for reasons a verdict cannot overturn, so judging them
# spends budget without measuring anything about the veto:
#   status  — the source is retracted or superseded. Not a judgement call at all.
#   policy  — a ratified review-only record (D-067). The researcher already ruled on it.
#   process — the counter-evidence search never completed, so the record was never really tried.
# What survives is thin evidence and contradicted-by-its-own-source. The second is in the frame
# on purpose: it is the one place the contradiction hunter and the adversarial judge read the
# same claim independently, and whether they agree is worth knowing.
DENIAL_STATUS, DENIAL_POLICY, DENIAL_PROCESS = "status", "policy", "process"
DENIAL_THIN, DENIAL_CONTRADICTED = "thin_evidence", "contradicted"


def denial_class(scored: dict) -> str:
    """Why this record was turned down, read off the record itself rather than off prose.

    Classifying by matching the gate's reason STRINGS would work today and break the first time
    somebody rewords a message. Every fact needed is already structured on the record.
    """
    fields = scored.get("fields") or []
    for fv in fields:
        prov = fv.get("provenance") or {}
        status = getattr(prov.get("source_status"), "value", prov.get("source_status"))
        if status is not None and status != "active":
            return DENIAL_STATUS
    route = getattr(scored.get("route"), "value", scored.get("route"))
    if route in ("human_review", "quarantine"):
        return DENIAL_POLICY
    for fv in fields:
        searched = getattr(fv.get("contradiction_search"), "value",
                           fv.get("contradiction_search"))
        if searched == "not_run":
            return DENIAL_PROCESS
    for fv in fields:
        if fv.get("contradictions"):
            return DENIAL_CONTRADICTED
    return DENIAL_THIN


def audit_slice(record_ids: list, fraction: float, salt: str) -> list:
    """A deterministic, resume-stable sample of `record_ids`, of size ceil(fraction * n).

    Deterministic because a resumed paper must re-draw the SAME rows: a fresh random draw on
    every leg would judge a different sample each time, and the reject-side rate would then be
    measured over a set nobody can reconstruct. Hashing (salt, id) rather than seeding a PRNG
    also means the sample does not shift when the record ORDER changes, which it does whenever
    the identity chain resolves a paper differently.

    `ceil` rather than `round`, so a non-zero fraction always yields at least one audited record
    when there is anything to audit — a wave that quietly audited nothing would report a saving
    it had not earned.
    """
    if not record_ids or fraction <= 0:
        return []
    n = min(len(record_ids), math.ceil(fraction * len(record_ids)))
    keyed = sorted(record_ids, key=lambda r: hashlib.blake2b(
        f"{salt}|{r}".encode("utf-8"), digest_size=16).hexdigest())
    return sorted(keyed[:n])


def select_for_judging(scored: list, cfg: dict, salt: str) -> dict:
    """Partition scored records into those that get an adversarial read and those that do not.

    THIS FUNCTION IS THE SAVING. Under the old order every merged record was judged before
    anything knew which records a verdict could affect; measured over 165 records, 139 of those
    calls could not have changed any outcome, because at the 0.95 bar only a unanimous,
    fully-grounded record can be written and for such a record the judge can only lower.

    `scored` items are `{"record": <scored record>, "composite": float}`. Returns the ids to
    judge, the audit sample, and the per-record denial class, so `scored.json` can report what
    was skipped instead of leaving it to be inferred.
    """
    lane = cfg.get("review_lane", [])
    thr = cfg["auto_accept_threshold"]
    selected, rejected, classes, seen = [], [], {}, set()
    # RECORD IDS ARE NOT GUARANTEED UNIQUE, and everything downstream assumes they are.
    # Measured on PMC10325987: `merge_passes` returned 15 records under 11 distinct ids, the
    # `fallback1` and `ordinal` identity tiers colliding. Sampling over the raw list drew the
    # same id twice, `to_judge` deduplicated it, and the run REPORTED a 3-record audit slice
    # having judged 2 — a claim larger than what happened, which is the one thing this
    # pipeline may not do. Deduplicate here, and hand the collision back as a finding rather
    # than absorbing it: a record id that names two different records is a defect upstream,
    # and it is the same id the output database uses as its primary key.
    duplicates = []
    for s in scored:
        rec, rid = s["record"], s["record"]["record_id"]
        if rid in seen:
            duplicates.append(rid)
            continue
        seen.add(rid)
        # Selection is the gate MINUS the veto — the same predicate the write path applies, so
        # the two cannot disagree about who was worth judging and who was worth writing.
        if not selection_reasons(rec, s["composite"], thr,
                                 require_contradiction_search=True, review_lane=lane):
            selected.append(rid)
        else:
            rejected.append(rid)
            classes[rid] = denial_class(rec)
    auditable = [r for r in rejected if classes[r] in (DENIAL_THIN, DENIAL_CONTRADICTED)]
    audit = audit_slice(auditable, float(cfg["judge_audit_fraction"]), salt)
    return {"selected": selected, "audit": audit, "rejected": rejected,
            "auditable": auditable, "denial_class": classes,
            "duplicate_record_ids": sorted(set(duplicates)),
            "to_judge": sorted(set(selected) | set(audit))}


def apply_verdicts(scored: list, verdicts: dict, judged: set) -> None:
    """Stamp `judge_verdict` / `judge_note` onto scored records, IN PLACE.

    Three states, kept apart because they are three different facts:
      * judged and answered      -> the verdict,
      * judged and no answer     -> `unparseable` (the call happened; the raw reply is on disk),
      * never sent to the judge  -> `not_run`, the default.
    All three except `supported` block. The last is not a loophole: a record only goes unjudged
    when selection already rejected it, so its denial is decided before the judge is consulted.
    """
    for s in scored:
        rid = s["record"]["record_id"]
        v = verdicts.get(rid)
        if v:
            s["record"]["judge_verdict"] = VERDICT_TO_STATE[v["verdict"]]
            note = v.get("weakest_supported_claim") or v.get("reasoning")
            if note:
                s["record"]["judge_note"] = str(note)[:400]
        elif rid in judged:
            s["record"]["judge_verdict"] = "unparseable"
        else:
            s["record"]["judge_verdict"] = "not_run"



def catalogue_questions(paper: str, merged: dict, failures: list, dropped: list,
                        unjudged: list | None = None, review_lane: tuple = (),
                        vetoed: list | None = None, audit_disagreements: list | None = None,
                        blocked_on_process: int = 0,
                        duplicate_record_ids: list | None = None) -> list:
    """Deterministic signals that a RESEARCHER, not the pipeline, has to resolve.

    Everything here is a fact about the run, not an opinion about the chemistry. The head
    session turns these into questions in the researcher's own language; this function only
    guarantees nothing gets silently decided while nobody is watching.
    """
    qs = []
    for a in merged.get("alignment", []):
        if a["found_by_passes"] == 1:
            qs.append({"paper": paper, "kind": "scope_disagreement",
                       "detail": f"{a['identity']} was found by only one of the readings",
                       "identity_tier": a["identity_tier"]})
        elif a["identity_tier"] == "ordinal":
            qs.append({"paper": paper, "kind": "weak_identity",
                       "detail": f"{a['identity']} was matched only by order of appearance",
                       "identity_tier": a["identity_tier"]})
    for name, rep in (merged.get("ensemble") or {}).items():
        if not rep.get("ambiguous_modal"):
            continue
        # A REVIEW-LANE FIELD DISAGREEING IS NOT NEWS. `function` is free prose the researcher
        # already ratified as never-auto-acceptable (T11): three independent readings phrase it
        # three ways every single time, by construction. Measured: 31 of 75 questions in the v4
        # slice were this one field, burying the 12 scope_disagreements that genuinely need a
        # human. A queue that always fires trains the researcher to stop reading it, which
        # destroys the signal for the cases that matter — the same argument the hunter prompt
        # makes about manufacturing doubt.
        if any(name.endswith(f":{f}") or name == f for f in review_lane):
            continue
        qs.append({"paper": paper, "kind": "no_consensus_value",
                   "detail": f"{name} split with no majority", "groups": rep.get("groups")})
    for rid in (unjudged or []):
        qs.append({"paper": paper, "kind": "no_verdict",
                   "detail": f"{rid}: the adversarial judge returned no parseable verdict; "
                             f"the raw response is in judge/ — a record that skipped its check"})
    # THE TWO QUESTIONS THE NEW ORDER CAN ASK AND THE OLD ONE COULD NOT. Under the old scheme a
    # verdict was one term in a mean, so "the score would have written this and the judge stopped
    # it" and "the score turned this down but the judge would have kept it" were both invisible —
    # they came out as a number that moved. They are now separable, and each is a direct
    # measurement of the accept bar rather than an opinion about a record.
    for v in (vetoed or []):
        qs.append({"paper": paper, "kind": "judge_veto",
                   "detail": f"{v['record_id']}: cleared every mechanical check and the "
                             f"adversarial judge struck it out ({v['verdict']})"
                             + (f" — {v['note']}" if v.get("note") else "")
                             + ". The score alone would have written this record."})
    for a in (audit_disagreements or []):
        qs.append({"paper": paper, "kind": "audit_disagreement",
                   "detail": f"{a['record_id']}: turned down as {a['denial_class']}, but the "
                             f"adversarial judge read the source and found the claim SUPPORTED. "
                             f"Evidence the accept bar is rejecting sound records."})
    # A record blocked because the counter-evidence search never completed is denied for a
    # DRIVER failure, not for anything about the paper — and under the D-079 order it is also
    # never sent to the judge, because a verdict cannot lift a process block. Both facts point
    # the same way and neither is visible in the yield, so it is said out loud: a paper that
    # produced nothing because a stage replied unusably must not read like a paper with nothing
    # in it. This is the same distinction v0.31.0 drew for a stage that never ran.
    # ONE ID NAMING TWO RECORDS IS A SILENT DATA-LOSS HAZARD, not a bookkeeping wrinkle. The
    # output DB declares `record_id TEXT PRIMARY KEY` and writes with `INSERT OR REPLACE`, so
    # if two colliding records both cleared the gate the second would overwrite the first with
    # no error, no reason string, and nothing in any artifact to show a row had gone. Measured
    # on PMC10325987: 15 records under 11 ids, the fallback1 and ordinal identity tiers
    # colliding. It has never fired — every paper carrying duplicates has written zero records
    # — which is exactly why it needs saying out loud before it does.
    for rid in (duplicate_record_ids or []):
        qs.append({"paper": paper, "kind": "colliding_record_id",
                   "detail": f"{rid}: the merge produced more than one record under this id, so "
                             f"they cannot be told apart downstream. The output database keys on "
                             f"record_id, so two colliding records that both cleared the gate "
                             f"would silently overwrite each other. Only the first was scored."})
    if blocked_on_process:
        qs.append({"paper": paper, "kind": "verification_unusable",
                   "detail": f"{blocked_on_process} record(s) were blocked because the "
                             f"counter-evidence search never completed for them, so they were "
                             f"also never sent to the adversarial judge — a denial caused by "
                             f"the run, not by the source. hunter_raw.txt has the reply."})
    for f in failures:
        qs.append({"paper": paper, "kind": "pass_failed",
                   "detail": f"pass {f['pass']} ({f['model']}) did not complete: {f['why']}"})
    for d in dropped:
        if d["why"] != "no quote":
            qs.append({"paper": paper, "kind": "unanchorable_quote", "detail": str(d)})
    return qs
