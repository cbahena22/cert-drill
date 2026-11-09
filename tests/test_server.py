"""The local study server: every refusal (403 and friends) and the happy path, over a real 127.0.0.1 socket."""
import http.client
import json
import re
import threading
from datetime import date

import pytest

from cert_drill import cli
from cert_drill.demo import prepare_demo_deck
from cert_drill.model import read_jsonl
from cert_drill.replay import DATA
from cert_drill.scheduler import Progress
from cert_drill.server import CSP, MAX_BODY, DrillApp, make_server, to_json

DAY = date(2026, 1, 5)
XSS = "<script>alert(1)</script>"


def start(tmp_path, mutate=None, **kw):
    deck = prepare_demo_deck(tmp_path)
    qs = read_jsonl(deck.path("questions.jsonl"))
    if mutate:
        qs = mutate(qs)
    progress = Progress(deck.path("progress.sqlite"), check_same_thread=False)
    srv = make_server(DrillApp(deck, qs, progress, today=lambda: DAY, **kw), 0)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    return srv, progress, t


@pytest.fixture
def served(tmp_path):
    srv, progress, t = start(tmp_path)
    yield srv
    srv.shutdown()
    srv.server_close()
    t.join(5)
    progress.close()


@pytest.fixture
def served_with(tmp_path):
    made = []

    def make(mutate, **kw):
        made.append(start(tmp_path, mutate, **kw))
        return made[-1][0]
    yield make
    for srv, progress, t in made:
        srv.shutdown()
        srv.server_close()
        t.join(5)
        progress.close()


def call(srv, method, path, *, host=True, token=True, headers=(), body=None, raw=None):
    """One request. host=True sends the right Host; a string sends that Host; None sends none."""
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    if host is True:
        c.putheader("Host", f"127.0.0.1:{srv.port}")
    elif host is not None:
        c.putheader("Host", host)
    if token is True:
        c.putheader("X-Drill-Token", srv.app.token)
    elif token is not None:
        c.putheader("X-Drill-Token", token)
    data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
    sent = {k.lower() for k, _ in headers}
    for k, v in headers:
        c.putheader(k, v)
    if data is not None:
        if "content-type" not in sent:
            c.putheader("Content-Type", "application/json")
        if "content-length" not in sent:
            c.putheader("Content-Length", str(len(data)))
    c.endheaders(data)
    r = c.getresponse()
    out = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hdrs, out


def jget(srv, path="/api/session"):
    status, _, body = call(srv, "GET", path)
    assert status == 200, body
    return json.loads(body)


pytestmark = pytest.mark.loopback


# --- the happy path
def test_good_host_and_token_get_the_session(served):
    status, hdrs, body = call(served, "GET", "/api/session")
    assert status == 200 and hdrs["content-type"].startswith("application/json")
    s = json.loads(body)
    assert s["deck"] == "networking-basics" and not s["done"] and s["question"]["stem"]
    assert served.app.token.encode() not in body


def test_localhost_host_is_accepted_too(served):
    assert call(served, "GET", "/api/session", host=f"localhost:{served.port}")[0] == 200


def test_the_page_and_its_assets_are_served_with_the_strict_headers(served):
    for path, kind in (("/", "text/html"), ("/app.js", "text/javascript"), ("/app.css", "text/css")):
        status, hdrs, _ = call(served, "GET", path, token=None)
        assert status == 200 and hdrs["content-type"].startswith(kind), path
        assert hdrs["content-security-policy"] == CSP and hdrs["x-content-type-options"] == "nosniff"
        assert hdrs["referrer-policy"] == "no-referrer" and hdrs["x-frame-options"] == "DENY"
    assert "script-src 'self'" in CSP and "default-src 'none'" in CSP and "unsafe" not in CSP


def test_the_question_never_carries_its_key_explanation_or_marking_points(served):
    for _ in range(15):
        s = jget(served)
        q = s["question"]
        if q is None:
            break
        assert set(q) <= {"id", "type", "domain", "stem", "retry", "options", "points"}
        body = {"qid": q["id"], "choice": 0} if q["type"] == "mc" else {"qid": q["id"], "self_grade": 5}
        status, _, out = call(served, "POST", "/api/answer", body=body)
        assert status == 200, out


def test_answering_records_progress_and_returns_feedback(served):
    q = jget(served)["question"]
    full = next(x for x in served.app.questions if x["id"] == q["id"])
    body = {"qid": q["id"], "choice": full["key"]} if q["type"] == "mc" else {"qid": q["id"], "self_grade": 5}
    status, _, out = call(served, "POST", "/api/answer", body=body)
    res = json.loads(out)
    assert status == 200 and res["feedback"]["correct"] and res["feedback"]["citations"]
    assert res["session"]["answered"] == 1
    assert served.app.progress.reviews()[0][1] == q["id"]
    stats = jget(served, "/api/stats")
    assert {d["name"] for d in stats["domains"]} == set(served.app.deck.domain_names)


