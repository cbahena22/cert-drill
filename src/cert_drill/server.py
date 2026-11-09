"""`drill serve`: a phone-width study page on 127.0.0.1 only. Standard library `http.server`, one request at a time.

Every request: the Host header must be this server (127.0.0.1:<port> or localhost:<port>), which stops DNS-rebinding pages.
Every /api/ call: the per-start random token must arrive as `X-Drill-Token` (the page reads it from the URL fragment, which is
never sent to a server). Every POST: `Origin` absent or this server's own origin, a JSON content type and a small body.
Deck text reaches the page only as JSON with `<`, `>` and `&` escaped; the page sets all text with `textContent`.
"""
import hmac
import json
import secrets
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

from .grading import MAX_ANSWER_CHARS, standin_grade
from .replay import DATA
from .scheduler import NEW_PER_DAY, SESSION_SIZE, Answer, SessionRunner

BIND = "127.0.0.1"
MAX_BODY = 16_384
TOKEN_BYTES = 32
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8")}
ANSWER_FIELDS = ("choice", "text", "self_grade")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def to_json(obj):
    """JSON safe to serve: `<`, `>` and `&` escaped, so no deck or answer text can read as markup even if sniffed."""
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").encode("utf-8")


def static_file(name):
    return (DATA / "web" / name).read_bytes()


class DrillApp:
    """The study state behind the page: one deck, its approved questions, the progress file and the current session."""

    def __init__(self, deck, questions, progress, today=date.today, grader=standin_grade, token=None,
                 size=SESSION_SIZE, new_per_day=NEW_PER_DAY):
        self.deck, self.questions, self.progress, self.today = deck, questions, progress, today
        self.base_grader, self.size, self.new_per_day = grader, size, new_per_day
        self.token = token or secrets.token_urlsafe(TOKEN_BYTES)
        self.runner = None
        self._last_grade = None

    def _grader(self, q, text):
        self._last_grade = self.base_grader(q, text)
        return self._last_grade

    def new_session(self):
        self.runner = SessionRunner(self.deck, self.questions, self.progress, self.today(), self.size, self.new_per_day, self._grader)
        return self.session_view()

    def session_view(self):
        if self.runner is None:
            return self.new_session()
        cur = self.runner.current()
        out = self.runner.outcomes
        view = {"deck": self.deck.name, "answered": len(out), "right": sum(1 for o in out if o.correct),
                "remaining": self.runner.remaining, "done": cur is None, "question": None}
        if cur is not None:
            q, retry = cur
            view["question"] = public_question(q, retry)
        return view

    def answer(self, body):
        if self.runner is None or self.runner.current() is None:
            raise ApiError(409, "no question is waiting; start a new session")
        q, _ = self.runner.current()
        answer = parse_answer(body, q)
        self._last_grade = None
        outcome = self.runner.answer(answer)
        feedback = {"correct": outcome.correct, "grade": outcome.grade, "retry": outcome.retry, "detail": outcome.detail,
                    "explanation": q["explanation"], "citations": citations_of(q)}
        if q["type"] == "mc":
            feedback["key"] = q["key"]
            feedback["choice"] = answer.choice
        else:
            feedback["marking_points"] = [p["text"] for p in q["marking_points"]]
            if self._last_grade is not None:
                feedback["points"] = [{"verdict": r.final, "claimed": r.claimed, "quote": r.quote, "note": r.note} for r in self._last_grade.points]
        return {"feedback": feedback, "session": self.session_view()}

    def stats(self):
        states, acc, today = self.progress.states(), self.progress.domain_accuracy(self.deck.domain_names), self.today()
        rows = []
        for d in self.deck.domains:
            ids = [q["id"] for q in self.questions if q["domain"] == d.name]
            seen = [i for i in ids if i in states]
            rows.append({"name": d.name, "weight": d.weight, "questions": len(ids), "seen": len(seen),
                         "due": sum(1 for i in seen if states[i].due <= today), "accuracy": round(acc[d.name], 4)})
        return {"deck": self.deck.name, "domains": rows}


def public_question(q, retry):
    """What the page may see before an answer: never the key, the explanation or the marking points."""
    view = {"id": q["id"], "type": q["type"], "domain": q["domain"], "stem": q["stem"], "retry": retry}
    if q["type"] == "mc":
        view["options"] = list(q["options"])
    else:
        view["points"] = len(q["marking_points"])
    return view


def citations_of(q):
    cites = q.get("citations") or [p["citation"] for p in q.get("marking_points", [])]
    return [{"source": c["source"], "quote": c["quote"]} for c in cites]


def _whole(v, low, high):
    return isinstance(v, int) and not isinstance(v, bool) and low <= v <= high


