from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cert_drill.scheduler import Answer, Progress, State, build_session, grade_answer, run_session, sm2_update

D0 = date(2026, 3, 1)
grades = st.integers(min_value=0, max_value=5)


@given(st.lists(grades, min_size=1, max_size=40))
def test_sm2_invariants(seq):
    s, day = State(), D0
    for g in seq:
        before = s
        s = sm2_update(s, g, day)
        assert s.interval >= 1 and s.ef >= 1.3
        assert s.due == day + timedelta(days=s.interval)
        if g < 3:
            assert s.reps == 0 and s.interval == 1
        else:
            assert s.reps == before.reps + 1
        day = s.due


@given(st.lists(st.integers(min_value=4, max_value=5), min_size=3, max_size=25))
def test_good_answers_never_shrink_the_interval(seq):
    s, last = State(), 0
    for g in seq:
        s = sm2_update(s, g, D0)
        assert s.interval >= last
        last = s.interval


@given(st.lists(grades, max_size=15), st.integers(min_value=0, max_value=2))
def test_a_miss_always_resets_to_tomorrow(seq, miss):
    s = State()
    for g in seq:
        s = sm2_update(s, g, D0)
    s = sm2_update(s, miss, D0)
    assert s.due == D0 + timedelta(days=1) and s.reps == 0


def test_first_intervals_are_one_then_six_days():
    s = sm2_update(State(), 4, D0)
    assert s.interval == 1
    s = sm2_update(s, 4, D0)
    assert s.interval == 6
    assert sm2_update(s, 5, D0).interval > 6


@pytest.mark.parametrize("bad", [-1, 6, True, 2.5, "3"])
def test_sm2_refuses_a_bad_grade(bad):
    with pytest.raises(ValueError):
        sm2_update(State(), bad, D0)


class Scripted:
    def __init__(self, right=True, stop_after=None):
        self.right, self.stop_after, self.asked, self.shown = right, stop_after, 0, []

    def ask(self, q, retry):
        if self.stop_after is not None and self.asked >= self.stop_after:
            return None
        self.asked += 1
        if q["type"] == "mc":
            return Answer(choice=q["key"] if self.right else (q["key"] + 1) % 4)
        return Answer(self_grade=5 if self.right else 0)

    def show(self, q, outcome):
        self.shown.append(outcome)


@pytest.fixture
def progress():
    p = Progress(":memory:")
    yield p
    p.close()


def test_new_questions_are_capped_per_day(net_deck, net_questions, progress):
    ids = build_session(net_deck, net_questions, progress, D0, size=50, new_per_day=10)
    assert len(ids) == 10 and len(set(ids)) == 10
    run_session(net_deck, net_questions, progress, D0, Scripted(), size=50, new_per_day=10)
    assert build_session(net_deck, net_questions, progress, D0, size=50, new_per_day=10) == []  # nothing due today, cap used
    assert len(build_session(net_deck, net_questions, progress, D0 + timedelta(days=1), size=50, new_per_day=10)) >= 10


def test_due_questions_come_before_new_ones(net_deck, net_questions, progress):
    run_session(net_deck, net_questions, progress, D0, Scripted(right=False), size=6, new_per_day=6)
    ids = build_session(net_deck, net_questions, progress, D0 + timedelta(days=1), size=12, new_per_day=6)
    first = {r[1] for r in progress.reviews()}
    assert set(ids[:len(first)]) == first and len(ids) == len(first) + 6


def test_with_equal_history_the_heavier_domains_get_more_new_slots(net_deck, net_questions, progress):
    new = build_session(net_deck, net_questions, progress, D0, size=10, new_per_day=10)
    counts = {pre: sum(1 for i in new if i.startswith(pre)) for pre in ("addr", "prot", "sec", "trbl")}
    assert counts["addr"] + counts["prot"] > counts["sec"] + counts["trbl"]