def test_a_new_session_can_be_started_with_a_same_origin_post(served):
    origin = f"http://127.0.0.1:{served.port}"
    status, _, out = call(served, "POST", "/api/session", headers=[("Origin", origin)], body={})
    assert status == 200 and json.loads(out)["answered"] == 0


# --- refusals: Host
@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1", "localhost", "127.0.0.1:1", "192.0.2.1:{port}",
                                  "127.0.0.1:{port}.evil.example", None])
def test_a_bad_or_missing_host_is_refused_everywhere(served, host):
    h = host.format(port=served.port) if isinstance(host, str) else None
    for path in ("/", "/app.js", "/api/session"):
        status, _, body = call(served, "GET", path, host=h)
        assert status == 403, (path, host)
    assert call(served, "POST", "/api/session", host=h, body={})[0] == 403


def test_two_host_headers_are_refused(served):
    status, _, _ = call(served, "GET", "/api/session", headers=[("Host", "evil.example")])
    assert status == 403


# --- refusals: token
@pytest.mark.parametrize("token", [None, "", "wrong", "x" * 43])
def test_api_calls_without_the_right_token_are_refused(served, token):
    assert call(served, "GET", "/api/session", token=token)[0] == 403
    assert call(served, "GET", "/api/stats", token=token)[0] == 403
    assert call(served, "POST", "/api/answer", token=token, body={})[0] == 403


def test_two_token_headers_are_refused(served):
    assert call(served, "GET", "/api/session", headers=[("X-Drill-Token", served.app.token)])[0] == 403


def test_the_token_is_random_per_start(tmp_path):
    from cert_drill.demo import prepare_demo_deck as prep

    deck = prep(tmp_path)
    a, b = DrillApp(deck, [], None), DrillApp(deck, [], None)
    assert a.token != b.token and len(a.token) >= 40


# --- refusals: cross-origin writes
@pytest.mark.parametrize("origin", ["http://evil.example", "null", "http://127.0.0.1:1", "https://127.0.0.1:{port}", "http://localhost:{port}.evil.example"])
def test_a_cross_origin_post_is_refused_even_with_the_token(served, origin):
    o = origin.format(port=served.port)
    for path in ("/api/session", "/api/answer"):
        assert call(served, "POST", path, headers=[("Origin", o)], body={})[0] == 403, (path, origin)


def test_a_post_without_origin_is_allowed(served):
    assert call(served, "POST", "/api/session", body={})[0] == 200


# --- refusals: bodies and methods
def test_post_needs_json_a_length_and_a_small_body(served):
    assert call(served, "POST", "/api/session", raw=b"{}", headers=[("Content-Type", "text/plain")])[0] == 415
    assert call(served, "POST", "/api/session", raw=b"x" * (MAX_BODY + 1))[0] == 413
    assert call(served, "POST", "/api/session", raw=b"not json")[0] == 400
    assert call(served, "POST", "/api/session", headers=[("Content-Type", "application/json")])[0] == 411
    assert call(served, "POST", "/api/session", raw=b"{}", headers=[("Content-Length", "-1")])[0] == 411
    assert call(served, "POST", "/api/session", raw=b"{}", headers=[("Transfer-Encoding", "chunked")])[0] == 411


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS"])
def test_other_methods_are_refused(served, method):
    assert call(served, method, "/api/session")[0] == 405
    assert call(served, method, "/api/session", token=None)[0] == 403


def test_unknown_paths_are_404_and_query_strings_do_not_skip_checks(served):
    assert call(served, "GET", "/api/nothing")[0] == 404
    assert call(served, "GET", "/api/session?x=1", token=None)[0] == 403
    assert call(served, "GET", "/../../etc/passwd")[0] == 404


@pytest.mark.parametrize("bad", [{"qid": "other", "choice": 0}, {"choice": 0}, {"qid": "{qid}"}, {"qid": "{qid}", "choice": 7},
                                 {"qid": "{qid}", "choice": True}, {"qid": "{qid}", "choice": "1"}, {"qid": "{qid}", "choice": 0, "text": "x"},
                                 {"qid": "{qid}", "choice": 0, "extra": 1}, ["{qid}"], {"qid": "{qid}", "text": "an answer"}])
def test_malformed_answers_are_refused_and_record_nothing(served, bad):
    q = jget(served)["question"]
    if q["type"] != "mc":
        pytest.skip("first question is free text")
    body = json.loads(json.dumps(bad).replace("{qid}", q["id"]))
    status = call(served, "POST", "/api/answer", body=body)[0]
    assert status in (400, 409)
    assert served.app.progress.reviews() == []