def parse_answer(body, q):
    if not isinstance(body, dict) or set(body) - {"qid", *ANSWER_FIELDS}:
        raise ApiError(400, "answer must be an object with qid and one of: choice, text, self_grade")
    if body.get("qid") != q["id"]:
        raise ApiError(409, "that is not the question waiting for an answer")
    given = [f for f in ANSWER_FIELDS if f in body]
    if len(given) != 1:
        raise ApiError(400, "give exactly one of: choice, text, self_grade")
    field, value = given[0], body[given[0]]
    if q["type"] == "mc":
        if field != "choice" or not _whole(value, 0, 3):
            raise ApiError(400, "choice must be 0, 1, 2 or 3")
        return Answer(choice=value)
    if field == "text":
        if not isinstance(value, str) or len(value) > MAX_ANSWER_CHARS:
            raise ApiError(400, f"text must be a string of at most {MAX_ANSWER_CHARS} characters")
        return Answer(text=value)
    if field == "self_grade" and _whole(value, 0, 5):
        return Answer(self_grade=value)
    raise ApiError(400, "a free-text answer needs text, or a self_grade from 0 to 5")


class Handler(BaseHTTPRequestHandler):
    server_version = "drill"
    sys_version = ""
    protocol_version = "HTTP/1.0"  # one response per connection: a browser's idle keep-alive cannot block the next request
    timeout = 10  # a stalled client cannot hold the one-at-a-time server for long

    def log_message(self, format, *args):  # noqa: A002 - the base class names it so
        pass  # no request log: paths are not secret, but there is nothing to keep

    # --- responses
    def _send(self, status, body, ctype):
        self.send_response(status)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, to_json(obj), "application/json; charset=utf-8")

    def _refuse(self, status, message):
        self.close_connection = True
        self._json(status, {"error": message})

    # --- checks
    def _host_ok(self):
        hosts = self.headers.get_all("Host") or []
        return len(hosts) == 1 and hosts[0] in self.server.allowed_hosts

    def _token_ok(self):
        got = self.headers.get_all("X-Drill-Token") or []
        return len(got) == 1 and hmac.compare_digest(got[0].encode("utf-8", "replace"), self.server.app.token.encode("utf-8"))

    def _origin_ok(self):
        got = self.headers.get_all("Origin") or []
        return not got or (len(got) == 1 and got[0] in self.server.allowed_origins)

    def _path(self):
        return self.path.split("?", 1)[0].split("#", 1)[0]

    def _guard(self):
        """Host for everything, Origin for POST, token for /api/. Returns the path or None after a 403."""
        if not self._host_ok():
            self._refuse(403, "forbidden: bad Host")
            return None
        path = self._path()
        if self.command == "POST" and not self._origin_ok():
            self._refuse(403, "forbidden: cross-origin request")
            return None
        if path.startswith("/api/") and not self._token_ok():
            self._refuse(403, "forbidden: missing or wrong token")
            return None
        return path

    def _body(self):
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(415, "send application/json")
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(411, "send a Content-Length, not a chunked body")
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.isdigit():
            raise ApiError(411, "Content-Length required")
        n = int(raw)
        if n > MAX_BODY:
            raise ApiError(413, f"body over {MAX_BODY} bytes")
        data = self.rfile.read(n)
        if not data:
            return {}
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "body is not JSON") from None

    # --- methods
    def do_GET(self):  # noqa: N802 - http.server naming
        path = self._guard()
        if path is None:
            return
        app = self.server.app
        try:
            if path in STATIC:
                name, ctype = STATIC[path]
                self._send(200, static_file(name), ctype)
            elif path == "/api/session":
                self._json(200, app.session_view())
            elif path == "/api/stats":
                self._json(200, app.stats())
            else:
                self._json(404, {"error": "not found"})
        except ApiError as e:
            self._refuse(e.status, e.message)
        except Exception:  # noqa: BLE001 - never a traceback or internal detail to the page
            self._refuse(500, "internal error")

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_POST(self):  # noqa: N802
        path = self._guard()
        if path is None:
            return
        app = self.server.app
        try:
            body = self._body()
            if path == "/api/session":
                self._json(200, app.new_session())
            elif path == "/api/answer":
                self._json(200, app.answer(body))
            else:
                self._json(404, {"error": "not found"})
        except ApiError as e:
            self._refuse(e.status, e.message)
        except Exception:  # noqa: BLE001
            self._refuse(500, "internal error")

    def _not_allowed(self):
        if self._guard() is not None:
            self._refuse(405, "method not allowed")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed  # noqa: N815


class DrillServer(HTTPServer):
    # On Windows SO_REUSEADDR lets another local program bind the same port and serve its own page; elsewhere it only skips TIME_WAIT.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, app, port=0):
        super().__init__((BIND, port), Handler)
        self.app = app
        self.port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.allowed_origins = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}

    def start_url(self):
        return f"http://127.0.0.1:{self.port}/#t={self.app.token}"

    def handle_error(self, request, client_address):
        pass  # a broken connection is not worth a traceback on the terminal


def make_server(app, port=0):
    if not isinstance(port, int) or isinstance(port, bool) or not (port == 0 or 1024 <= port <= 65535):
        raise ValueError("port must be 0 or 1024-65535")
    return DrillServer(app, port)
