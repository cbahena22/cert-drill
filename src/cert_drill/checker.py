"""Question checker: plain code that accepts or refuses a question, with a reason for every refusal."""
import re
from dataclasses import dataclass, field

from .model import DeckError

MC_FIELDS = {"id", "type", "domain", "stem", "options", "key", "explanation", "citations"}
FREE_FIELDS = {"id", "type", "domain", "stem", "explanation", "marking_points", "citations"}
CAP_STEM = 400
CAP_OPTION = 200
CAP_EXPLANATION = 800
CAP_QUOTE = 400
CAP_POINT = 300
MIN_QUOTE = 8
NEAR_DUPLICATE = 0.8
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")  # use fullmatch
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
VAGUE_OPTION_RE = re.compile(r"\b(?:all|none|both|neither)\s+of\s+(?:the\s+)?(?:above|these|them)\b", re.IGNORECASE)
QUOTE_MARKS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})


def normalise(text):
    """Quote marks straightened and whitespace collapsed, so a quote matches its source however it was wrapped."""
    return re.sub(r"\s+", " ", text.translate(QUOTE_MARKS)).strip()


def stem_tokens(text):
    return frozenset(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 1.0


def _text(value, what, cap, reasons):
    if not isinstance(value, str) or not value.strip():
        reasons.append(f"bad-field: {what} must be a non-empty string")
        return False
    if len(value) > cap:
        reasons.append(f"too-long: {what} is over {cap} characters")
        return False
    if CONTROL_RE.search(value):
        reasons.append(f"control-characters: {what}")
        return False
    return True


def _check_citation(c, deck, where, reasons):
    if not isinstance(c, dict) or set(c) != {"source", "quote"}:
        reasons.append(f"bad-citation: {where} must have exactly source and quote")
        return
    src, quote = c["source"], c["quote"]
    if not isinstance(src, str) or src not in deck.source_paths:
        reasons.append(f"citation-source-unknown: {where} names a file that is not a deck source")
        return
    if not _text(quote, f"{where} quote", CAP_QUOTE, reasons):
        return
    if len(normalise(quote)) < MIN_QUOTE:
        reasons.append(f"quote-too-short: {where} (under {MIN_QUOTE} characters)")
        return
    if normalise(quote) not in normalise(deck.source_text(src)):
        reasons.append(f"citation-quote-not-found: {where} quote is not in {src}")


def _check_citations(cites, deck, where, reasons, low=1, high=3):
    if not isinstance(cites, list) or not low <= len(cites) <= high:
        reasons.append(f"bad-citations: {where} needs {low} to {high} citations")
        return
    for i, c in enumerate(cites):
        _check_citation(c, deck, f"{where}[{i}]", reasons)


def check_question(q, deck, seen_stems=()):
    """Reasons a question is refused (empty list = it passes). `seen_stems` are token sets of stems already in the deck."""
    reasons = []
    if not isinstance(q, dict):
        return ["bad-record: a question must be a JSON object"]
    kind = q.get("type")
    if kind not in ("mc", "free"):
        return ["bad-type: type must be mc or free"]
    allowed = MC_FIELDS if kind == "mc" else FREE_FIELDS
    extra = sorted(str(k) for k in set(q) - allowed)
    missing = sorted(k for k in allowed - set(q) if not (kind == "free" and k == "citations"))
    if extra:
        reasons.append(f"unknown-field: {', '.join(repr(e) for e in extra)}")
    if missing:
        reasons.append(f"missing-field: {', '.join(missing)}")
        return reasons
    if not isinstance(q["id"], str) or not ID_RE.fullmatch(q["id"]):
        reasons.append("bad-id: id must be lowercase letters, digits and hyphens (at most 40)")
    if q["domain"] not in deck.domain_names:
        reasons.append("domain-unknown: domain is not in the deck")
    stem_ok = _text(q["stem"], "stem", CAP_STEM, reasons)
    _text(q["explanation"], "explanation", CAP_EXPLANATION, reasons)
    if stem_ok:
        toks = stem_tokens(q["stem"])
        if any(_jaccard(toks, s) >= NEAR_DUPLICATE for s in seen_stems):
            reasons.append("near-duplicate-stem: stem is almost the same as another question")
    if kind == "mc":
        _check_mc(q, deck, reasons)
    else:
        _check_free(q, deck, reasons)
    return reasons


def _check_mc(q, deck, reasons):
    opts = q["options"]
    if not isinstance(opts, list) or len(opts) != 4:
        reasons.append("options-count: a multiple-choice question has exactly 4 options")
    else:
        good = [_text(o, f"option {i}", CAP_OPTION, reasons) for i, o in enumerate(opts)]
        if all(good):
            if len({normalise(o).casefold() for o in opts}) != 4:
                reasons.append("options-not-distinct: two options are the same")
            if any(VAGUE_OPTION_RE.search(o) for o in opts):
                reasons.append("vague-option: no 'all of the above' or 'none of the above' style options")
    key = q["key"]
    if isinstance(key, bool) or not isinstance(key, int) or not 0 <= key <= 3:
        reasons.append("bad-key: key must be the index (0 to 3) of exactly one option")
    _check_citations(q["citations"], deck, "citations", reasons)


def _check_free(q, deck, reasons):
    pts = q["marking_points"]
    if not isinstance(pts, list) or not 2 <= len(pts) <= 5:
        reasons.append("points-count: a free-text question has 2 to 5 marking points")
        return
    for i, p in enumerate(pts):
        if not isinstance(p, dict) or set(p) != {"text", "citation"}:
            reasons.append(f"bad-point: marking point {i} must have exactly text and citation")
            continue
        _text(p["text"], f"marking point {i}", CAP_POINT, reasons)
        _check_citation(p["citation"], deck, f"marking point {i} citation", reasons)
    if "citations" in q:
        _check_citations(q["citations"], deck, "citations", reasons)


def check_batch(questions):
    """Checks over the whole set of multiple-choice questions (a key that is usually the longest option gives it away)."""
    mcs = [q for q in questions if isinstance(q, dict) and q.get("type") == "mc" and isinstance(q.get("options"), list)
           and len(q["options"]) == 4 and isinstance(q.get("key"), int) and not isinstance(q.get("key"), bool) and 0 <= q["key"] <= 3
           and all(isinstance(o, str) for o in q["options"])]
    reasons = []
    if len(mcs) >= 8:
        longest = 0
        for q in mcs:
            lens = [len(o) for o in q["options"]]
            if lens[q["key"]] == max(lens) and lens.count(max(lens)) == 1:
                longest += 1
        if longest / len(mcs) > 0.5:
            reasons.append(f"key-usually-longest: the key is the longest option in {longest} of {len(mcs)} questions")
    return reasons


@dataclass
class Report:
    per_question: dict = field(default_factory=dict)
    batch: list = field(default_factory=list)
    total: int = 0

    @property
    def ok(self):
        return not self.per_question and not self.batch


def check_deck(deck, questions):
    """Check a list of questions as a set: each one in order (so later duplicates are caught), then the batch rules."""
    rep = Report(total=len(questions))
    seen, ids = [], set()
    for n, q in enumerate(questions):
        reasons = check_question(q, deck, seen)
        qid = q.get("id") if isinstance(q, dict) else None
        if isinstance(qid, str):
            if qid in ids:
                reasons.append("duplicate-id: id is used twice")
            ids.add(qid)
        if reasons:
            rep.per_question[qid if isinstance(qid, str) else f"#{n + 1}"] = reasons
        elif isinstance(q.get("stem"), str):
            seen.append(stem_tokens(q["stem"]))
    rep.batch = check_batch(questions)
    return rep


__all__ = ["check_question", "check_batch", "check_deck", "normalise", "stem_tokens", "Report", "DeckError"]
