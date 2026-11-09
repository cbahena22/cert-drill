"""Writing and review: proposals (from a replay file) go through the checker; passes wait for the learner, refusals are kept with reasons."""
from dataclasses import dataclass

from .checker import check_question, stem_tokens
from .model import append_jsonl, read_jsonl, write_jsonl
from .replay import load_replay

FLAG_KEY = "check-this-key"


@dataclass(frozen=True)
class WriteResult:
    written: int
    passed: int
    rejected: int
    flagged: int
    synthetic: bool


def _known(deck):
    """Stem token sets and ids of every question already approved or waiting."""
    seen, ids = [], set()
    for rec in read_jsonl(deck.path("questions.jsonl")) + [e.get("q") for e in read_jsonl(deck.path("pending.jsonl")) if isinstance(e, dict)]:
        if isinstance(rec, dict) and isinstance(rec.get("stem"), str):
            seen.append(stem_tokens(rec["stem"]))
        if isinstance(rec, dict) and isinstance(rec.get("id"), str):
            ids.add(rec["id"])
    return seen, ids


def write_from_replay(deck, replay_path, blind_path=None):
    """Proposals from a replay file (and blind-pass answers from another) through `write_proposals`."""
    header, proposals = load_replay(replay_path)
    blind = {}
    if blind_path is not None:
        _, rows = load_replay(blind_path)
        blind = {r["id"]: r["answer"] for r in rows if isinstance(r, dict) and "id" in r}
    return write_proposals(deck, proposals, blind, bool(header.get("synthetic")))


def write_proposals(deck, proposals, blind, synthetic):
    """Run every proposed question through the checker. The blind pass (answers given without the key) flags a disagreeing key."""
    seen, ids = _known(deck)
    pending, rejected = [], []
    flagged = 0
    for p in proposals:
        reasons = check_question(p, deck, seen)
        qid = p.get("id") if isinstance(p, dict) else None
        if isinstance(qid, str) and qid in ids:
            reasons.append("duplicate-id: id already used in this deck")
        if reasons:
            rejected.append({"q": p if isinstance(p, dict) else str(p)[:200], "reasons": reasons})
            continue
        flags = []
        if p["type"] == "mc" and p["id"] in blind and blind[p["id"]] != p["key"]:
            flags.append(FLAG_KEY)
            flagged += 1
        pending.append({"q": p, "flags": flags})
        seen.append(stem_tokens(p["stem"]))
        ids.add(p["id"])
    append_jsonl(deck.path("pending.jsonl"), pending)
    if rejected:
        append_jsonl(deck.path("rejected.jsonl"), rejected)
    return WriteResult(len(proposals), len(pending), len(rejected), flagged, synthetic)


def review_pending(deck, decide):
    """The learner decides. `decide(entry)` returns 'a' (approve), 'r' (reject) or 's' (skip). Approved questions are checked again."""
    pending = read_jsonl(deck.path("pending.jsonl"))
    approved = read_jsonl(deck.path("questions.jsonl"))
    seen = [stem_tokens(q["stem"]) for q in approved]
    ids = {q["id"] for q in approved}
    keep, rejected, n_ok = [], [], 0
    for entry in pending:
        choice = decide(entry)
        if choice == "a":
            reasons = check_question(entry["q"], deck, seen)
            if not reasons and entry["q"]["id"] in ids:
                reasons = ["duplicate-id: id already approved"]
            if reasons:
                rejected.append({"q": entry["q"], "reasons": reasons})
            else:
                approved.append(entry["q"])
                ids.add(entry["q"]["id"])
                seen.append(stem_tokens(entry["q"]["stem"]))
                n_ok += 1
        elif choice == "r":
            rejected.append({"q": entry["q"], "reasons": ["rejected by the learner" + (" (key flagged by the blind pass)" if entry.get("flags") else "")]})
        else:
            keep.append(entry)
    write_jsonl(deck.path("questions.jsonl"), approved)
    write_jsonl(deck.path("pending.jsonl"), keep)
    if rejected:
        append_jsonl(deck.path("rejected.jsonl"), rejected)
    return n_ok, len(rejected), len(keep)
