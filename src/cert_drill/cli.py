"""The `drill` command line."""
import argparse
import json
import re
import signal
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

from . import __version__
from .checker import CONTROL_RE, check_deck
from .demo import SAMPLES, prepare_demo_deck, run_demo
from .grading import self_grade
from .model import DeckError, load_deck, read_jsonl
from .replay import default_replay
from .scheduler import NEW_PER_DAY, SESSION_SIZE, Answer, Progress, run_session
from .writer import review_pending, write_from_replay

LETTERS = "ABCD"
BIDI_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
DECK_TEMPLATE = """name = "{name}"
description = "Describe the exam or topic."

[[domains]]
name = "Domain one"
weight = 50

[[domains]]
name = "Domain two"
weight = 50

# Every source needs a source_license: self-written | public-domain | cc-by | own-notes-private
[[sources]]
path = "sources/notes.md"
source_license = "own-notes-private"
"""


def clean(text):
    """Text for the terminal with control characters (including escape sequences) removed."""
    return BIDI_RE.sub("", CONTROL_RE.sub(" ", str(text).replace("\n", " ").replace("\t", " ")))


class TerminalUI:
    def __init__(self, self_grading=False, inp=None, out=print):
        self.self_grading, self.inp, self.out = self_grading, inp or input, out

    def ask(self, q, retry):
        self.out(("\n[again] " if retry else "\n") + f"({clean(q['domain'])}) {clean(q['stem'])}")
        try:
            if q["type"] == "mc":
                for i, o in enumerate(q["options"]):
                    self.out(f"  {LETTERS[i]}) {clean(o)}")
                while True:
                    raw = self.inp("answer (A-D, q to stop): ").strip().upper()
                    if raw == "Q":
                        return None
                    if len(raw) == 1 and raw in LETTERS:
                        return Answer(choice=LETTERS.index(raw))
            text = self.inp("your answer (q to stop): ").strip()
            if text.lower() == "q":
                return None
            if not self.self_grading:
                return Answer(text=text)
            self.out("marking points: " + "; ".join(clean(p["text"]) for p in q["marking_points"]))
            while True:
                try:
                    return Answer(self_grade=self_grade(int(self.inp("grade yourself 0-5: ").strip())))
                except ValueError:
                    continue
        except EOFError:
            return None

    def show(self, q, outcome):
        self.out(("right" if outcome.correct else "not yet") + (f" ({clean(outcome.detail)})" if outcome.detail else ""))
        self.out(clean(q["explanation"]))
        cites = q.get("citations") or [p["citation"] for p in q.get("marking_points", [])]
        for c in cites[:2]:
            self.out(f'  "{clean(c["quote"])}" ({clean(c["source"])})')


def _deck(args):
    return load_deck(args.deck)


def _approved(deck):
    """The approved questions, re-checked on every load: a hand-edited file cannot slip a bad key or record past the checker."""
    qs = read_jsonl(deck.path("questions.jsonl"))
    rep = check_deck(deck, qs)
    if not rep.ok:
        raise DeckError(f"questions.jsonl fails the checks ({len(rep.per_question) + len(rep.batch)} problems); run `drill check --deck ...`")
    return qs


def cmd_init(args):
    from .model import NAME_RE

    if not NAME_RE.fullmatch(args.name):
        raise DeckError("name must be lowercase letters, digits and hyphens (at most 40)")
    root = Path(args.dir) / args.name
    if root.exists():
        raise DeckError(f"{root} already exists")
    (root / "sources").mkdir(parents=True)
    (root / "deck.toml").write_text(DECK_TEMPLATE.format(name=args.name), encoding="utf-8", newline="\n")
    (root / "sources" / "notes.md").write_text("# Notes\n\nPaste your own notes here.\n", encoding="utf-8", newline="\n")
    print(f"created {root}; add your notes under sources/ and list them in deck.toml")


