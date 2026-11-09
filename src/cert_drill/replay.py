"""Replay layer: model outputs come from files whose first line says where they came from. Hand-written files carry a `synthetic` header."""
import hashlib
import json
from pathlib import Path

from .checker import normalise
from .model import DeckError, read_jsonl

DATA = Path(__file__).resolve().parent / "data"
REPLAYS = DATA / "replays"
SYNTHETIC_NOTE = "hand-written, not model output"
MAX_REPLAY_BYTES = 2_000_000


def check_header(h):
    """True for a synthetic header, or a recorded header that names its model and date."""
    if not isinstance(h, dict):
        return False
    if h.get("synthetic") is True:
        return h.get("note") == SYNTHETIC_NOTE and set(h) == {"synthetic", "note"}
    return h.get("recorded") is True and isinstance(h.get("model"), str) and isinstance(h.get("date"), str) and set(h) == {"recorded", "model", "date"}


def load_replay(path, max_bytes=MAX_REPLAY_BYTES, max_line_chars=None):
    """Records after the header. A file without a valid header is refused."""
    p = Path(path)
    if not p.is_file() or p.stat().st_size > max_bytes:
        raise DeckError(f"replay file missing or too large: {p.name}")
    rows = read_jsonl(p) if max_line_chars is None else read_jsonl(p, max_line_chars)
    if not rows or not check_header(rows[0]):
        raise DeckError(f"{p.name}: first line must be a replay header (synthetic or recorded)")
    return rows[0], rows[1:]


def is_synthetic(header):
    return bool(header.get("synthetic"))


def default_replay(kind, deck_name):
    """The shipped replay of one kind (write, blind, grader) for a deck of that name."""
    return REPLAYS / f"{kind}-{deck_name}.jsonl"


def answer_key(question_id, answer):
    return hashlib.sha256(f"{question_id}\n{normalise(answer)}".encode("utf-8")).hexdigest()


class ReplayGrader:
    """Looks up a recorded grader reply by question id and answer text; no match is an error, never a guess."""

    def __init__(self, path):
        self.header, rows = load_replay(path)
        self.replies = {answer_key(r["question"], r["answer"]): r["reply"] for r in rows}

    def reply(self, question, answer):
        try:
            return self.replies[answer_key(question["id"], answer)]
        except KeyError:
            raise DeckError(f"no replayed grader reply for question {question['id']}") from None


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
