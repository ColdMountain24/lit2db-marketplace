"""The browser review loop — that it shows real evidence, and that it cannot write a record.

Two rules carry this feature, and both are the kind that hold right up until nobody is looking.

**A human verdict never writes a record.** Already pinned for the MCP path in
`test_adjudication.py`; pinned again here because a NEW surface is a new way around it, and the
argument for the rule is not obvious enough to survive on style. A reviewer saying "yes, that one
is real" is a measurement of whether the GATE was right; spending it to write the row destroys
the only thing it could have told us.

**No verdict without the evidence.** `commands/lit2db-review.md` forbids asking for a ruling on a
record whose quote has not been shown. A page can honour that by disabling a button — and a
disabled button is a suggestion. These tests go at the server, because that is where the rule has
to live to be one: a stale page, a replayed request, or a second tab must all hit the same wall.

The offset tests exist because the failure they describe is SILENT. Slicing `full.txt` at a wrong
offset returns text — just not the cited text — and a verdict given against the wrong paragraph
is not a weak label, it is a wrong one that is indistinguishable from a good one afterwards.
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys
import threading
import urllib.error
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(ROOT))

import review_ui as UI  # noqa: E402
from lit2db_mcp import server as S  # noqa: E402
from lit2db.output import (adjudications, record_adjudication,  # noqa: E402
                           record_candidate, surface_of, tag_adjudicator)
from lit2db.review import (ANCHOR_ABSENT, ANCHOR_EXACT, ANCHOR_MOVED,  # noqa: E402
                           ANCHOR_NO_STORE, ANCHOR_PAST_END, QUOTE_JOIN, anchor, load_store,
                           may_record, record_view, source_links)
from lit2db.store import build_from_abstract, store_dirname, write_store  # noqa: E402

PAPER = ("Kinetics of a bacterial hydrolase",
         "The enzyme was assayed at 25 C. The Michaelis constant Km was 4.2 uM at pH 7.4. "
         "A second determination gave 4.4 uM. The Michaelis constant Km was 4.2 uM at pH 7.4.")


def _store(tmp_path, source_id="PMC1", title=PAPER[0], body=PAPER[1]):
    root = tmp_path / "sources"
    store = build_from_abstract(title, body, source_id)
    write_store(store, root)
    return str(root), store["full_text"]


def _candidate(db, source_id="PMC1", record_id="r1", quote="", offset=0, **prov):
    rec = {"record_id": record_id, "entity_type": "enzyme_substrate_pair",
           "judge_verdict": "partial",
           "fields": [{"field_name": "km_value", "value": 4.2,
                       "provenance": {"kind": "literature", "source_id": source_id,
                                      "source_status": "active", "verbatim_quote": quote,
                                      "char_offset": offset, **prov}}]}
    record_candidate(rec, 0.90, {"decision": "deny", "reasons": ["thin agreement"]},
                     db, source_id=source_id)
    return rec


# --- 1. the rule that a verdict is not a write -------------------------------------------
def test_confirming_a_record_in_the_browser_does_not_put_it_in_the_ml_ready_table(tmp_path):
    """The whole point of collecting the verdict is to measure the gate. A surface that let a
    confirmation write the row would be measuring itself."""
    db = str(tmp_path / "o.db")
    sources, text = _store(tmp_path)
    quote = "The Michaelis constant Km was 4.2 uM at pH 7.4."
    _candidate(db, quote=quote, offset=text.index(quote))

    with _serving(db, sources) as base:
        code, out = _post(base, {"record_id": "r1", "source_id": "PMC1", "verdict": "right"})

    assert (code, out["recorded"]) == (200, True)
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0, (
        "a browser confirmation reached the ML-ready table")
    assert con.execute("SELECT COUNT(*) FROM adjudications").fetchone()[0] == 1
    con.close()


def test_the_review_ui_never_reaches_a_write_tool():
    """`gate.WRITE_TOOLS` is the allowlist this feature must stay off, and the cheapest way to
    keep it off is to assert the words are absent rather than to trust a code review.

    Checked as source text, like `test_declarations` does, because the alternative — proving it
    dynamically — only covers the paths a test happens to walk."""
    forbidden = ("gate_upsert", "db_upsert", "upsert(")
    for path in (ROOT / "src" / "lit2db" / "review.py",
                 ROOT / "scripts" / "review_ui.py",
                 ROOT / "scripts" / "review_ui.html"):
        body = path.read_text(encoding="utf-8")
        hits = [w for w in forbidden if w in body]
        assert not hits, (f"{path.name} references {hits} — the review surface must not be able "
                          f"to reach the ML-ready table (gate.WRITE_TOOLS)")


# --- 2. the rule that a verdict needs its evidence ----------------------------------------
def test_a_quote_that_is_not_at_its_recorded_offset_cannot_be_confirmed_or_denied(tmp_path):
    """REFUSED by the server, not merely greyed out in the page.

    A disabled button is a suggestion to whoever is at the keyboard. This is the half of the
    rule that survives a stale tab, a replayed request, or curl."""
    db = str(tmp_path / "o.db")
    sources, _ = _store(tmp_path)
    _candidate(db, quote="A sentence this paper does not contain.", offset=10)

    with _serving(db, sources) as base:
        view = _get(base, "/api/record?record_id=r1&source_id=PMC1")
        assert view["verdicts_allowed"] == ["cant_tell"]
        for verdict in ("right", "wrong"):
            code, out = _post(base, {"record_id": "r1", "source_id": "PMC1",
                                     "verdict": verdict})
            assert code == 409 and out["recorded"] is False
            assert "could not be put in front of you" in out["reason"]
        code, out = _post(base, {"record_id": "r1", "source_id": "PMC1",
                                 "verdict": "cant_tell"})
        assert (code, out["recorded"]) == (200, True), "can't tell must always stay available"


def test_a_quote_at_its_recorded_offset_anchors_exactly(tmp_path):
    """The ordinary case: the reviewer is looking at the evidence, so all three answers open."""
    db = str(tmp_path / "o.db")
    sources, text = _store(tmp_path)
    quote = "A second determination gave 4.4 uM."
    _candidate(db, quote=quote, offset=text.index(quote))

    view = record_view(db, sources, "r1", "PMC1")
    field = view["fields"][0]
    assert field["anchor"]["state"] == ANCHOR_EXACT
    assert text[field["anchor"]["spans"][0]["start"]:
                field["anchor"]["spans"][0]["end"]] == quote
    assert view["verdicts_allowed"] == ["right", "wrong", "cant_tell"]
    assert view["blocked_because"] is None


def test_a_quote_found_somewhere_other_than_its_offset_is_reported_as_moved(tmp_path):
    """The offset is what disambiguates two occurrences of the same entity — this paper contains
    the same sentence twice. A quote that is in the paper but not where the record says leaves
    nobody able to say WHICH occurrence was extracted, so it is not evidence for a ruling."""
    db = str(tmp_path / "o.db")
    sources, text = _store(tmp_path)
    quote = "The Michaelis constant Km was 4.2 uM at pH 7.4."
    first = text.index(quote)
    second = text.index(quote, first + 1)
    assert second > first, "fixture must contain the sentence twice"

    _candidate(db, quote=quote, offset=first + 3)   # off by three: lands mid-sentence
    view = record_view(db, sources, "r1", "PMC1")
    a = view["fields"][0]["anchor"]
    assert a["state"] == ANCHOR_MOVED
    assert a["spans"][0]["found_at"] == [first, second]
    assert str(first) in a["explain"].replace(",", "")
    assert view["verdicts_allowed"] == ["cant_tell"]


def test_an_offset_past_the_end_of_the_text_says_so_in_both_numbers(tmp_path):
    """"The record points to character 12,400. This paper's stored text ends at 9,812." — the
    reviewer can act on that sentence; "could not locate quote" tells them nothing."""
    _, text = _store(tmp_path)
    a = anchor(text, "The enzyme was assayed at 25 C.", len(text) + 500)
    assert a["state"] == ANCHOR_PAST_END
    assert str(len(text) + 500) in a["explain"].replace(",", "")
    assert str(len(text)) in a["explain"].replace(",", "")


def test_a_source_with_no_store_is_reviewable_only_as_cant_tell(tmp_path):
    """A paper that was never stored is the commonest honest `cant_tell` in this corpus — much
    of the literature is behind a paywall. It must not read as a defect, and must not be
    silently skipped either: the record is still shown, with the reason."""
    db = str(tmp_path / "o.db")
    sources, _ = _store(tmp_path)
    _candidate(db, source_id="PMC_never_stored", quote="Anything at all.", offset=0)

    view = record_view(db, sources, "r1", "PMC_never_stored")
    assert view["fields"][0]["anchor"]["state"] == ANCHOR_NO_STORE
    assert view["source_text"] is None
    assert view["verdicts_allowed"] == ["cant_tell"]
    assert "never built into a store" in view["blocked_because"]
    assert may_record(view, "right") and may_record(view, "cant_tell") is None


# --- 3. what an offset MEANS ---------------------------------------------------------------
def test_offsets_are_character_indices_not_byte_indices(tmp_path):
    """Every paper in this literature carries non-ASCII — µ, °, –, Greek — so a byte offset and
    a character offset diverge silently. `server.py`'s `locate_spans` warns about exactly this;
    a reader that got it wrong would highlight the wrong span with total confidence."""
    body = "Reaction at 25 °C in 50 µM buffer. The Km was 4.2 µM — a low value. Then more text."
    sources, text = _store(tmp_path, source_id="PMC_unicode", body=body)
    quote = "The Km was 4.2 µM — a low value."

    char_off = text.index(quote)
    byte_off = len(text[:char_off].encode("utf-8"))
    assert byte_off > char_off, "fixture must contain multi-byte characters before the quote"

    assert anchor(text, quote, char_off)["state"] == ANCHOR_EXACT
    assert anchor(text, quote, byte_off)["state"] == ANCHOR_MOVED, (
        "a byte offset must not be accepted as if it were a character offset")


def test_a_multi_quote_field_anchors_on_the_part_the_offset_names(tmp_path):
    """`pipeline.assemble` joins several quotes for one value with ' | ' while `char_offset`
    anchors only the first that resolved. Treating the joined string as one quote would fail to
    find it and report perfectly good evidence as missing."""
    _, text = _store(tmp_path)
    first = "The enzyme was assayed at 25 C."
    second = "A second determination gave 4.4 uM."
    a = anchor(text, first + QUOTE_JOIN + second, text.index(first))

    assert a["state"] == ANCHOR_EXACT
    assert len(a["spans"]) == 2
    assert [text[s["start"]:s["end"]] for s in a["spans"]] == [first, second]


def test_a_field_is_only_as_anchored_as_its_worst_quote(tmp_path):
    """One good quote beside one that is not in the paper is not a record to rule on."""
    _, text = _store(tmp_path)
    good = "The enzyme was assayed at 25 C."
    a = anchor(text, good + QUOTE_JOIN + "Not in this paper.", text.index(good))
    assert a["state"] == ANCHOR_ABSENT


# --- 4. addressing, and the queue it reads --------------------------------------------------
def test_the_store_directory_name_matches_what_write_store_wrote(tmp_path):
    """The reader and the writer must agree about where a paper lives. A DOI arrives with a
    slash in it, and a reader that skipped the sanitizer would look in a directory that does not
    exist and report a stored paper as missing."""
    root = tmp_path / "sources"
    for source_id in ("PMC3429353", "DOI_10.1002/anie.201506541", "a b/c"):
        store = build_from_abstract("t", "some body text", source_id)
        paths = write_store(store, root)
        assert pathlib.Path(paths["dir"]).name == store_dirname(source_id)
        loaded = load_store(str(root), source_id)
        assert loaded is not None and loaded["full_text"] == store["full_text"]


def test_a_source_id_cannot_escape_the_sources_root(tmp_path):
    """`source_id` arrives from a query string. Localhost or not, a path that climbs out of its
    root is a defect — and the sanitizer removing '/' is a consequence of its character class,
    not a guarantee, so the containment check is what makes it one."""
    sources, _ = _store(tmp_path)
    (tmp_path / "secret.txt").write_text("not a paper", encoding="utf-8")
    for hostile in ("../..", "../../secret.txt", "/etc/passwd"):
        assert load_store(sources, hostile) is None


def test_the_queue_shows_only_what_the_gate_denied(tmp_path):
    """`records` is what cleared the gate and needs no confirming; the queue is the near-misses.
    Showing accepted rows would spend a researcher's attention where it converts into nothing."""
    db = str(tmp_path / "o.db")
    sources, text = _store(tmp_path)
    _candidate(db, record_id="denied", quote="The enzyme was assayed at 25 C.",
               offset=text.index("The enzyme was assayed at 25 C."))
    record_candidate({"record_id": "allowed", "entity_type": "t", "fields": []},
                     0.99, {"decision": "allow", "reasons": []}, db, source_id="PMC1")

    with _serving(db, sources) as base:
        ids = [r["record_id"] for r in _get(base, "/api/queue")["queue"]]
    assert ids == ["denied"]


