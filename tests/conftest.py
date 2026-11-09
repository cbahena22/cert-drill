"""Offline by default: no key in the environment, no socket connect or bind (except 127.0.0.1 in tests marked `loopback`)."""
import shutil
import socket
from pathlib import Path

import pytest

from cert_drill.model import load_deck, read_jsonl

ROOT = Path(__file__).resolve().parents[1]
DECKS = ROOT / "decks"


@pytest.fixture(autouse=True)
def offline(monkeypatch, request):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    loopback = request.node.get_closest_marker("loopback") is not None
    real_connect, real_bind = socket.socket.connect, socket.socket.bind

    def guard(real):
        def inner(self, address, *a, **k):
            host = address[0] if isinstance(address, tuple) else address
            if loopback and host == "127.0.0.1":
                return real(self, address, *a, **k)
            raise OSError("network is disabled in tests")
        return inner

    monkeypatch.setattr(socket.socket, "connect", guard(real_connect))
    monkeypatch.setattr(socket.socket, "bind", guard(real_bind))


@pytest.fixture
def deck_copy(tmp_path):
    """A writable copy of the networking deck: deck.toml and sources only (no approved questions yet)."""
    def make(name="networking-basics", with_questions=False):
        dst = tmp_path / name
        shutil.copytree(DECKS / name, dst, ignore=None if with_questions else shutil.ignore_patterns("questions.jsonl"))
        return load_deck(dst)
    return make


@pytest.fixture(scope="session")
def net_deck():
    return load_deck(DECKS / "networking-basics")


@pytest.fixture(scope="session")
def net_questions(net_deck):
    return read_jsonl(net_deck.path("questions.jsonl"))
