"""`drill eval`: how well the plain-code checks and the stand-in grader do on hand-written sets. No model call, $0.
Every row says what produced its number: "code", "stand-in" or "synthetic replay (hand-written)"."""
import copy
import json
from pathlib import Path

from .checker import check_batch, check_deck, check_question, stem_tokens
from .grading import VALUE, grade_from_score, grade_with_reply, standin_grade
from .model import DeckError, load_deck, read_jsonl, write_text_atomic
from .replay import DATA, REPLAYS, load_replay

EVALS = DATA / "evals"
DECKS = ("networking-basics", "us-constitution")
LONGEST_SUFFIX = " - this is the one the notes give"


def _decks():
    out = {}
    for name in DECKS:
        deck = load_deck(DATA / "decks" / name)
        out[name] = (deck, read_jsonl(deck.path("questions.jsonl")))
    return out


def _code(reason):
    return reason.split(":", 1)[0]


def _planted(row, deck, questions):
    by_id = {q["id"]: q for q in questions}
    if row.get("batch"):
        mcs = copy.deepcopy([q for q in questions if q["type"] == "mc"][:row["take"]])
        for q in mcs:
            while len(q["options"][q["key"]]) <= max(len(o) for i, o in enumerate(q["options"]) if i != q["key"]):
                q["options"][q["key"]] += LONGEST_SUFFIX
        return check_batch(mcs)
    if row["base"] not in by_id:
        raise DeckError(f"planted fault {row['case']}: no base question {row['base']}")
    q = copy.deepcopy(by_id[row["base"]])
    q.update(copy.deepcopy(row["set"]))
    for k in row["drop"]:
        q.pop(k, None)
    seen = [stem_tokens(x["stem"]) for x in questions if x["id"] != row["base"]]
    return check_question(q, deck, seen)


def eval_checker(decks):
    deck, questions = decks["networking-basics"]
    rows = read_jsonl(EVALS / "planted-faults.jsonl")
    caught, misses, checkable, checkable_caught = 0, [], 0, 0
    for r in rows:
        reasons = _planted(r, deck, questions)
        hit = r["fault"] in {_code(x) for x in reasons}
        caught += hit
        if r["caught_by"] == "code":
            checkable += 1
            checkable_caught += hit
        if not hit:
            misses.append({"case": r["case"], "fault": r["fault"], "caught_by": r["caught_by"], "note": r.get("note", ""),
                           "code_said": [_code(x) for x in reasons] or ["passed"]})
    return {"name": "checker-planted-faults", "label": "code: the question checker on hand-written planted faults (one fault per question)",
            "n": len(rows), "caught": caught, "result": f"{caught}/{len(rows)} caught", "code_checkable_n": checkable,
            "code_checkable_caught": checkable_caught, "misses": misses}


def eval_clean(decks):
    n = refused = batch = 0
    for _, (deck, questions) in sorted(decks.items()):
        rep = check_deck(deck, questions)
        n += rep.total
        refused += len(rep.per_question)
        batch += len(rep.batch)
    return {"name": "checker-sample-questions", "label": "code: the question checker on the shipped sample questions (should refuse none)",
            "n": n, "refused": refused, "batch_problems": batch, "result": f"{refused}/{n} refused, {batch} batch problems"}


def eval_standin(decks):
    rows = read_jsonl(EVALS / "expected-grades.jsonl")
    exact = within = point_agree = point_n = over = under = 0
    abs_err = 0
    disagreements = []
    for r in rows:
        deck, questions = decks[r["deck"]]
        q = next(x for x in questions if x["id"] == r["question"])
        want = r["expected_points"]
        if len(want) != len(q["marking_points"]):
            raise DeckError(f"expected grade {r['case']}: {len(want)} verdicts for {len(q['marking_points'])} marking points")
        expected = grade_from_score(sum(VALUE[v] for v in want) / len(want))
        res = standin_grade(q, r["answer"])
        got = [p.final for p in res.points]
        exact += res.grade == expected
        within += abs(res.grade - expected) <= 1
        abs_err += abs(res.grade - expected)
        over += res.grade > expected
        under += res.grade < expected
        point_agree += sum(a == b for a, b in zip(got, want))
        point_n += len(want)
        if res.grade != expected:
            disagreements.append({"case": r["case"], "expected_grade": expected, "stand_in_grade": res.grade,
                                  "expected_points": want, "stand_in_points": got, "note": r["note"]})
    n = len(rows)
    return {"name": "standin-vs-expected-grades", "label": "stand-in grader (keyword overlap, not a model) vs hand-written expected grades",
            "n": n, "exact": exact, "within_one": within, "mean_abs_error": round(abs_err / n, 3), "over_credit": over, "under_credit": under,
            "points_agree": point_agree, "points_n": point_n,
            "result": f"{exact}/{n} exact, {within}/{n} within one grade; {point_agree}/{point_n} marking points agree",
            "disagreements": disagreements}


def eval_red_team(decks):
    n = matches = 0
    mismatches = []
    for name in DECKS:
        _, questions = decks[name]
        by_id = {q["id"]: q for q in questions}
        _, rows = load_replay(REPLAYS / f"grader-{name}.jsonl")
        for r in rows:
            n += 1
            score = grade_with_reply(by_id[r["question"]], r["answer"], r["reply"]).score
            if abs(score - r["expected_score"]) <= 1e-4:
                matches += 1
            else:
                mismatches.append({"case": r["case"], "expected_score": r["expected_score"], "code_score": score})
    return {"name": "grader-red-team", "label": "code score on synthetic replays (hand-written) of a grader red-team set vs the expected score",
            "n": n, "matches": matches, "result": f"{matches}/{n} as expected", "mismatches": mismatches}


def run_eval():
    decks = _decks()
    return {"tool": "drill eval", "eval_version": 1, "model_calls": 0, "cost_usd": 0.0,
            "rows": [eval_checker(decks), eval_clean(decks), eval_standin(decks), eval_red_team(decks)]}


def dumps(result):
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def table(result):
    lines = [f"{'row':<28} {'n':>3}  {'result':<64} label", "-" * 140]
    for r in result["rows"]:
        lines.append(f"{r['name']:<28} {r['n']:>3}  {r['result']:<64} {r['label']}")
    checker = result["rows"][0]
    for m in checker["misses"]:
        lines.append(f"  checker miss: {m['case']} ({m['fault']}) -> left to: {m['caught_by']}; code said: {', '.join(m['code_said'])}")
    lines.append(f"model calls: {result['model_calls']} (${result['cost_usd']:.2f})")
    return "\n".join(lines)


def write_eval(out_dir):
    result = run_eval()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_text_atomic(out / "eval.json", dumps(result))  # a fresh file replaced into place: never follows a planted link
    return result, out / "eval.json"
