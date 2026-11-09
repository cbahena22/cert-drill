"""Deck model: loading a deck folder, path rules and small JSON Lines helpers. Standard library only."""
import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

LICENSES = ("self-written", "public-domain", "cc-by", "own-notes-private")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")  # use fullmatch
MAX_SOURCE_BYTES = 1_000_000
MAX_DOMAINS = 12
MAX_SOURCES = 20
MAX_JSONL_LINES = 5000
MAX_JSONL_LINE_CHARS = 50_000
MAX_DECK_TOML_BYTES = 50_000
MAX_JSONL_BYTES = 20_000_000
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class DeckError(ValueError):
    """A deck, a path or a data file broke a rule."""


def safe_join(root, rel):
    """Resolve `rel` under `root`; refuse absolute paths, `..`, drive letters, backslashes and anything that resolves outside."""
    if not isinstance(rel, str) or not rel or "\0" in rel or "\\" in rel or ":" in rel:
        raise DeckError(f"bad path: {rel!r}")
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts:
        raise DeckError(f"path must stay inside the deck folder: {rel!r}")
    base = Path(root).resolve()
    full = (base / Path(*p.parts)).resolve()
    try:
        full.relative_to(base)
    except ValueError:
        raise DeckError(f"path must stay inside the deck folder: {rel!r}") from None
    return full


@dataclass(frozen=True)
class Domain:
    name: str
    weight: float


@dataclass(frozen=True)
class Source:
    path: str
    license: str


@dataclass
class Deck:
    root: Path
    name: str
    domains: tuple
    sources: tuple
    _text: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def domain_names(self):
        return [d.name for d in self.domains]

    @property
    def source_paths(self):
        return [s.path for s in self.sources]

    def weight(self, domain):
        total = sum(d.weight for d in self.domains)
        return next((d.weight for d in self.domains if d.name == domain), 0.0) / total

    def source_text(self, rel):
        """Text of a declared source file (read once, size-capped)."""
        if rel not in self.source_paths:
            raise DeckError(f"not a source of this deck: {rel!r}")
        if rel not in self._text:
            full = safe_join(self.root, rel)
            if not full.is_file() or full.stat().st_size > MAX_SOURCE_BYTES:
                raise DeckError(f"source missing or too large: {rel!r}")
            self._text[rel] = full.read_text(encoding="utf-8")
        return self._text[rel]

    def path(self, filename):
        """A file inside the deck folder (questions.jsonl, pending.jsonl, rejected.jsonl, progress.sqlite)."""
        return safe_join(self.root, filename)


def _str(v, what, cap):
    if not isinstance(v, str) or not v.strip() or len(v) > cap or CONTROL_RE.search(v):
        raise DeckError(f"{what} must be a non-empty string of at most {cap} characters")
    return v


def load_deck(folder):
    """Read `deck.toml` in `folder` and validate it. Every source needs a `source_license`."""
    root = Path(folder)
    toml_path = safe_join(root, "deck.toml")
    if not toml_path.is_file() or toml_path.stat().st_size > MAX_DECK_TOML_BYTES:
        raise DeckError(f"no readable deck.toml in {folder}")
    try:
        meta = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise DeckError(f"deck.toml is not valid TOML: {e}") from None
    name = _str(meta.get("name"), "name", 40)
    if not NAME_RE.fullmatch(name):
        raise DeckError("name must be lowercase letters, digits and hyphens (at most 40)")
    raw_domains = meta.get("domains")
    if not isinstance(raw_domains, list) or not 1 <= len(raw_domains) <= MAX_DOMAINS:
        raise DeckError(f"domains must be a list of 1 to {MAX_DOMAINS} tables")
    domains = []
    for d in raw_domains:
        if not isinstance(d, dict):
            raise DeckError("each domain must be a table")
        w = d.get("weight")
        if isinstance(w, bool) or not isinstance(w, (int, float)) or not 0 < w <= 100:
            raise DeckError("domain weight must be a number above 0 and at most 100")
        domains.append(Domain(_str(d.get("name"), "domain name", 60), float(w)))
    if len({d.name for d in domains}) != len(domains):
        raise DeckError("domain names must be unique")
    raw_sources = meta.get("sources")
    if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= MAX_SOURCES:
        raise DeckError(f"sources must be a list of 1 to {MAX_SOURCES} tables")
    sources = []
    for s in raw_sources:
        if not isinstance(s, dict):
            raise DeckError("each source must be a table")
        rel = _str(s.get("path"), "source path", 200)
        lic = s.get("source_license")
        if lic is None:
            raise DeckError(f"source {rel!r} has no source_license; set one of: {', '.join(LICENSES)}")
        if lic not in LICENSES:
            raise DeckError(f"source {rel!r}: source_license must be one of: {', '.join(LICENSES)}")
        full = safe_join(root, rel)
        if not full.is_file() or full.stat().st_size > MAX_SOURCE_BYTES:
            raise DeckError(f"source file missing or too large: {rel!r}")
        sources.append(Source(rel, lic))
    if len({s.path for s in sources}) != len(sources):
        raise DeckError("source paths must be unique")
    return Deck(root=root, name=name, domains=tuple(domains), sources=tuple(sources))


def read_jsonl(path, max_line_chars=MAX_JSONL_LINE_CHARS):
    """Records of a JSON Lines file (a missing file means no records). Capped in lines and line length."""
    p = Path(path)
    if not p.exists():
        return []
    if p.stat().st_size > MAX_JSONL_BYTES:
        raise DeckError(f"{p.name}: file too large")
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if n > MAX_JSONL_LINES:
                raise DeckError(f"{p.name}: more than {MAX_JSONL_LINES} lines")
            line = line.strip()
            if not line:
                continue
            if len(line) > max_line_chars:
                raise DeckError(f"{p.name}:{n}: line too long")
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                raise DeckError(f"{p.name}:{n}: not valid JSON") from None
    return out


def write_jsonl(path, records):
    """Replace a JSON Lines file atomically."""
    write_text_atomic(path, "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records))


def write_text_atomic(path, text):
    """Replace a text file atomically."""
    p = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-")  # a fresh file: never follows a planted link
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_jsonl(path, records):
    write_jsonl(path, read_jsonl(path) + list(records))