def cmd_write(args):
    deck = _deck(args)
    if args.live:
        return cmd_write_live(args, deck)
    replay = Path(args.replay) if args.replay else default_replay("write", deck.name)
    blind = Path(args.blind) if args.blind else default_replay("blind", deck.name)
    res = write_from_replay(deck, replay, blind if blind.is_file() else None)
    label = "synthetic replay (hand-written, not model output)" if res.synthetic else "recorded replay"
    print(f"{label}: {res.written} proposed, {res.passed} passed checks (waiting for review), {res.rejected} rejected, {res.flagged} key(s) flagged")


def cmd_write_live(args, deck):
    """Estimate first; send only with --go; a recording replays a past run at $0 with no client."""
    from .live import BLIND_MODEL, HARD_CAP_USD, WRITER_MODEL, Budget, Caller, Recording, estimate_write, run_write

    n_req, usd = estimate_write(deck, args.count)
    print(f"live write plan: {n_req} requests ({WRITER_MODEL} writer, {BLIND_MODEL} blind pass) for {args.count} questions; "
          f"estimate at most ${usd:.4f} (hard cap ${HARD_CAP_USD:.2f})")
    models = f"{WRITER_MODEL} + {BLIND_MODEL}"
    if args.go:
        if usd > HARD_CAP_USD:
            raise DeckError(f"the estimate is over the ${HARD_CAP_USD:.2f} cap; ask for fewer questions")
        rec = Recording.open(args.recording or Path("out") / "live" / f"write-{deck.name}.jsonl", models)
        caller = Caller("live", recording=rec, budget=Budget(HARD_CAP_USD), go=True)
    elif args.recording:
        caller = Caller("replay", recording=Recording.open(args.recording, models))
    else:
        print("estimate only: nothing was sent. Add --go to call the API (needs ANTHROPIC_API_KEY and the live extra).")
        return 0
    res = run_write(deck, caller, args.count)
    spent = f"${caller.budget.spent:.4f} in {caller.sent} calls" if caller.mode == "live" else "$0.00 (replayed from the recording)"
    print(f"recorded model output ({clean(caller.recording.models)}, {clean(caller.recording.day)}): {res.written} proposed, {res.passed} passed checks "
          f"(waiting for review), {res.rejected} rejected, {res.flagged} key(s) flagged; spent {spent}")
    return 0


def cmd_check(args):
    deck = _deck(args)
    qs = read_jsonl(deck.path("questions.jsonl"))
    rep = check_deck(deck, qs)
    for qid, reasons in rep.per_question.items():
        for r in reasons:
            print(f"{clean(qid)}: {clean(r)}")
    for r in rep.batch:
        print(f"batch: {clean(r)}")
    if rep.ok:
        print(f"ok: {rep.total} questions pass the checks")
        return 0
    return 1


def cmd_review(args):
    deck = _deck(args)
    approve, reject = set(args.approve or []), set(args.reject or [])

    def decide(entry):
        qid = entry["q"]["id"]
        if qid in approve or (args.approve_clean and not entry["flags"]):
            return "a"
        if qid in reject:
            return "r"
        if args.approve or args.reject or args.approve_clean:
            return "s"
        print(f"\n{clean(entry['q']['id'])} ({clean(entry['q']['domain'])}): {clean(entry['q']['stem'])}")
        if entry["q"]["type"] == "mc":
            opts = entry["q"].get("options")
            if not isinstance(opts, list) or len(opts) != 4:  # a hand-edited pending.jsonl: show it, never index past D
                print("  MALFORMED: a multiple-choice question needs exactly 4 options; approving it will be refused")
            else:
                for i, o in enumerate(opts):
                    print(f"  {LETTERS[i]}{'*' if i == entry['q'].get('key') else ' '} {clean(o)}")
        if entry["flags"]:
            print("  CHECK THIS KEY: the blind pass answered differently")
        try:
            raw = input("approve (a), reject (r), skip (s): ").strip().lower()
        except EOFError:
            return "s"
        return raw if raw in ("a", "r") else "s"

    ok, rej, left = review_pending(deck, decide)
    print(f"approved {ok}, rejected {rej}, still pending {left}")