def test_re_adjudicating_through_the_ui_replaces_rather_than_duplicates(tmp_path):
    """A reviewer is allowed to change their mind. The alternative is a calibration table where
    one record carries two verdicts and nothing says which is current."""
    db = str(tmp_path / "o.db")
    sources, text = _store(tmp_path)
    quote = "A second determination gave 4.4 uM."
    _candidate(db, quote=quote, offset=text.index(quote))

    with _serving(db, sources) as base:
        _post(base, {"record_id": "r1", "source_id": "PMC1", "verdict": "right"})
        _post(base, {"record_id": "r1", "source_id": "PMC1", "verdict": "wrong",
                     "note": "the organism is wrong"})

    con = sqlite3.connect(db)
    rows = con.execute("SELECT verdict, note FROM adjudications").fetchall()
    con.close()
    assert rows == [("wrong", "the organism is wrong")]


def test_a_verdict_outside_the_vocabulary_is_refused_as_a_bad_request(tmp_path):
    """Fails closed, and distinguishably: an unreadable verdict is a malformed request, while a
    refused one is a rule firing. Storing free text would make the calibration table silently
    incomplete — the same reason `failure_reason` is an enum."""
    db = str(tmp_path / "o.db")
    sources, text = _store(tmp_path)
    quote = "A second determination gave 4.4 uM."
    _candidate(db, quote=quote, offset=text.index(quote))

    with _serving(db, sources) as base:
        code, out = _post(base, {"record_id": "r1", "source_id": "PMC1",
                                 "verdict": "probably right"})
    assert code == 400 and out["recorded"] is False

    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM adjudications").fetchone()[0] == 0
    con.close()