def test_a_free_text_answer_must_be_capped_text(served_with):
    srv = served_with(lambda qs: [q for q in qs if q["type"] == "free"])
    q = jget(srv)["question"]
    assert q["type"] == "free" and "options" not in q
    assert call(srv, "POST", "/api/answer", body={"qid": q["id"], "text": "x" * 4001})[0] == 400
    assert call(srv, "POST", "/api/answer", body={"qid": q["id"], "text": 5})[0] == 400
    assert call(srv, "POST", "/api/answer", body={"qid": q["id"], "self_grade": 6})[0] == 400
    assert call(srv, "POST", "/api/answer", body={"qid": q["id"], "choice": 1})[0] == 400


# --- text-only rendering: markup in a stem, an option and an answer comes back as escaped JSON text
def test_script_in_a_stem_and_an_option_is_served_escaped(served_with):
    def mutate(qs):
        out = []
        for q in qs:
            q = dict(q, stem=XSS + " " + q["stem"])
            if q["type"] == "mc":
                q["options"] = [XSS + " " + q["options"][0]] + q["options"][1:]
            out.append(q)
        return out
    srv = served_with(mutate)
    status, _, raw = call(srv, "GET", "/api/session")
    assert status == 200 and b"<script" not in raw.lower() and b"\\u003cscript\\u003e" in raw
    q = json.loads(raw)["question"]
    assert q["stem"].startswith(XSS)
    if q["type"] == "mc":
        assert q["options"][0].startswith(XSS)
    status, _, raw = call(srv, "POST", "/api/answer", body={"qid": q["id"], "choice": 0} if q["type"] == "mc" else {"qid": q["id"], "self_grade": 1})
    assert status == 200 and b"<script" not in raw.lower()


def test_script_in_a_free_text_answer_is_served_escaped(served_with):
    srv = served_with(lambda qs: [q for q in qs if q["id"] == "addr-f1"])
    q = jget(srv)["question"]
    answer = XSS + " A subnet mask separates the network portion of an address from the host portion."
    status, _, raw = call(srv, "POST", "/api/answer", body={"qid": q["id"], "text": answer})
    assert status == 200 and b"<script" not in raw.lower()
    fb = json.loads(raw)["feedback"]
    assert any(XSS in p["quote"] for p in fb["points"])  # the text is kept as text; the page shows it with textContent


def test_to_json_escapes_markup_characters():
    assert to_json({"a": "<b>&</b>"}) == b'{"a": "\\u003cb\\u003e\\u0026\\u003c/b\\u003e"}'
    assert json.loads(to_json({"a": "<b>&</b>"})) == {"a": "<b>&</b>"}


def test_the_page_script_sets_text_only_and_loads_nothing_external():
    js = (DATA / "web" / "app.js").read_text(encoding="utf-8")
    html = (DATA / "web" / "index.html").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute", "srcdoc", "javascript:"):
        assert bad not in js, bad
    assert "textContent" in js
    assert not re.search(r"(?i)https?:|//[a-z0-9.-]+\.[a-z]{2,}/", js + html)
    assert re.findall(r"<script\b[^>]*>", html) == ['<script src="/app.js" defer>']
    assert not re.search(r"(?i)\son[a-z]+\s*=|\sstyle\s*=|<style", html)
    assert re.findall(r'(?:src|href)="([^"]+)"', html) == ["/app.css", "/app.js"]


def test_make_server_refuses_bad_ports(tmp_path):
    for port in (-1, 80, 70000, True, "8765"):
        with pytest.raises(ValueError):
            make_server(None, port)


def test_no_shared_port_on_windows():
    import sys

    from cert_drill.server import DrillServer

    assert DrillServer.allow_reuse_address == (sys.platform != "win32")


def test_drill_serve_prints_a_start_url_with_the_token_last(capsys, monkeypatch):
    from cert_drill import server

    def stop(self, *a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(server.DrillServer, "serve_forever", stop)
    code = cli.main(["serve", "--demo", "--port", "0"])
    out = capsys.readouterr().out
    assert code == 0 and "synthetic replay (hand-written" in out and "stopped" in out
    line = next(ln for ln in out.splitlines() if "#t=" in ln)
    assert re.fullmatch(r"open http://127\.0\.0\.1:\d+/#t=[A-Za-z0-9_-]{40,}", line)
    assert sum("#t=" in ln for ln in out.splitlines()) == 1


def test_drill_serve_needs_a_deck_or_demo(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["serve"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["serve", "--demo", "--deck", "x"])