def test_a_weak_domain_wins_the_next_new_slots_over_a_strong_one(net_deck, net_questions, progress):
    sec = [q for q in net_questions if q["domain"] == "Security"][:6]
    addr = [q for q in net_questions if q["domain"] == "Addressing"][:6]
    for q in sec:
        progress.add_review(q["id"], q["domain"], D0, 0)
    for q in addr:
        progress.add_review(q["id"], q["domain"], D0, 5)
    ids = build_session(net_deck, net_questions, progress, D0 + timedelta(days=1), size=30, new_per_day=30)
    new = [i for i in ids if i not in {q["id"] for q in sec + addr}]
    first_four = [next(q["domain"] for q in net_questions if q["id"] == i) for i in new[:4]]
    assert first_four.count("Security") >= 2 and first_four.count("Addressing") == 0


def test_a_missed_question_returns_later_in_the_same_session_and_tomorrow(net_deck, net_questions, progress):
    ui = Scripted(right=False)
    out = run_session(net_deck, net_questions, progress, D0, ui, size=5, new_per_day=5)
    first = out[0].qid
    assert [o.qid for o in out].count(first) == 3  # asked once, then two retries
    assert [o.retry for o in out if o.qid == first] == [False, True, True]
    state = progress.states()[first]
    assert state.due == D0 + timedelta(days=1)
    assert first in build_session(net_deck, net_questions, progress, D0 + timedelta(days=1))


def test_retries_do_not_change_the_schedule_state(net_deck, net_questions, progress):
    run_session(net_deck, net_questions, progress, D0, Scripted(right=False), size=3, new_per_day=3)
    assert all(s.reps == 0 and s.interval == 1 for s in progress.states().values())
    assert all(r[1] for r in progress.reviews())


def test_stopping_ends_the_session_without_error(net_deck, net_questions, progress):
    out = run_session(net_deck, net_questions, progress, D0, Scripted(stop_after=2), size=5, new_per_day=5)
    assert len(out) == 2


def test_dispute_replaces_the_grade_and_the_state_is_recomputed(net_deck, net_questions, progress):
    run_session(net_deck, net_questions, progress, D0, Scripted(right=False), size=1, new_per_day=1)
    qid = progress.reviews()[0][1]
    assert progress.states()[qid].reps == 0
    assert progress.dispute(qid, 5, D0) is True
    assert progress.states()[qid].reps == 1 and progress.reviews()[0][4] == 5
    assert progress.dispute("never-asked", 5, D0) is False
    with pytest.raises(ValueError):
        progress.dispute(qid, 9, D0)


def test_progress_persists_in_a_file_and_forget_clears_it(tmp_path):
    f = tmp_path / "progress.sqlite"
    p = Progress(f)
    p.add_review("a-1", "Dom", D0, 4)
    p.close()
    p = Progress(f)
    assert len(p.reviews()) == 1
    p.forget()
    assert p.reviews() == []
    p.close()


def test_domain_accuracy_is_smoothed(progress):
    assert progress.domain_accuracy(["X"]) == {"X": 0.5}
    for i in range(4):
        progress.add_review(f"q{i}", "X", D0, 4)
    assert progress.domain_accuracy(["X"])["X"] == pytest.approx(5 / 6)


def test_grade_answer_for_each_question_type(net_questions):
    mc = next(q for q in net_questions if q["type"] == "mc")
    fr = next(q for q in net_questions if q["type"] == "free")
    assert grade_answer(mc, Answer(choice=mc["key"]))[0] == 4
    assert grade_answer(mc, Answer(choice=(mc["key"] + 1) % 4))[0] == 1
    assert grade_answer(fr, Answer(self_grade=2)) == (2, "self-graded")
    grade, detail = grade_answer(fr, Answer(text="I have no idea"))
    assert grade == 0 and "marking points kept" in detail


@settings(max_examples=25, deadline=None)
@given(st.lists(grades, min_size=1, max_size=8))
def test_state_replays_from_the_log(seq):
    p = Progress(":memory:")
    s = State()
    for i, g in enumerate(seq):
        p.add_review("q", "X", D0 + timedelta(days=i), g)
        s = sm2_update(s, g, D0 + timedelta(days=i))
    assert p.states()["q"] == s
    p.close()