# --- 5. what the reviewer is told -----------------------------------------------------------
def test_the_denial_is_translated_but_the_original_is_kept(tmp_path):
    """`lit2db-review.md` requires the researcher's language, not the schema's. It also requires
    that the record not be replaced by the translation — a paraphrase is the plugin's reading of
    the denial, and the reviewer must be able to see the denial itself."""
    db = str(tmp_path / "o.db")
    sources, text = _store(tmp_path)
    quote = "A second determination gave 4.4 uM."
    _candidate(db, quote=quote, offset=text.index(quote))

    with _serving(db, sources) as base:
        view = _get(base, "/api/record?record_id=r1&source_id=PMC1")

    plain = " ".join(view["stopped_short_because"])
    assert "supports part of this" in plain, "judge_verdict=partial was not translated"
    assert "composite" not in plain and "judge_verdict" not in plain, (
        "schema vocabulary leaked into the researcher-facing explanation")
    assert view["reasons"] == ["thin agreement"], "the denial as recorded must survive verbatim"


def test_an_abstract_only_source_is_declared_to_the_reviewer(tmp_path):
    """Reviewing an abstract when the full text could not be obtained is exactly when
    `cant_tell` is the honest answer rather than `wrong`. The reviewer cannot make that call
    unless the page says which one they are reading."""
    db = str(tmp_path / "o.db")
    sources, text = _store(tmp_path)
    quote = "A second determination gave 4.4 uM."
    _candidate(db, quote=quote, offset=text.index(quote))

    view = record_view(db, sources, "r1", "PMC1")
    assert view["source_text_scope"] == "abstract_only"


