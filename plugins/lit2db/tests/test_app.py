"""The app (D-211): the page's server keeps the same guarantees as the commands it stands in for.

Offline. Europe PMC is a fake getter and the draft is a fixed reply, because what is under test is
what the server does with them: who it answers, where it writes, and that the spec it writes is
exactly what the researcher ticked. The live path (a real draft, a real search, one paper read)
was run by hand on 2026-10-13 through Chrome.
"""
import json
import os
import pathlib
import subprocess
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from lit2db import app, cli, intake
from lit2db.contracts import SchemaReadySpec

from test_intake import REPLY

JATS = b"""<article><front><article-meta><article-title>Uptake in mice</article-title>
<abstract><p>Brain uptake was 2.3 nmol/L.</p></abstract></article-meta></front>
<body><sec><title>Results</title><p>Brain uptake of the antibody was 2.3 nmol/L in mice.</p></sec></body>
</article>"""


def fake_get(url: str) -> bytes:
    if "/search?" in url:
        hits = [{"pmcid": "PMC1", "pmid": "1", "doi": "10.1/a", "title": "Uptake &lt;i&gt;in&lt;/i&gt; mice",
                 "pubYear": "2024", "isOpenAccess": "Y", "inEPMC": "Y", "license": "cc by",
                 "abstractText": "x", "journalInfo": {"journal": {"title": "J"}},
                 "pubTypeList": {"pubType": ["research-article"]}},
                {"pmid": "2", "doi": "10.1/b", "title": "Closed", "pubYear": "2023",
                 "isOpenAccess": "N", "inEPMC": "N", "journalInfo": {"journal": {"title": "J"}},
                 "pubTypeList": {"pubType": []}}]
        return json.dumps({"hitCount": 2, "resultList": {"result": hits}}).encode()
    if url.endswith("/fullTextXML"):
        return JATS
    raise AssertionError(url)


@pytest.fixture
def server(tmp_path):
    a = app.App(home=tmp_path / "home", port=0)
    a.home.mkdir()
    a.get = fake_get
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(a))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield a, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def call(base, path, body=None, host=None, ctype="application/json"):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data)
    if data is not None:
        req.add_header("Content-Type", ctype)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def ok(base, path, body=None):
    code, raw = call(base, path, body)
    out = json.loads(raw)
    assert code == 200 and out["ok"], out
    return out


def test_it_answers_only_localhost_and_only_json_posts(server):
    _, base = server
    assert call(base, "/api/home", host="evil.example")[0] == 403
    assert call(base, "/api/new", {"name": "x"}, ctype="text/plain")[0] == 415
    assert call(base, "/")[0] == 200
    assert call(base, "/static/../__init__.py")[0] == 404


def test_it_opens_only_databases_under_its_home(server, tmp_path):
    a, base = server
    outside = tmp_path / "elsewhere"
    (outside / "lit2db").mkdir(parents=True)
    code, raw = call(base, f"/api/state?p={outside}")
    assert code == 400 and "not one this page can open" in json.loads(raw)["error"]


def test_the_page_writes_exactly_what_was_ticked(server):
    a, base = server
    pid = ok(base, "/api/new", {"name": "Uptake"})["id"]
    assert ok(base, f"/api/state?p={pid}")["current"] == "question"
    ok(base, "/api/question", {"p": pid, "question": "How well do antibodies get into the brain?"})
    pr = app.Project(pathlib.Path(pid))
    it = pr.intake()
    proposal = intake.parse_proposal(REPLY)           # the draft call itself is not under test
    it.update(proposal=proposal, rulings=intake.default_rulings(proposal),
              proposal_meta={"for_question": it["question"]})
    pr.save_intake(it)
    assert ok(base, f"/api/state?p={pid}")["current"] == "collect"

    rulings = intake.default_rulings(proposal)
    for c in rulings["columns"]:
        if c["name"] == "species":
            c["on"] = False
    ok(base, "/api/rulings", {"p": pid, "rulings": rulings, "confirm": True})
    assert ok(base, f"/api/state?p={pid}")["current"] == "papers"

    job = ok(base, "/api/approve", {"p": pid, "query": proposal["query"], "abstracts": False,
                                    "limit": 5})["job"]
    for _ in range(200):
        j = ok(base, f"/api/job?id={job}")
        if j["status"] != "running":
            break
        threading.Event().wait(0.02)
    assert j["status"] == "done", j
    assert j["result"]["full_text"] == 1 and j["result"]["unreachable"] == 1

    spec = json.loads(pr.p["spec"].read_text())
    SchemaReadySpec.model_validate(spec)
    items = {i["item_id"]: i["status"] for i in spec["ledger"]["items"]}
    assert items["F-species"] == "REJECTED" and items["F-antibody"] == "ACCEPTED"
    assert "species" not in {f["name"] for f in spec["fields"]}
    st = ok(base, f"/api/state?p={pid}")
    assert st["current"] == "reading" and st["readable"] == 1


