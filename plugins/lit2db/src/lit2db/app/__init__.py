"""The lit2db app: one local page that walks a researcher from a question to three spreadsheets.

## Why this exists (D-211)

The engine could be driven only from a terminal, one JSON-printing command at a time, and the
scope interview lived inside Claude Code. A professor could not get through either alone, which is
M12's whole bar. This is the same engine behind a page: Your question, What to collect, Which
papers, Reading, Checking, Spreadsheets. Every step calls the library functions the commands call
(`intake`, `project`), so the page cannot do anything the contract would refuse elsewhere.

## What it may do, and what it may not

- It binds 127.0.0.1 and answers only requests addressed to localhost, so another site open in
  the same browser cannot drive it (a POST must also be JSON, which a cross-site form cannot be).
- It writes only inside a project folder: under the projects home, or the folder it was opened in.
- It never ratifies on the researcher's behalf. A spec is written only from what the page showed
  and they continued past, through `intake.build_spec`, which `SchemaReadySpec` checks.
- Reading runs as its own process, detached, so closing the page does not stop it; progress is
  read from the run's own files every time, never kept here.

Stdlib only, like `project`: this is the first thing a stranger runs.
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import mimetypes
import os
import pathlib
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import intake, project

STATIC = pathlib.Path(__file__).resolve().parent / "static"
PLUGIN_ROOT = project.PLUGIN_ROOT
SCRIPTS = PLUGIN_ROOT / "scripts"
DEFAULT_HOME = pathlib.Path.home() / "Documents" / "lit2db"

STEPS = ("question", "collect", "papers", "reading", "checking", "spreadsheets")
DRAFT_MODEL = "opus"          # writing the draft is design work; reading uses the run's models


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: pathlib.Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    # A run this server started and that has since ended is a zombie until it is reaped, and a
    # zombie still answers kill(pid, 0). Measured: a paused run read as "reading paper 2 of 8".
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass                                 # not our child (the server was restarted): fine
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _version() -> str:
    m = _read_json(PLUGIN_ROOT / ".claude-plugin" / "plugin.json", {}) or {}
    return m.get("version", "")


class Plain(Exception):
    """A refusal worded for the researcher. Shown on the page as it is."""


# --- jobs: the slow things, run off the request thread ----------------------------------------

class Jobs:
    def __init__(self):
        self._jobs: dict = {}
        self._lock = threading.Lock()

    def start(self, kind: str, fn) -> str:
        jid = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[jid] = {"id": jid, "kind": kind, "status": "running",
                               "started": time.time()}

        def run():
            try:
                result = fn()
                update = {"status": "done", "result": result}
            except Plain as exc:
                update = {"status": "failed", "error": str(exc)}
            except Exception as exc:                          # noqa: BLE001
                traceback.print_exc()
                update = {"status": "failed", "error": f"Something went wrong: {exc}"}
            with self._lock:
                self._jobs[jid].update(update, finished=time.time())

        threading.Thread(target=run, daemon=True).start()
        return jid

    def get(self, jid: str) -> dict:
        with self._lock:
            j = dict(self._jobs.get(jid) or {})
        if not j:
            raise Plain("That task is no longer known. Reload the page.")
        j["elapsed"] = round((j.get("finished") or time.time()) - j["started"])
        return j


# --- a project, as the page sees it --------------------------------------------------------------

class Project:
    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root).resolve()
        self.p = project.paths(self.root)
        self.intake_path = self.p["base"] / "intake.json"
        self.app_path = self.p["base"] / "app.json"

    @property
    def name(self) -> str:
        return self.root.name

    def intake(self) -> dict:
        return _read_json(self.intake_path, {}) or {}

    def save_intake(self, data: dict) -> None:
        data["updated"] = _now()
        _write_json(self.intake_path, data)

    def app(self) -> dict:
        return _read_json(self.app_path, {}) or {}

    def save_app(self, data: dict) -> None:
        _write_json(self.app_path, data)

    def config(self) -> dict:
        return _read_json(self.p["config"], {}) or {}

    def progress(self) -> dict:
        cfg = self.config()
        papers = cfg.get("papers") or []
        done = [x for x in papers if (self.p["run"] / x / "scored.json").exists()]
        app = self.app()
        running = bool(app.get("run_pid")) and _alive(app.get("run_pid"))
        log = self.p["base"] / "run.log"
        tail, now_reading = [], ""
        if log.exists():
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
            tail = lines[-8:]
            for line in reversed(lines):
                m = re.search(r"--- paper (\d+)/(\d+) ---", line)
                if m:
                    now_reading = f"paper {m.group(1)} of {m.group(2)}"
                    break
        questions = 0
        qf = self.p["run"] / "QUESTIONS.jsonl"
        if qf.exists():
            questions = sum(1 for line in qf.read_text(encoding="utf-8").splitlines()
                            if line.strip())
        return {"total": len(papers), "done": len(done), "running": running,
                "now": now_reading if running else "", "log": tail, "questions": questions,
                "started": app.get("run_started"), "mode": app.get("run_mode"),
                "finished": bool(papers) and len(done) == len(papers)}

    def results(self) -> dict:
        out = {}
        for name in ("papers", "candidates", "accepted"):
            f = self.p["results"] / f"{name}.csv"
            if f.exists():
                with f.open(encoding="utf-8") as fh:
                    out[name] = {"rows": max(0, sum(1 for _ in fh) - 1), "path": str(f)}
        return out

    def state(self) -> dict:
        it = self.intake()
        search = _read_json(self.p["search"], None)
        prog = self.progress()
        results = self.results()
        spec = _read_json(self.p["spec"], None)
        done = {
            "question": bool(it.get("question")) and bool(it.get("proposal")),
            "collect": bool(it.get("rulings_confirmed")),
            "papers": bool(spec) and bool(search) and bool(project.readable_sources(self.root)),
            "reading": prog["finished"],
            "checking": bool(it.get("checked")),
            "spreadsheets": bool(results),
        }
        current = next((s for s in STEPS if not done[s]), "spreadsheets")
        return {
            "id": str(self.root), "name": self.name, "folder": str(self.root),
            "steps": done, "current": current,
            "intake": {k: it.get(k) for k in ("question", "seed", "proposal", "rulings",
                                              "papers", "proposal_meta")},
            "spec": {"frozen": bool(spec), "version": (spec or {}).get("spec_version"),
                     "fields": [f["name"] for f in (spec or {}).get("fields", [])]},
            "search": {"counts": (search or {}).get("counts"),
                       "query": (search or {}).get("query")},
            "readable": len(project.readable_sources(self.root)) if search else 0,
            "progress": prog, "results": results,
        }


# --- the server --------------------------------------------------------------------------------

class App:
    def __init__(self, *, home: pathlib.Path = DEFAULT_HOME, here: pathlib.Path | None = None,
                 port: int = 8770):
        self.home = pathlib.Path(home).expanduser().resolve()
        self.here = pathlib.Path(here).resolve() if here else None
        self.port = port
        self.jobs = Jobs()
        self.review_proc = None
        self.review_for = None
        self.get = functools.partial(project.http_get,
                                     contact=os.environ.get("LIT2DB_CONTACT_EMAIL", ""))

    # projects
    def projects(self) -> list:
        out = []
        if self.home.is_dir():
            for d in sorted(self.home.iterdir()):
                if d.is_dir() and (d / "lit2db").is_dir():
                    out.append(d)
        if self.here and self.here not in out:
            out.insert(0, self.here)
        rows = []
        for d in out:
            pr = Project(d)
            st = pr.state()
            stamp = max((f.stat().st_mtime for f in (pr.intake_path, pr.p["spec"],
                                                      pr.p["config"]) if f.exists()), default=0)
            rows.append({"id": str(d), "name": d.name, "current": st["current"],
                         "question": st["intake"].get("question") or "",
                         "updated": stamp})
        rows.sort(key=lambda r: -r["updated"])
        return rows

    def project(self, pid: str) -> Project:
        if not pid:
            raise Plain("No database was chosen.")
        root = pathlib.Path(pid).expanduser().resolve()
        allowed = root == self.here or (root.parent == self.home)
        if not allowed or not (root / "lit2db").is_dir():
            raise Plain("That database is not one this page can open.")
        return Project(root)

    def new_project(self, name: str) -> dict:
        name = re.sub(r"[\\/:*?\"<>|]+", " ", (name or "")).strip()[:80]
        if not name:
            raise Plain("Give the database a name first.")
        root = self.home / name
        if root.exists():
            raise Plain(f"There is already a database called “{name}”.")
        project.init(root)
        Project(root).save_intake({"created": _now()})
        return {"id": str(root)}

    # step 1 and 2
    def draft(self, pr: Project) -> dict:
        it = pr.intake()
        prompt = intake.build_prompt(it.get("question") or "", it.get("seed") or "")
        sys.path.insert(0, str(SCRIPTS))
        import run_wave                                       # noqa: PLC0415
        from ..fuse import Fuse                               # noqa: PLC0415
        # Billed and fused like every other call: the signed-in subscription unless the
        # researcher said otherwise, and the stripped harness (D-208), because a draft needs no
        # tool and the full one costs about 40,000 tokens of nothing per call.
        run_wave._CHILD_ENV = run_wave.child_env({}, os.environ)
        run_wave._STRIPPED = True
        try:
            r = run_wave.call_agent(prompt, DRAFT_MODEL, label="intake", cwd=pr.p["base"],
                                    fuse=Fuse(label="intake"), stage="intake", retries=2,
                                    timeout=600, tools="")
        except FileNotFoundError as exc:
            raise Plain("lit2db reads papers with Claude Code, and it is not installed on this "
                        "Mac. Install it from claude.com/claude-code, sign in once by typing "
                        "claude in Terminal, then try again.") from exc
        except run_wave.CredentialFailure as exc:
            raise Plain("Claude Code is installed but not signed in. Type claude in Terminal, "
                        "sign in, then try again.") from exc
        if not r["ok"]:
            raise Plain(f"The draft did not come back. Claude said: {r['text'][-300:]}")
        try:
            proposal = intake.parse_proposal(r["text"])
        except ValueError as exc:
            raise Plain(f"The draft came back in a shape lit2db could not read ({exc}). "
                        "Try again; drafts vary.") from exc
        it = pr.intake()
        it.update(proposal=proposal, rulings=intake.default_rulings(proposal),
                  rulings_confirmed=False,
                  proposal_meta={"model": DRAFT_MODEL, "at": _now(),
                                 "work_tokens": r.get("work_tokens"),
                                 "tokens": r.get("tokens"), "for_question": it.get("question")})
        pr.save_intake(it)
        return {"columns": len(proposal["columns"])}

    # step 3
    def preview(self, pr: Project, query: str) -> dict:
        try:
            out = project.preview(query, limit=100, get=self.get)
        except Exception as exc:                               # noqa: BLE001
            raise Plain("Europe PMC did not answer. Check the internet connection and try "
                        f"again. ({type(exc).__name__})") from exc
        it = pr.intake()
        papers = it.get("papers") or {}
        papers.update(query=query, preview=out)
        it["papers"] = papers
        pr.save_intake(it)
        return out

    def approve(self, pr: Project, query: str, abstracts, limit: int) -> dict:
        if abstracts not in (True, False):
            raise Plain("Say whether abstracts may be read when the full paper is not free.")
        if pr.progress()["done"]:
            raise Plain("This database has already started reading, so its table and papers are "
                        "fixed. Start a new database to change them.")
        it = pr.intake()
        preview = (it.get("papers") or {}).get("preview") or {}
        counts = {"hits": preview["hits"]} if preview.get("query") == query else {}
        try:
            spec = intake.build_spec(it, query=query, result_counts=counts,
                                     abstracts=abstracts)
        except ValueError as exc:
            raise Plain(str(exc)) from exc
        if pr.p["spec"].exists():
            pr.p["spec"].replace(pr.p["spec"].with_name("spec.previous.json"))
        _write_json(pr.p["spec"], spec)
        it["papers"] = {**(it.get("papers") or {}), "query": query, "abstracts": abstracts,
                        "limit": int(limit), "approved": _now()}
        pr.save_intake(it)
        try:
            out = project.fetch(pr.root, limit=int(limit), allow_abstracts=abstracts,
                                get=self.get)
        except Exception as exc:                               # noqa: BLE001
            raise Plain("Fetching the papers stopped part way: "
                        f"{exc}. Try again; papers already saved are kept.") from exc
        return out["counts"]

    # step 4
    def start(self, pr: Project, mode: str, audit) -> dict:
        prog = pr.progress()
        if prog["running"]:
            raise Plain("Reading is already running.")
        spec = _read_json(pr.p["spec"], None)
        if not spec:
            raise Plain("Approve the papers first.")
        plan = spec.get("_plan") or {}
        if mode not in ("careful", "quick"):
            raise Plain("Choose Careful or Quick.")
        if mode == "careful" and audit not in (0, 0.2, 1, 1.0, 0.0):
            raise Plain("Say how many of the turned-down values should get a second check.")
        lean = mode == "quick"
        if not pr.config() or not prog["done"]:
            try:
                project.plan(pr.root, entity_type=plan["entity_type"],
                             identity_field=plan["identity_field"],
                             models=["sonnet"] if lean else ["sonnet"] * 3,
                             judge_model="opus", hunter_model="sonnet",
                             judge_audit_fraction=1.0 if lean else float(audit),
                             identity_chain=plan.get("identity_chain"),
                             ordinal_within=plan.get("ordinal_within"),
                             evidence_grounded=plan.get("evidence_grounded"),
                             run_mode="lean" if lean else "fat",
                             plugin_version=_version())
            except (ValueError, KeyError) as exc:
                raise Plain(f"The reading could not be set up: {exc}") from exc
            dry = subprocess.run([sys.executable, str(SCRIPTS / "run_wave.py"), "--config",
                                  str(pr.p["config"]), "--dry-run"],
                                 capture_output=True, text=True, cwd=str(pr.root))
            if dry.returncode != 0:
                raise Plain("A check before reading failed, so nothing was spent: "
                            + (dry.stdout + dry.stderr).strip().splitlines()[-1][:300])
        log = (pr.p["base"] / "run.log").open("a", encoding="utf-8")
        log.write(f"\n[{time.strftime('%H:%M:%S')}] started from the app ({mode})\n")
        log.flush()
        proc = subprocess.Popen([sys.executable, str(SCRIPTS / "run_wave.py"), "--config",
                                 str(pr.p["config"])], cwd=str(pr.root), stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        app = pr.app()
        app.update(run_pid=proc.pid, run_started=_now(), run_mode=mode)
        pr.save_app(app)
        return {"pid": proc.pid}

    def pause(self, pr: Project) -> dict:
        pid = pr.app().get("run_pid")
        if pid and _alive(pid):
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        return {"paused": True}

    # step 5 and 6
    def review(self, pr: Project) -> dict:
        port = self.port + 1
        if self.review_proc and self.review_proc.poll() is None and self.review_for == pr.root:
            return {"url": f"http://127.0.0.1:{port}/"}
        self.stop_review()
        self.review_proc = subprocess.Popen(
            [sys.executable, str(SCRIPTS / "review_ui.py"), "--db", str(pr.p["db"]),
             "--sources", str(pr.p["sources"]), "--port", str(port), "--no-browser",
             "--focus", "judge-supported"], cwd=str(pr.root),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.review_for = pr.root
        time.sleep(0.8)
        return {"url": f"http://127.0.0.1:{port}/"}

    def stop_review(self):
        if self.review_proc and self.review_proc.poll() is None:
            self.review_proc.terminate()
        self.review_proc = None

    def export(self, pr: Project) -> dict:
        project.export(pr.root)
        return pr.results()

    def reveal(self, pr: Project, which: str) -> dict:
        target = pr.p["results"] / f"{which}.csv" if which != "folder" else pr.p["results"]
        if not target.exists():
            raise Plain("That spreadsheet has not been made yet.")
        cmd = ["open", str(target)] if which != "folder" else ["open", str(target)]
        subprocess.run(cmd, check=False)
        return {"opened": str(target)}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "lit2db"

        def log_message(self, *a):                       # quiet: the terminal is the researcher's
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200):
            self._send(code, json.dumps(data, default=str).encode(), "application/json")

        def _local(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def do_GET(self):  # noqa: N802
            if not self._local():
                return self._send(403, b"forbidden", "text/plain")
            url = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            try:
                if url.path in ("/", "/index.html"):
                    return self._file(STATIC / "index.html")
                if url.path.startswith("/static/"):
                    f = (STATIC / url.path[len("/static/"):]).resolve()
                    if STATIC not in f.parents:
                        return self._send(404, b"", "text/plain")
                    return self._file(f)
                if url.path == "/api/home":
                    return self._json({"ok": True, "home": str(app.home),
                                       "here": str(app.here) if app.here else None,
                                       "projects": app.projects()})
                if url.path == "/api/state":
                    return self._json({"ok": True, **app.project(q.get("p")).state()})
                if url.path == "/api/job":
                    return self._json({"ok": True, **app.jobs.get(q.get("id", ""))})
                return self._send(404, b"not found", "text/plain")
            except Plain as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)

        def do_POST(self):  # noqa: N802
            if not self._local():
                return self._send(403, b"forbidden", "text/plain")
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._send(415, b"json only", "text/plain")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"ok": False, "error": "unreadable request"}, 400)
            path = urllib.parse.urlparse(self.path).path
            try:
                return self._json({"ok": True, **(self._route(path, body) or {})})
            except Plain as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:                          # noqa: BLE001
                traceback.print_exc()
                return self._json({"ok": False, "error": f"Something went wrong: {exc}"}, 500)

        def _route(self, path: str, b: dict):
            if path == "/api/new":
                return app.new_project(b.get("name", ""))
            pr = app.project(b.get("p", ""))
            if path == "/api/question":
                it = pr.intake()
                question = (b.get("question") or "").strip()
                if not question:
                    raise Plain("Write the question first.")
                it.update(question=question, seed=(b.get("seed") or "").strip())
                pr.save_intake(it)
                return {}
            if path == "/api/draft":
                return {"job": app.jobs.start("draft", lambda: app.draft(pr))}
            if path == "/api/rulings":
                it = pr.intake()
                if not it.get("proposal"):
                    raise Plain("There is no draft to rule on yet.")
                it["rulings"] = b.get("rulings") or {}
                it["rulings_confirmed"] = bool(b.get("confirm"))
                pr.save_intake(it)
                return {}
            if path == "/api/preview":
                query = (b.get("query") or "").strip()
                if not query:
                    raise Plain("The search is empty.")
                return {"job": app.jobs.start("preview", lambda: app.preview(pr, query))}
            if path == "/api/approve":
                query = (b.get("query") or "").strip()
                return {"job": app.jobs.start("fetch", lambda: app.approve(
                    pr, query, b.get("abstracts"), int(b.get("limit") or 20)))}
            if path == "/api/start":
                return app.start(pr, b.get("mode"), b.get("audit"))
            if path == "/api/pause":
                return app.pause(pr)
            if path == "/api/review":
                return app.review(pr)
            if path == "/api/checked":
                it = pr.intake()
                it["checked"] = _now()
                pr.save_intake(it)
                return {}
            if path == "/api/export":
                return {"results": app.export(pr)}
            if path == "/api/reveal":
                return app.reveal(pr, b.get("which", "folder"))
            raise Plain("Unknown request.")

        def _file(self, f: pathlib.Path):
            if not f.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, f.read_bytes(), ctype)

    return Handler


def serve(*, here: pathlib.Path | None = None, home: pathlib.Path = DEFAULT_HOME,
          port: int = 8770, open_browser: bool = True) -> None:
    app = App(home=home, here=here, port=port)
    app.home.mkdir(parents=True, exist_ok=True)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    except OSError:
        url = f"http://127.0.0.1:{port}/"
        print(f"lit2db is already open at {url}")
        if open_browser:
            webbrowser.open(url)
        return
    url = f"http://127.0.0.1:{port}/"
    print(f"lit2db is open at {url}\nYour databases are kept in {app.home}\n"
          "Leave this window open while you use it. Press Control-C here to close lit2db.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nlit2db closed. Anything still reading carries on; type lit2db to look in.")
    finally:
        app.stop_review()
        httpd.server_close()