def cmd_study(args):
    deck = _deck(args)
    qs = _approved(deck)
    if not qs:
        raise DeckError("no approved questions yet: run `drill write` and `drill review` first")
    progress = Progress(deck.path("progress.sqlite"))
    try:
        out = run_session(deck, qs, progress, date.today(), TerminalUI(args.self_grade), size=args.count, new_per_day=args.new_per_day)
    finally:
        progress.close()
    print(f"\n{len(out)} answers, {sum(1 for o in out if o.correct)} right")


def cmd_stats(args):
    deck = _deck(args)
    qs = _approved(deck)
    progress = Progress(deck.path("progress.sqlite"))
    states, acc, today = progress.states(), progress.domain_accuracy(deck.domain_names), date.today()
    progress.close()
    print(f"{'domain':<24}{'questions':>10}{'seen':>6}{'due':>5}{'accuracy':>10}")
    for d in deck.domain_names:
        ids = [q["id"] for q in qs if q["domain"] == d]
        seen = [i for i in ids if i in states]
        due = sum(1 for i in seen if states[i].due <= today)
        print(f"{clean(d):<24}{len(ids):>10}{len(seen):>6}{due:>5}{acc[d]:>10.2f}")


def cmd_export(args):
    deck = _deck(args)
    progress = Progress(deck.path("progress.sqlite"))
    data = {"deck": deck.name, "questions": _approved(deck),
            "reviews": [{"qid": q, "domain": d, "day": day.isoformat(), "grade": g} for _, q, d, day, g in progress.reviews()]}
    progress.close()
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {args.out}")
    else:
        print(text)


def cmd_forget(args):
    deck = _deck(args)
    if not args.yes:
        try:
            if input(f"Delete all progress for {deck.name}? Type yes: ").strip().lower() != "yes":
                print("nothing deleted")
                return 1
        except EOFError:
            print("nothing deleted")
            return 1
    progress = Progress(deck.path("progress.sqlite"))
    progress.forget()
    progress.close()
    deck.path("progress.sqlite").unlink(missing_ok=True)
    print(f"progress for {deck.name} deleted")


def cmd_dispute(args):
    deck = _deck(args)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", args.question):
        raise DeckError("bad question id")
    progress = Progress(deck.path("progress.sqlite"))
    try:
        done = progress.dispute(args.question, args.grade, date.today())
    finally:
        progress.close()
    if not done:
        raise DeckError(f"{args.question} has no scheduled review to dispute")
    print(f"recorded: {args.question} now counts as grade {args.grade}")


def cmd_demo(args):
    run_demo(sample=args.sample)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def cmd_serve(args):
    from .server import DrillApp, make_server

    with tempfile.TemporaryDirectory(prefix="cert-drill-serve-") as tmp:
        if args.demo:
            deck = prepare_demo_deck(tmp, args.sample)
            print(f"demo deck {deck.name}: questions from a synthetic replay (hand-written, not model output); "
                  "progress is temporary and deleted when the server stops", flush=True)
        else:
            deck = load_deck(args.deck)
        qs = _approved(deck)
        if not qs:
            raise DeckError("no approved questions yet: run `drill write` and `drill review` first")
        progress = Progress(deck.path("progress.sqlite"))
        try:
            srv = make_server(DrillApp(deck, qs, progress, size=args.count, new_per_day=args.new_per_day), args.port)
            print("local only (127.0.0.1); the link carries a one-time access token; Ctrl+C stops", flush=True)
            print(f"open {srv.start_url()}", flush=True)
            try:
                previous = signal.signal(signal.SIGTERM, _interrupt)  # `kill` stops cleanly too: the demo's temporary copy is deleted
            except ValueError:
                previous = None  # not the main thread: Ctrl+C handling is unchanged
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                print("stopped", flush=True)
            finally:
                srv.server_close()
                if previous is not None:
                    signal.signal(signal.SIGTERM, previous)
        finally:
            progress.close()