# --- 6. the demo, since a fresh checkout has no project ------------------------------------
def test_the_demo_project_anchors_every_quote_it_did_not_deliberately_break():
    """The demo recomputes each offset from the store it just built rather than trusting the
    fixture's hand-written one — the spine derives offsets and the extractor is forbidden from
    computing one, so a demo that hand-wrote them would teach the opposite of what the store is
    for. The fourth record is broken ON PURPOSE, so the refusal is visible without a test."""
    db, sources, _ = UI.seed_demo()
    states = {}
    for rid, sid in (("demoA", "PMC_demo_A"), ("demoB", "PMC_demo_B"),
                     ("demoC", "PMC_demo_C"), ("demoD", "PMC_demo_D")):
        states[rid] = record_view(db, sources, rid, sid)
    assert [states[r]["evidence_state"] for r in ("demoA", "demoB", "demoC")] == \
        [ANCHOR_EXACT] * 3
    assert states["demoD"]["evidence_state"] == ANCHOR_ABSENT
    assert states["demoD"]["verdicts_allowed"] == ["cant_tell"]


def test_the_demo_does_not_modify_the_fixture_run_demo_depends_on():
    """`run_demo.py` iterates `examples/demo_records.json` and CLAUDE.md pins its output ('A
    written; B & C denied') as a release check. The review demo reads that file and must leave
    it alone — a seeder that rewrote offsets on disk would break an unrelated release gate."""
    path = ROOT / "examples" / "demo_records.json"
    before = path.read_text(encoding="utf-8")
    UI.seed_demo()
    assert path.read_text(encoding="utf-8") == before
    offsets = [f["provenance"]["char_offset"]
               for r in json.loads(before).values() for f in r["fields"]]
    assert offsets == [1180, 990, 400]