def test_approving_needs_an_answer_about_abstracts(server):
    a, base = server
    pid = ok(base, "/api/new", {"name": "U"})["id"]
    pr = app.Project(pathlib.Path(pid))
    proposal = intake.parse_proposal(REPLY)
    pr.save_intake({"question": "q", "proposal": proposal,
                    "rulings": intake.default_rulings(proposal), "rulings_confirmed": True})
    job = ok(base, "/api/approve", {"p": pid, "query": "q", "abstracts": None})["job"]
    for _ in range(200):
        j = ok(base, f"/api/job?id={job}")
        if j["status"] != "running":
            break
        threading.Event().wait(0.02)
    assert j["status"] == "failed" and "abstracts" in j["error"]
    assert not pr.p["spec"].exists()


def test_a_finished_run_is_not_read_as_running():
    """A paused run is a zombie until reaped, and a zombie answers kill(pid, 0). Measured
    2026-10-13: the page said "reading paper 2 of 8" about a run that had stopped."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    threading.Event().wait(0.5)
    assert app._alive(p.pid) is False
    assert app._alive(None) is False


def test_the_command_speaks_in_sentences(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("LIT2DB_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["help"]) == 0
    out = capsys.readouterr().out
    assert "lit2db status" in out and "{" not in out
    assert cli.main(["status"]) == 0
    assert "no databases yet" in capsys.readouterr().out
    assert cli.main(["nonsense"]) == 2


def test_checking_in_the_page_keeps_the_review_rule(server, tmp_path):
    """Checking moved into the page (2026-10-13). The rule moved with it: right or wrong is refused
    on a record whose quote is not where it says, whatever the page sends."""
    import shutil
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import review_ui
    a, base = server
    db, sources, _ = review_ui.seed_demo()
    pid = ok(base, "/api/new", {"name": "Demo"})["id"]
    p = app.Project(pathlib.Path(pid)).p
    shutil.copy(db, p["db"])
    shutil.rmtree(p["sources"])
    shutil.copytree(sources, p["sources"])

    q = ok(base, f"/api/check/queue?p={pid}&show=held")
    assert q["counts"]["held"] == len(q["items"]) == 4
    broken = next(i for i in q["items"] if i["record_id"] == "demoD")
    view = ok(base, f"/api/check/record?p={pid}&record_id=demoD&source_id={broken['source_id']}")
    assert view["verdicts_allowed"] == ["cant_tell"]
    code, raw = call(base, "/api/check/verdict", {"p": pid, "record_id": "demoD",
                                                  "source_id": broken["source_id"],
                                                  "verdict": "right"})
    assert code == 400 and not json.loads(raw)["ok"]

    good = next(i for i in q["items"] if i["record_id"] != "demoD")
    ok(base, "/api/check/verdict", {"p": pid, "record_id": good["record_id"],
                                    "source_id": good["source_id"], "verdict": "wrong",
                                    "note": "kept as typed"})
    after = ok(base, f"/api/check/queue?p={pid}&show=checked")
    assert after["counts"]["wrong"] == 1
    assert [i["verdict"] for i in after["items"]] == ["wrong"]
