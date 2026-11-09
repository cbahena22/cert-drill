"""Spend control, request hashing and record/replay for the optional real-model path. Standard library only at import time."""
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..model import DeckError, write_jsonl
from ..replay import load_replay

HARD_CAP_USD = 1.00
WRITER_MODEL = "claude-sonnet-5-5"
GRADER_MODEL = "claude-sonnet-5-5"
BLIND_MODEL = "claude-haiku-5-5"
PRICES = {"claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}  # USD per million tokens: input, output
CACHE_WRITE, CACHE_READ = 1.25, 0.10  # multiples of the input price
RECORD_LINE_CHARS = 1_000_000  # a whole writer reply (16k tokens, JSON-escaped) fits on one recorded line
RECORD_MAX_BYTES = 20_000_000
MODES = ("mock", "replay", "live")


class LiveError(RuntimeError):
    """The real-model path stopped: no key, no package, a refusal, a cut-off reply, or nothing recorded."""


class BudgetExceeded(LiveError):
    """A call would take the run over its hard cap."""


def request_hash(req):
    """Stable id of a request: the same deck, task and settings give the same hash on every machine."""
    return hashlib.sha256(json.dumps(req, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _price(model):
    if model not in PRICES:
        raise LiveError(f"no price known for model {model!r}; add it to PRICES before a real run")
    return PRICES[model]


def estimate_usd(req):
    """Upper-bound cost of one request: every input token billed as a cache write, the whole max_tokens as output."""
    p_in, p_out = _price(req["model"])
    text = json.dumps({"system": req.get("system"), "messages": req.get("messages"), "output_config": req.get("output_config")}, ensure_ascii=False)
    tokens_in = len(text.encode("utf-8"))  # never more tokens than UTF-8 bytes, whatever the script of the notes
    return (tokens_in * p_in * CACHE_WRITE + req["max_tokens"] * p_out) / 1_000_000


def actual_usd(model, usage):
    """Cost from the usage numbers the API returns."""
    p_in, p_out = _price(model)

    def n(name):
        v = getattr(usage, name, None) if not isinstance(usage, dict) else usage.get(name)
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

    return (n("input_tokens") * p_in + n("cache_creation_input_tokens") * p_in * CACHE_WRITE
            + n("cache_read_input_tokens") * p_in * CACHE_READ + n("output_tokens") * p_out) / 1_000_000


@dataclass
class Budget:
    cap: float = HARD_CAP_USD
    spent: float = 0.0
    calls: int = 0

    def __post_init__(self):
        if not 0 < self.cap <= HARD_CAP_USD:
            raise LiveError(f"the cap must be above $0 and at most ${HARD_CAP_USD:.2f}")

    def check(self, estimate):
        if self.spent + estimate > self.cap:
            raise BudgetExceeded(f"stopping: this call could take the run to ${self.spent + estimate:.4f}, over the ${self.cap:.2f} cap "
                                 f"(spent so far ${self.spent:.4f} in {self.calls} calls)")

    def add(self, cost):
        self.spent += cost
        self.calls += 1


@dataclass
class Recording:
    """Responses kept by request hash in a JSON Lines file whose header names the models and the date (a recorded replay)."""
    path: Path
    models: str
    day: str = field(default_factory=lambda: date.today().isoformat())
    rows: dict = field(default_factory=dict)

    @classmethod
    def open(cls, path, models):
        p = Path(path)
        if not p.exists():
            return cls(p, models)
        header, rows = load_replay(p, RECORD_MAX_BYTES, RECORD_LINE_CHARS)
        if not header.get("recorded"):
            raise DeckError(f"{p.name}: not a recorded replay")
        rec = cls(p, header["model"], header["date"])
        for r in rows:
            if isinstance(r, dict) and isinstance(r.get("request_hash"), str) and isinstance(r.get("text"), str):
                rec.rows[r["request_hash"]] = r
        return rec

    def get(self, h):
        row = self.rows.get(h)
        return row["text"] if row else None

    def put(self, h, model, text, usage):
        self.rows[h] = {"request_hash": h, "model": model, "text": text, "usage": usage}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = {"recorded": True, "model": self.models, "date": self.day}
        write_jsonl(self.path, [header] + list(self.rows.values()))


def make_client():
    """The real client. The key comes from the environment only and is never printed, stored or sent to the page."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise LiveError("set ANTHROPIC_API_KEY in the environment first (never in a file inside this folder)")
    try:
        import anthropic  # the optional `live` extra
    except ImportError:
        raise LiveError("the live extra is not installed: pip install --require-hashes -r requirements-live.lock") from None
    return anthropic.Anthropic(max_retries=0, timeout=600.0)  # no silent retries: every attempt is counted against the cap


def _usage_dict(usage):
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: v for k in keys if isinstance(v := getattr(usage, k, None), int)}


class Caller:
    """Sends requests in one of three modes.
    mock: answers come from `mock(request)`; no client is ever made.
    replay: answers come from a recording by request hash; no client is ever made; a miss is an error.
    live: a recorded answer is reused at $0; otherwise, only with go=True, the estimate is checked against the budget,
    the client is made (once) and the reply is recorded."""

    def __init__(self, mode, *, mock=None, recording=None, budget=None, client_factory=None, go=False):
        if mode not in MODES:
            raise LiveError(f"mode must be one of: {', '.join(MODES)}")
        if mode == "mock" and mock is None:
            raise LiveError("mock mode needs a mock function")
        if mode == "replay" and recording is None:
            raise LiveError("replay mode needs a recording")
        self.mode, self.mock, self.recording, self.go = mode, mock, recording, go
        self.budget = budget or Budget()
        self.client_factory = client_factory
        self._client = None
        self.sent = 0

    def call(self, req):
        h = request_hash(req)
        if self.mode == "mock":
            return self.mock(req)
        if self.recording is not None and (text := self.recording.get(h)) is not None:
            return text
        if self.mode == "replay":
            raise LiveError("this request is not in the recording; a real run needs --go")
        if not self.go:
            raise LiveError("estimate only: nothing is sent without --go")
        estimate = estimate_usd(req)
        self.budget.check(estimate)
        if self._client is None:
            self._client = (self.client_factory or make_client)()
        self.sent += 1
        try:
            resp = self._client.messages.create(**req)
        except Exception as e:  # noqa: BLE001 - any SDK or network error: count the worst case, stop with a short message
            self.budget.add(estimate)
            raise LiveError(f"the API call failed ({type(e).__name__}); ${estimate:.4f} counted against the cap as a worst case") from None
        self.budget.add(actual_usd(req["model"], resp.usage))
        if resp.stop_reason == "refusal":
            raise LiveError("the model declined this request; nothing was kept")
        if resp.stop_reason == "max_tokens":
            raise LiveError("the reply was cut off at max_tokens; nothing was kept")
        texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        if len(texts) != 1:
            raise LiveError("expected exactly one text block in the reply")
        if self.recording is not None:
            self.recording.put(h, req["model"], texts[0], _usage_dict(resp.usage))
        return texts[0]

    def call_json(self, req):
        text = self.call(req)
        try:
            data = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            raise LiveError("the reply is not JSON") from None
        if not isinstance(data, dict):
            raise LiveError("the reply is not a JSON object")
        return data

