"""Spaced repetition: SM-2 per question, a local progress file, and a session builder that favours the weakest domains."""
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from .grading import grade_from_score, standin_grade

NEW_PER_DAY = 10
SESSION_SIZE = 12
MAX_RETRIES = 2
RETRY_GAP = 3
ROLLING = 20
MC_RIGHT, MC_WRONG = 4, 1
PASS_GRADE = 3
MAX_INTERVAL = 365

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, qid TEXT NOT NULL, domain TEXT NOT NULL,
  day TEXT NOT NULL, grade INTEGER NOT NULL, kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS overrides (review_id INTEGER PRIMARY KEY, grade INTEGER NOT NULL, day TEXT NOT NULL);
"""


@dataclass(frozen=True)
class State:
    ef: float = 2.5
    reps: int = 0
    interval: int = 0
    due: date = date.min
    first_day: date = date.max


def sm2_update(state, grade, today):
    """One SM-2 step. A grade under 3 resets repetitions and brings the question back tomorrow."""
    if not isinstance(grade, int) or isinstance(grade, bool) or not 0 <= grade <= 5:
        raise ValueError("grade must be a whole number from 0 to 5")
    ef = max(1.3, state.ef + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    if grade < PASS_GRADE:
        reps, interval = 0, 1
    else:
        reps = state.reps + 1
        interval = 1 if reps == 1 else 6 if reps == 2 else min(MAX_INTERVAL, max(state.interval + 1, round(state.interval * ef)))
    return State(ef=ef, reps=reps, interval=interval, due=today + timedelta(days=interval), first_day=min(state.first_day, today))


class Progress:
    """Local progress in a SQLite file (or `:memory:`). Only scheduled reviews move the SM-2 state; retries inside a session do not."""

    def __init__(self, path, check_same_thread=True):
        self.db = sqlite3.connect(str(path), check_same_thread=check_same_thread)
        self.db.execute("PRAGMA trusted_schema=OFF")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def add_review(self, qid, domain, day, grade, kind="scheduled"):
        cur = self.db.execute("INSERT INTO reviews (qid, domain, day, grade, kind) VALUES (?, ?, ?, ?, ?)",
                              (qid, domain, day.isoformat(), int(grade), kind))
        self.db.commit()
        return cur.lastrowid

    def reviews(self):
        """Scheduled reviews in order, each with its effective grade (a dispute replaces the grade)."""
        rows = self.db.execute(
            "SELECT r.id, r.qid, r.domain, r.day, COALESCE(o.grade, r.grade), r.kind FROM reviews r "
            "LEFT JOIN overrides o ON o.review_id = r.id WHERE r.kind = 'scheduled' ORDER BY r.id").fetchall()
        return [(i, q, d, date.fromisoformat(day), g) for i, q, d, day, g, _ in rows]

    def dispute(self, qid, grade, day):
        """Override the grade of the latest scheduled review of a question. Returns False if it was never answered."""
        if isinstance(grade, bool) or not isinstance(grade, int) or not 0 <= grade <= 5:
            raise ValueError("grade must be a whole number from 0 to 5")
        row = self.db.execute("SELECT id FROM reviews WHERE qid = ? AND kind = 'scheduled' ORDER BY id DESC LIMIT 1", (qid,)).fetchone()
        if row is None:
            return False
        self.db.execute("INSERT OR REPLACE INTO overrides (review_id, grade, day) VALUES (?, ?, ?)", (row[0], grade, day.isoformat()))
        self.db.commit()
        return True

    def states(self):
        out = {}
        for _, qid, _, day, grade in self.reviews():
            out[qid] = sm2_update(out.get(qid, State()), grade, day)
        return out

    def domain_accuracy(self, domains):
        """Smoothed accuracy of the last ROLLING scheduled reviews per domain (no reviews = 0.5)."""
        acc = {}
        revs = self.reviews()
        for d in domains:
            last = [g for _, _, dom, _, g in revs if dom == d][-ROLLING:]
            acc[d] = (sum(1 for g in last if g >= PASS_GRADE) + 1) / (len(last) + 2)
        return acc

    def forget(self):
        self.db.executescript("DELETE FROM overrides; DELETE FROM reviews;")
        self.db.commit()


def build_session(deck, questions, progress, today, size=SESSION_SIZE, new_per_day=NEW_PER_DAY):
    """Question ids for one session: due questions (oldest due first), then new ones from the weakest domains
    (priority = (1 - rolling accuracy) x domain weight, spread so one domain does not take every slot)."""
    by_id = {q["id"]: q for q in questions}
    states = progress.states()
    due = sorted((s.due, qid) for qid, s in states.items() if qid in by_id and s.due <= today)
    picked = [qid for _, qid in due][:size]
    introduced_today = sum(1 for s in states.values() if s.first_day == today)
    room = min(size - len(picked), max(0, new_per_day - introduced_today))
    if room <= 0:
        return picked
    acc = progress.domain_accuracy(deck.domain_names)
    pools = {d: [q["id"] for q in questions if q["domain"] == d and q["id"] not in states] for d in deck.domain_names}
    taken = {d: 0 for d in deck.domain_names}
    for _ in range(room):
        open_domains = [d for d in deck.domain_names if pools[d]]
        if not open_domains:
            break
        best = max(open_domains, key=lambda d: ((1 - acc[d]) * deck.weight(d) / (1 + taken[d]), -deck.domain_names.index(d)))
        picked.append(pools[best].pop(0))
        taken[best] += 1
    return picked


@dataclass(frozen=True)
class Answer:
    """What the learner gave: a choice index for multiple choice, or text (graded) or a self-grade for free text."""
    choice: int | None = None
    text: str | None = None
    self_grade: int | None = None


@dataclass(frozen=True)
class Outcome:
    qid: str
    domain: str
    grade: int
    correct: bool
    retry: bool
    detail: str = ""


def grade_answer(q, answer, grader=standin_grade):
    """(grade, detail) for one answer. Multiple choice is exact; free text is graded by code from the grader's reply."""
    if q["type"] == "mc":
        return (MC_RIGHT, "") if answer.choice == q["key"] else (MC_WRONG, "")
    if answer.self_grade is not None:
        return answer.self_grade, "self-graded"
    res = grader(q, answer.text or "")
    kept = sum(1 for p in res.points if p.final != "missed")
    return res.grade, f"score {res.score:.2f}; {kept} of {len(res.points)} marking points kept"