def cmd_eval(args):
    from .evals import table, write_eval

    result, path = write_eval(args.out)
    print(table(result))
    print(f"wrote {path}")


def build_parser():
    p = argparse.ArgumentParser(prog="drill", description="Drill for any exam, from your own notes.")
    p.add_argument("--version", action="version", version=f"drill {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, deck=True):
        s = sub.add_parser(name, help=help_)
        s.set_defaults(fn=fn)
        if deck:
            s.add_argument("--deck", required=True, help="path to the deck folder (holds deck.toml)")
        return s

    s = add("init", cmd_init, "create an empty deck folder", deck=False)
    s.add_argument("name")
    s.add_argument("--dir", default="decks/private")
    s = add("write", cmd_write, "propose questions from a replay file and run the checks")
    s.add_argument("--replay")
    s.add_argument("--blind")
    s.add_argument("--live", action="store_true", help="plan a real-model write and print the cost estimate (sends nothing)")
    s.add_argument("--go", action="store_true", help="with --live: send the requests (needs ANTHROPIC_API_KEY; hard cap $1.00)")
    s.add_argument("--count", type=int, default=20, help="with --live: questions to ask for (1-100)")
    s.add_argument("--recording", help="with --live: the recording to reuse (default out/live/write-<deck>.jsonl with --go)")
    add("check", cmd_check, "check every approved question (exit 1 if any fails)")
    s = add("review", cmd_review, "approve or reject pending questions")
    s.add_argument("--approve", action="append", metavar="ID")
    s.add_argument("--reject", action="append", metavar="ID")
    s.add_argument("--approve-clean", action="store_true", help="approve every pending question with no flag")
    s = add("study", cmd_study, "run one study session in the terminal")
    s.add_argument("--count", type=int, default=SESSION_SIZE)
    s.add_argument("--new-per-day", type=int, default=NEW_PER_DAY)
    s.add_argument("--self-grade", action="store_true", help="grade free-text answers yourself (0-5)")
    add("stats", cmd_stats, "accuracy and due counts per domain")
    s = add("export", cmd_export, "export questions and review history as JSON")
    s.add_argument("--out")
    s = add("forget", cmd_forget, "delete this deck's progress")
    s.add_argument("--yes", action="store_true")
    s = add("dispute", cmd_dispute, "override the grade of your latest answer to a question")
    s.add_argument("--question", required=True)
    s.add_argument("--grade", type=int, required=True)
    s = add("serve", cmd_serve, "study on a phone-width page at 127.0.0.1 (local only)", deck=False)
    where = s.add_mutually_exclusive_group(required=True)
    where.add_argument("--deck", help="path to the deck folder (holds deck.toml)")
    where.add_argument("--demo", action="store_true", help="serve a temporary copy of a sample deck")
    s.add_argument("--sample", choices=SAMPLES, default=SAMPLES[0])
    s.add_argument("--port", type=int, default=8731, help="1024-65535 (0 picks a free port)")
    s.add_argument("--count", type=int, default=SESSION_SIZE)
    s.add_argument("--new-per-day", type=int, default=NEW_PER_DAY)
    s = add("eval", cmd_eval, "measure the checker and the stand-in grader on hand-written sets (no model call)", deck=False)
    s.add_argument("--out", default=str(Path("evals") / "results"), help="folder for eval.json (default evals/results)")
    s = add("demo", cmd_demo, "run the whole flow on a sample deck with synthetic replays (no model call)", deck=False)
    s.add_argument("--sample", choices=SAMPLES, default=SAMPLES[0])
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except (DeckError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (KeyError, TypeError, sqlite3.Error) as e:
        print(f"error: malformed data file ({type(e).__name__})", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: {e.strerror or e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