# --- test plumbing --------------------------------------------------------------------------
class _serving:
    """Run the real handler on a free port. The rules under test live in the server, so testing
    them anywhere but through HTTP would test a different object than the one that ships."""

    def __init__(self, db, sources, show_all=False):
        self.cfg = {"db": db, "sources": sources, "limit": 100, "show_all": show_all,
                    "adjudicator": "researcher", "autoaccept": 0.95, "verbose": False}

    def __enter__(self):
        self.httpd = UI.serve(self.cfg, 0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.load(r)


def _post(base, payload):
    req = urllib.request.Request(base + "/api/adjudicate",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_the_page_cannot_file_a_verdict_against_a_record_it_is_not_showing():
    """Found in the browser, not by a test: holding `j` fires overlapping record fetches, and
    without a supersession token the LAST ONE TO RESOLVE wins the render. That left the record
    on screen and the record in `VIEW` disagreeing — and the next verdict was filed against a
    paper the reviewer never saw, which looked entirely correct while it happened.

    The server cannot catch this one. It validates the record NAMED IN THE REQUEST, and the
    wrongly-named record was itself perfectly adjudicable. So the guard has to be in the page,
    and this asserts it is still there: a stale response is dropped, and a verdict is refused
    outright if the queue position and the loaded view ever disagree again.

    A source assertion rather than a browser test because the plugin ships no JS runtime and
    will not grow one for this — the same reasoning `test_declarations` uses."""
    html = (ROOT / "scripts" / "review_ui.html").read_text(encoding="utf-8")
    assert "if (mine !== SEQ) return;" in html, (
        "the stale-response guard is gone — overlapping fetches can render one record while "
        "another is held in VIEW")
    assert "here.record_id !== VIEW.record_id" in html, (
        "the queue-position/view agreement check is gone — a desync would file a verdict "
        "against the wrong record instead of refusing")


def test_every_quoted_sentence_is_reachable_from_the_value_it_supports():
    """A record's fields quote places thousands of characters apart, and the page can only open
    at one of them. Observed on a real pilot record: five fields quoting characters 589 through
    13,396 of one 16,255-character paper — so a reviewer scrolled to the first was being asked
    to confirm five values while looking at one of them.

    That is `lit2db-review.md`'s rule failing quietly rather than loudly: the quote WAS shown,
    somewhere, and nothing said there were others. Each value is clickable to its own sentence,
    and the count is stated. Asserted as source because the mapping lives in the page."""
    html = (ROOT / "scripts" / "review_ui.html").read_text(encoding="utf-8")
    assert "MARKS = v.fields.map" in html, (
        "the field-to-highlight mapping is gone — values can no longer reach their own quote")
    assert "sentences in this paper are quoted for this row" in html, (
        "the count of quoted sentences is gone — a reviewer cannot tell there are others")
    assert "renderRight(); renderLeft();" in html, (
        "render order flipped — the left pane links against the PREVIOUS record's highlights")


def test_the_reviewer_is_given_a_way_to_the_real_paper(tmp_path):
    """The store is a normalized extract with the reference list stripped — good for anchoring an
    offset, and NOT the paper. A reviewer deciding whether a value is right often needs a figure,
    an SI table, or something `build_store` did not keep, and the honest answer is to hand them
    the article rather than imply the extract was all there was.

    Built only from identifiers the store actually recorded: an id that was not captured yields
    no link rather than a constructed one that 404s."""
    assert source_links({"doi": "10.1002/anie.201914449", "pmid": "31943579",
                         "pmcid": "PMC7187462"}, "PMC7187462") == [
        {"label": "doi:10.1002/anie.201914449", "url": "https://doi.org/10.1002/anie.201914449"},
        {"label": "PMC7187462",
         "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7187462/"},
        {"label": "PMID 31943579", "url": "https://pubmed.ncbi.nlm.nih.gov/31943579/"}]

    # A DOI-only source carries its id in `source_id`, as the pilot's stores do.
    assert source_links({}, "DOI:10.1002/anie.201501119")[0]["url"] == \
        "https://doi.org/10.1002/anie.201501119"
    assert source_links({}, "") == [], "no identifier must yield no link, not a guess"


def test_a_verdict_records_which_surface_it_was_given_on(tmp_path):
    """Both review paths write the same table with the same three verdicts, and they do NOT apply
    the same conditions: the browser refuses right/wrong when a record's quote could not be shown,
    while `/lit2db-review` states that rule in prose for an agent to honour.

    So the two can produce differently-distributed labels from one corpus — and with nothing
    recording the path, that difference was unmeasurable rather than absent. Pooling them would
    let a bar be calibrated partly against how each surface asks."""
    db = str(tmp_path / "o.db")
    sources, text = _store(tmp_path)
    quote = "A second determination gave 4.4 uM."
    _candidate(db, record_id="r1", quote=quote, offset=text.index(quote))
    _candidate(db, record_id="r2", quote=quote, offset=text.index(quote))

    with _serving(db, sources) as base:
        _post(base, {"record_id": "r1", "source_id": "PMC1", "verdict": "right"})
    S.record_adjudication("r2", "PMC1", "cant_tell", db_path=db)   # the MCP surface

    rows = {a["record_id"]: a for a in adjudications(db)["adjudications"]}
    assert rows["r1"]["surface"] == "browser"
    assert rows["r2"]["surface"] == "chat"
    assert adjudications(db)["by_surface"] == {
        "browser": {"right": 1, "wrong": 0, "cant_tell": 0},
        "chat": {"right": 0, "wrong": 0, "cant_tell": 1}}


def test_the_surface_tag_survives_a_named_adjudicator_and_never_doubles(tmp_path):
    """`--adjudicator alice` must still record the path: which surface a verdict came from is a
    property of the path, not a setting someone remembers to pass.

    And tagging must be idempotent — both callers go through a wrapper that tags, so a
    double-tagged value would split into its own bucket and silently halve a comparison."""
    assert tag_adjudicator("alice", "browser") == "alice (browser)"
    assert tag_adjudicator("alice (browser)", "browser") == "alice (browser)"
    assert tag_adjudicator("", "chat") == "researcher (chat)"
    assert surface_of("alice (browser)") == "browser"
    assert surface_of("researcher") == "unknown", (
        "rows written before this must read as unknown, not be assigned a path they never had")


def test_verdicts_recorded_before_the_surface_was_tracked_still_read(tmp_path):
    """The tag rides on `adjudicator` rather than a new column precisely so existing calibration
    data survives. A schema change would have stranded every row already collected."""
    db = str(tmp_path / "o.db")
    _candidate(db, record_id="old", quote="q", offset=0)
    record_adjudication("old", "PMC1", "right", db, adjudicator="researcher")

    rep = adjudications(db)
    assert rep["adjudications"][0]["surface"] == "unknown"
    assert rep["counts"]["right"] == 1, "an untagged row must still count in the pooled figure"


def test_everything_the_page_renders_from_a_record_is_escaped():
    """Field names, values, quotes and denial strings all originate in a PAPER, by way of an LLM.
    They are untrusted text, and the page writes them with innerHTML.

    Verified live against a record carrying `<img src=x onerror=...>` in its field name, value
    and reasons: it rendered as text, created no element, and did not reach `document.title`.
    This keeps it that way — the interpolations are listed rather than the escaper merely being
    present, because adding one unescaped `${...}` is the whole failure and it would not disturb
    any other test."""
    html = (ROOT / "scripts" / "review_ui.html").read_text(encoding="utf-8")
    for expr in ("esc(f.field_name)", "esc(f.value)", "esc(f.quote)", "esc(v.source_id)",
                 "esc(f.anchor.explain)", "esc(v.reasons.join", "esc(v.entity_type",
                 "esc(l.url)", "esc(l.label)", "esc(v.blocked_because"):
        assert expr in html, (f"`{expr}` is no longer escaped — a value quoted out of a paper "
                              f"would be rendered as markup")
    assert "t.slice(at, a)" in html and "esc(t.slice" in html, (
        "the source text must be escaped slice-by-slice, so a paper containing markup cannot "
        "corrupt the page it is being reviewed in")


def test_the_page_loads_nothing_from_a_network():
    """No CDN, no build step, no fonts, no analytics: a review tool that stops working on a train
    or inside an institution that blocks a CDN is not a review tool.

    Links OUT to the article are the deliberate exception and are not loads — the reviewer
    clicks them. They are built server-side from recorded identifiers, so no URL is hardcoded
    here, and they carry `noopener` because a new tab that can reach back into this page is a
    hazard for no benefit."""
    html = (ROOT / "scripts" / "review_ui.html").read_text(encoding="utf-8")
    assert "https://" not in html and "http://" not in html.replace("http://127.0.0.1", ""), (
        "a URL is hardcoded in the page — article links are built from the store's identifiers")
    for tag in ("<script src", '<link rel="stylesheet"', "@import", "//cdn", "fonts.g"):
        assert tag not in html, f"{tag} pulls in something the page should carry itself"
    assert 'rel="noopener noreferrer"' in html, "outbound links must not hand over window.opener"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
