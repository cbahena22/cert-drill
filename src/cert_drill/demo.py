"""`drill demo`: write from a synthetic replay, let a simulated learner review, then run three study sessions. No model call."""
import hashlib
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from .model import load_deck, read_jsonl
from .replay import DATA, default_replay
from .scheduler import Progress, run_session
from .writer import review_pending, write_from_replay

SAMPLES = ("networking-basics", "us-constitution")
START = date(2026, 1, 5)
WEAK_DOMAIN = {"networking-basics": "Security", "us-constitution": "Congress"}
SESSIONS = 3
BAR = 20


class ScriptedLearner:
    """A simulated learner: strong everywhere but one weak domain. Deterministic (a hash decides each answer), labelled as simulated."""

    def __init__(self, session, weak):
        self.session, self.weak = session, weak
        self.log = []

    def _right(self, q, retry):
        if retry:
            return True
        h = int(hashlib.sha256(f"{q['id']}|{self.session}".encode("utf-8")).hexdigest(), 16) % 100
        return h < (30 if q["domain"] == self.weak else 90)

    def ask(self, q, retry):
        from .scheduler import Answer

        right = self._right(q, retry)
        if q["type"] == "mc":
            return Answer(choice=q["key"] if right else (q["key"] + 1) % 4)
        return Answer(text=" ".join(p["citation"]["quote"] for p in q["marking_points"]) if right else "I am not sure about this one.")

    def show(self, q, outcome):
        self.log.append(outcome)


def _bars(deck, outcomes, out):
    for d in deck.domain_names:
        scheduled = [o for o in outcomes if o.domain == d and not o.retry]
        if scheduled:
            ok = sum(1 for o in scheduled if o.correct)
            n = round(BAR * ok / len(scheduled))
            out(f"    {d:<22} {'#' * n}{'.' * (BAR - n)} {ok}/{len(scheduled)}")


def run_demo(out=print, sample="networking-basics"):
    """Returns a dict of counts; prints the walk-through, ending with the one-line summary."""
    src = DATA / "decks" / sample
    with tempfile.TemporaryDirectory(prefix="cert-drill-demo-") as tmp:
        root = Path(tmp) / sample
        root.mkdir()
        shutil.copy(src / "deck.toml", root / "deck.toml")
        shutil.copytree(src / "sources", root / "sources")
        deck = load_deck(root)
        out(f"deck: {deck.name} ({len(deck.domains)} domains, {len(deck.sources)} sources)")
        res = write_from_replay(deck, default_replay("write", sample), default_replay("blind", sample))
        out(f"write: synthetic replay (hand-written, not model output): {res.written} proposed, {res.passed} passed checks, {res.rejected} rejected")
        for r in read_jsonl(deck.path("rejected.jsonl")):
            out(f"  rejected {r['q'].get('id')}: {r['reasons'][0]}")
        flagged = {e["q"]["id"] for e in read_jsonl(deck.path("pending.jsonl")) if e["flags"]}
        approved, learner_rejected, _ = review_pending(deck, lambda e: "r" if e["flags"] else "a")
        out(f"review (simulated learner): approved {approved}, rejected {learner_rejected} flagged by the blind pass {sorted(flagged)}")
        questions = read_jsonl(deck.path("questions.jsonl"))
        progress = Progress(deck.path("progress.sqlite"))
        weak = WEAK_DOMAIN[sample]
        asked = 0
        for n in range(SESSIONS):
            day = START + timedelta(days=n)
            learner = ScriptedLearner(n, weak)
            outcomes = run_session(deck, questions, progress, day, learner)
            asked += len(outcomes)
            new = {}
            for o in outcomes:
                if not o.retry:
                    new[o.domain] = new.get(o.domain, 0) + 1
            out(f"session {n + 1} ({day.isoformat()}): {len(outcomes)} answers, {sum(1 for o in outcomes if o.correct)} right")
            _bars(deck, outcomes, out)
        acc = progress.domain_accuracy(deck.domain_names)
        out("accuracy by domain after 3 sessions (simulated learner, weak area: " + weak + "): " + ", ".join(f"{d} {acc[d]:.2f}" for d in deck.domain_names))
        progress.close()
        line = f"written: {res.written} | passed checks: {res.passed} | rejected: {res.rejected} | sessions: {SESSIONS} | model calls: 0 ($0.00)"
        out(line)
        return {"written": res.written, "passed": res.passed, "rejected": res.rejected, "sessions": SESSIONS, "answers": asked, "line": line}


def prepare_demo_deck(folder, sample="networking-basics"):
    """Copy a sample deck into `folder`, write from its synthetic replays and approve every question the blind pass did not flag."""
    if sample not in SAMPLES:
        raise ValueError(f"sample must be one of: {', '.join(SAMPLES)}")
    src = DATA / "decks" / sample
    root = Path(folder) / sample
    root.mkdir()
    shutil.copy(src / "deck.toml", root / "deck.toml")
    shutil.copytree(src / "sources", root / "sources")
    deck = load_deck(root)
    write_from_replay(deck, default_replay("write", sample), default_replay("blind", sample))
    review_pending(deck, lambda e: "r" if e["flags"] else "a")
    return deck