class SessionRunner:
    """One session as a step-by-step object (the terminal loop and the local web page both drive it).
    A missed question returns a few questions later (up to MAX_RETRIES) and is due again tomorrow."""

    def __init__(self, deck, questions, progress, today, size=SESSION_SIZE, new_per_day=NEW_PER_DAY, grader=standin_grade):
        self.by_id = {q["id"]: q for q in questions}
        self.progress, self.today, self.grader = progress, today, grader
        self.queue = [(qid, 0) for qid in build_session(deck, questions, progress, today, size, new_per_day)]
        self.pos = 0
        self.outcomes = []

    def current(self):
        """(question, is_retry) to ask next, or None when the session is over."""
        if self.pos >= len(self.queue):
            return None
        qid, tries = self.queue[self.pos]
        return self.by_id[qid], tries > 0

    @property
    def remaining(self):
        return len(self.queue) - self.pos

    def answer(self, answer):
        """Grade the answer to the current question, record it and move on. Returns the Outcome."""
        qid, tries = self.queue[self.pos]
        self.pos += 1
        q = self.by_id[qid]
        grade, detail = grade_answer(q, answer, self.grader)
        correct = grade >= PASS_GRADE
        self.progress.add_review(qid, q["domain"], self.today, grade, "retry" if tries else "scheduled")
        outcome = Outcome(qid, q["domain"], grade, correct, tries > 0, detail)
        self.outcomes.append(outcome)
        if not correct and tries < MAX_RETRIES:
            self.queue.insert(min(len(self.queue), self.pos + RETRY_GAP), (qid, tries + 1))
        return outcome


def run_session(deck, questions, progress, today, ui, size=SESSION_SIZE, new_per_day=NEW_PER_DAY, grader=standin_grade):
    """Run one session. `ui.ask(question, retry)` returns an Answer (or None to stop); `ui.show(question, outcome)` gives feedback."""
    runner = SessionRunner(deck, questions, progress, today, size, new_per_day, grader)
    while (cur := runner.current()) is not None:
        q, retry = cur
        answer = ui.ask(q, retry)
        if answer is None:
            break
        ui.show(q, runner.answer(answer))
    return runner.outcomes


__all__ = ["State", "Progress", "sm2_update", "build_session", "run_session", "SessionRunner", "Answer", "Outcome", "grade_answer", "grade_from_score"]
