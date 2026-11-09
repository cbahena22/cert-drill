"""Free-text grading: a grader proposes a verdict and a quote per marking point; code keeps only what it can verify and computes the number."""
import re
from dataclasses import dataclass

from .checker import normalise

VERDICTS = ("met", "partly", "missed")
VALUE = {"met": 1.0, "partly": 0.5, "missed": 0.0}
MIN_QUOTE_CHARS = 12
MIN_QUOTE_WORDS = 3
MAX_ANSWER_CHARS = 4000
STOPWORDS = frozenset(
    "about above after again also another because been before being between both could does doing down each from have having here "
    "into itself just like more most much must only other over same should some such than that their them then there these they "
    "this those through under until very were what when where which while will with would your".split()
)
WORD_RE = re.compile(r"[a-z]+")
MIN_SHARED_WORDS = 2
MARKING_VERB_RE = re.compile(r"^\s*(?:states|says|names|explains|mentions|notes|gives|describes)\b(?:\s+that\b)?", re.IGNORECASE)
SENTENCE_RE = re.compile(r"[^.!?;]+")
CLAUSE_RE = re.compile(r",|\b(?:and|but|while|whereas|because)\b", re.IGNORECASE)
MAX_CLAUSES = 12


def content_words(text):
    """Lowercased words of 4+ letters, stopwords dropped, a plural 's' folded away."""
    out = set()
    for w in WORD_RE.findall(text.lower()):
        if len(w) >= 4 and w not in STOPWORDS:
            out.add(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


@dataclass(frozen=True)
class PointResult:
    claimed: str
    final: str
    quote: str
    note: str


@dataclass(frozen=True)
class GradeResult:
    score: float
    grade: int
    points: tuple


def grade_from_score(score):
    """Map the 0-1 score to the SM-2 grade (0-5)."""
    for floor, g in ((0.9, 5), (0.7, 4), (0.5, 3), (0.3, 2), (0.1, 1)):
        if score >= floor:
            return g
    return 0


def point_words(text):
    """Content words of a marking point, without its leading marking verb ("States that", "Says", "Names")."""
    return content_words(MARKING_VERB_RE.sub("", text, count=1))


def shared_needed(point_cw):
    """How many content words a quote must share with a marking point: two, or all of them if the point has fewer (never zero)."""
    return max(1, min(MIN_SHARED_WORDS, len(point_cw)))


def _place(quote, answer_norm, used):
    """Span of the first occurrence of `quote` in the answer that overlaps no span already kept for another point."""
    start = answer_norm.find(quote)
    while start != -1:
        end = start + len(quote)
        if all(end <= a or start >= b for a, b in used):
            return start, end
        start = answer_norm.find(quote, start + 1)
    return None


def _verify_point(point, entry, answer_norm, used, used_words):
    verdict = entry.get("verdict") if isinstance(entry, dict) else None
    quote = entry.get("quote") if isinstance(entry, dict) else None
    if verdict not in VERDICTS:
        return PointResult(str(verdict)[:20], "missed", "", "unknown verdict")
    if verdict == "missed":
        return PointResult("missed", "missed", "", "")
    if not isinstance(quote, str):
        return PointResult(verdict, "missed", "", "no quote from the answer")
    q = normalise(quote)
    if q not in answer_norm:
        return PointResult(verdict, "missed", q[:80], "quote is not in the answer")
    if len(q) < MIN_QUOTE_CHARS or len(q.split()) < MIN_QUOTE_WORDS:
        return PointResult(verdict, "missed", q, f"quote under {MIN_QUOTE_CHARS} characters or {MIN_QUOTE_WORDS} words")
    want = point_words(point["text"])
    need = shared_needed(want)
    if len(content_words(q) & want) < need:
        return PointResult(verdict, "missed", q, f"quote shares fewer than {need} content word(s) with the marking point")
    span = _place(q, answer_norm, used)
    words = frozenset(content_words(q))
    if span is None or any(words == w for w in used_words):
        return PointResult(verdict, "missed", q, "quote overlaps or repeats text already used for another marking point")
    used.append(span)
    used_words.append(words)
    return PointResult(verdict, verdict, q, "")


def grade_with_reply(question, answer, reply):
    """Score a free-text answer from a grader reply `{"points": [{"point": i, "verdict": ..., "quote": ...}]}`.
    The reply is data: any number it carries is ignored, and a point counts only if its quote passes the checks above.
    Points are checked in order; one stretch of the answer can earn at most one point."""
    if not isinstance(answer, str):
        raise TypeError("answer must be text")
    answer_norm = normalise(answer[:MAX_ANSWER_CHARS])
    entries = {}
    raw = reply.get("points") if isinstance(reply, dict) else None
    for e in raw if isinstance(raw, list) else []:
        idx = e.get("point") if isinstance(e, dict) else None
        if isinstance(idx, int) and not isinstance(idx, bool) and idx not in entries:
            entries[idx] = e
    used, used_words = [], []
    results = tuple(_verify_point(p, entries.get(i), answer_norm, used, used_words) if i in entries else PointResult("missed", "missed", "", "not graded")
                    for i, p in enumerate(question["marking_points"]))
    score = sum(VALUE[r.final] for r in results) / len(results)
    return GradeResult(round(score, 4), grade_from_score(score), results)


def _strip_span(text, a, b):
    seg = text[a:b]
    return a + (len(seg) - len(seg.lstrip(" ,"))), b - (len(seg) - len(seg.rstrip(" ,")))


def _candidates(norm):
    """Stretches of the answer the stand-in may quote: each sentence and each run of its clauses, long enough to keep."""
    out = set()
    for m in SENTENCE_RE.finditer(norm):
        s0, s1 = m.span()
        pieces, prev = [], s0
        for sep in CLAUSE_RE.finditer(norm, s0, s1):
            pieces.append((prev, sep.start()))
            prev = sep.end()
        pieces.append((prev, s1))
        pieces = pieces[:MAX_CLAUSES]
        for i in range(len(pieces)):
            for j in range(i, len(pieces)):
                a, b = _strip_span(norm, pieces[i][0], pieces[j][1] if j < len(pieces) - 1 else s1)
                seg = norm[a:b]
                if len(seg) >= MIN_QUOTE_CHARS and len(seg.split()) >= MIN_QUOTE_WORDS:
                    out.add((a, b))
    return sorted(out)


def standin_reply(question, answer):
    """The labelled stand-in grader: keyword overlap between stretches of the answer and each marking point. Weaker than a model.
    It quotes the best-overlapping stretch not already quoted for an earlier point (shortest on a tie)."""
    norm = normalise(answer[:MAX_ANSWER_CHARS])
    cands = [(a, b, content_words(norm[a:b])) for a, b in _candidates(norm)]
    used, points = [], []
    for i, p in enumerate(question["marking_points"]):
        want = point_words(p["text"])
        best, share = None, 0.0
        for a, b, words in cands:
            if not all(b <= x or a >= y for x, y in used):
                continue
            got = len(words & want) / len(want) if want else 0.0
            if got > share or (got == share and best is not None and got > 0 and b - a < best[1] - best[0]):
                best, share = (a, b), got
        verdict = "met" if share >= 0.5 else "partly" if share >= 0.25 else "missed"
        if verdict == "missed" or best is None:
            points.append({"point": i, "verdict": "missed", "quote": ""})
            continue
        used.append(best)
        points.append({"point": i, "verdict": verdict, "quote": norm[best[0]:best[1]]})
    return {"points": points}


def standin_grade(question, answer):
    return grade_with_reply(question, answer, standin_reply(question, answer))


def self_grade(value):
    """The learner grades their own free-text answer 0 to 5."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 5:
        raise ValueError("self-grade must be a whole number from 0 to 5")
    return value
