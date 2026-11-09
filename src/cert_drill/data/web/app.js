"use strict";
// cert-drill study page. Every piece of deck or answer text is set with textContent; no markup is ever built from data.
(function () {
  var KEY = "drill-t";
  var tok = "";
  var m = /^#t=([A-Za-z0-9_-]{20,128})$/.exec(window.location.hash);
  if (m) {
    tok = m[1];
    try { window.sessionStorage.setItem(KEY, tok); } catch (e) { /* storage off: keep it in memory */ }
    window.history.replaceState(null, "", window.location.pathname);  // the token leaves the address bar
  } else {
    try { tok = window.sessionStorage.getItem(KEY) || ""; } catch (e) { tok = ""; }
  }

  var LETTERS = ["A", "B", "C", "D"];
  var current = null;

  function $(id) { return document.getElementById(id); }
  function show(id, on) { $(id).hidden = !on; }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = String(text); }
    return e;
  }

  function api(method, path, body) {
    var opts = { method: method, headers: { "X-Drill-Token": tok }, credentials: "omit", cache: "no-store" };
    if (method === "POST") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body || {});
    }
    return window.fetch(path, opts).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) { throw new Error((data && data.error) || ("HTTP " + r.status)); }
        return data;
      });
    });
  }

  function fail(err) {
    $("error").textContent = tok ? String(err.message || err) : "Open the link that `drill serve` printed (it carries the access token).";
    show("error", true);
  }

  function renderSession(s) {
    show("error", false);
    $("deck").textContent = s.deck;
    $("progress").textContent = s.answered + " answered, " + s.right + " right, " + s.remaining + " to go";
    show("feedback", false);
    if (s.done || !s.question) {
      show("card", false);
      renderDone(s);
      return;
    }
    show("done", false);
    current = s.question;
    $("domain").textContent = current.domain + (current.retry ? " - again" : "");
    $("stem").textContent = current.stem;
    var opts = $("options");
    clear(opts);
    if (current.type === "mc") {
      show("free", false);
      current.options.forEach(function (o, i) {
        var b = el("button", "option", LETTERS[i] + ") " + o);
        b.type = "button";
        b.addEventListener("click", function () { send({ qid: current.id, choice: i }); });
        opts.appendChild(b);
      });
    } else {
      $("text").value = "";
      show("free", true);
    }
    show("card", true);
  }

  function send(body) {
    api("POST", "/api/answer", body).then(renderFeedback).catch(fail);
  }

  function renderFeedback(res) {
    var f = res.feedback;
    show("card", false);
    var v = $("verdict");
    v.textContent = f.correct ? "Right" : "Not yet";
    v.className = "verdict " + (f.correct ? "good" : "bad");
    var detail = "grade " + f.grade + " of 5" + (f.detail ? " - " + f.detail : "");
    if (current && current.type === "mc" && typeof f.key === "number") {
      detail = "answer: " + LETTERS[f.key] + ") " + current.options[f.key] + " | " + detail;
    }
    $("detail").textContent = detail;
    $("explanation").textContent = f.explanation;
    var pts = $("points");
    clear(pts);
    (f.marking_points || []).forEach(function (text, i) {
      var li = el("li", null, text);
      var p = f.points && f.points[i];
      if (p) {
        li.appendChild(el("span", p.verdict === "missed" ? "bad" : "good", " [" + p.verdict + "]"));
        if (p.quote && p.verdict !== "missed") {
          var q = el("blockquote");
          q.appendChild(el("mark", null, p.quote));
          li.appendChild(q);
        }
      }
      pts.appendChild(li);
    });
    var cites = $("citations");
    clear(cites);
    (f.citations || []).forEach(function (c) {
      var q = el("blockquote");
      q.appendChild(el("mark", null, c.quote));
      q.appendChild(el("div", "muted", c.source));
      cites.appendChild(q);
    });
    show("feedback", true);
    $("next").onclick = function () { renderSession(res.session); };
  }

  function renderDone(s) {
    $("summary").textContent = "Session done: " + s.right + " of " + s.answered + " right";
    var bars = $("bars");
    clear(bars);
    api("GET", "/api/stats").then(function (st) {
      st.domains.forEach(function (d) {
        var row = el("div", "bar");
        row.appendChild(el("span", null, d.name + " (" + d.seen + "/" + d.questions + " seen, " + d.due + " due)"));
        row.appendChild(el("span", "muted", Math.round(d.accuracy * 100) + "%"));
        var meter = el("meter");
        meter.min = 0;
        meter.max = 1;
        meter.value = d.accuracy;
        row.appendChild(meter);
        bars.appendChild(row);
      });
    }).catch(fail);
    show("done", true);
  }

  function start() {
    var sg = $("selfgrade");
    for (var g = 0; g <= 5; g += 1) {
      (function (grade) {
        var b = el("button", null, String(grade));
        b.type = "button";
        b.addEventListener("click", function () { send({ qid: current.id, self_grade: grade }); });
        sg.appendChild(b);
      })(g);
    }
    $("send").addEventListener("click", function () { send({ qid: current.id, text: $("text").value }); });
    $("again").addEventListener("click", function () { api("POST", "/api/session", {}).then(renderSession).catch(fail); });
    api("GET", "/api/session").then(renderSession).catch(fail);
  }

  document.addEventListener("DOMContentLoaded", start);
})();
