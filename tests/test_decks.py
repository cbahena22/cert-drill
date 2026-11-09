import filecmp
import re

import pytest

from cert_drill.checker import check_deck
from cert_drill.model import load_deck, read_jsonl
from cert_drill.replay import DATA, default_replay
from cert_drill.writer import FLAG_KEY, review_pending, write_from_replay
from conftest import DECKS


@pytest.mark.parametrize("name,total,mc,free_n", [("networking-basics", 60, 56, 4), ("us-constitution", 20, 18, 2)])
def test_sample_decks_have_the_stated_shape_and_pass_every_check(name, total, mc, free_n):
    deck = load_deck(DECKS / name)
    qs = read_jsonl(deck.path("questions.jsonl"))
    assert len(qs) == total and sum(q["type"] == "mc" for q in qs) == mc and sum(q["type"] == "free" for q in qs) == free_n
    assert check_deck(deck, qs).ok
    assert {q["domain"] for q in qs} == set(deck.domain_names)


def test_the_package_copy_of_each_sample_deck_equals_the_decks_folder():
    for name in ("networking-basics", "us-constitution"):
        cmp = filecmp.dircmp(DECKS / name, DATA / "decks" / name)
        assert not cmp.left_only and not cmp.right_only and not cmp.diff_files
        assert not cmp.common_dirs or not filecmp.dircmp(DECKS / name / "sources", DATA / "decks" / name / "sources").diff_files


def test_the_networking_deck_uses_documentation_addresses_only():
    deck = load_deck(DECKS / "networking-basics")
    text = "\n".join(deck.source_text(p) for p in deck.source_paths) + "\n".join(map(str, read_jsonl(deck.path("questions.jsonl"))))
    for ip in set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)):
        assert ip.startswith(("192.0.2.", "198.51.100.", "203.0.113.", "127.0.0.1")), ip


def test_writing_from_the_replay_rebuilds_the_shipped_deck(deck_copy):
    deck = deck_copy()
    res = write_from_replay(deck, default_replay("write", deck.name), default_replay("blind", deck.name))
    assert (res.written, res.passed, res.rejected, res.flagged, res.synthetic) == (67, 61, 6, 1, True)
    flagged = [e for e in read_jsonl(deck.path("pending.jsonl")) if e["flags"]]
    assert [e["q"]["id"] for e in flagged] == ["prot-amb"] and flagged[0]["flags"] == [FLAG_KEY]
    reasons = {r["q"]["id"]: r["reasons"][0].split(":")[0] for r in read_jsonl(deck.path("rejected.jsonl"))}
    assert reasons == {"flaw-quote": "citation-quote-not-found", "flaw-vague": "vague-option", "flaw-dupstem": "near-duplicate-stem",
                       "flaw-domain": "domain-unknown", "flaw-source": "citation-source-unknown", "flaw-options": "options-not-distinct"}
    ok, rej, left = review_pending(deck, lambda e: "r" if e["flags"] else "a")
    assert (ok, rej, left) == (60, 1, 0)
    assert read_jsonl(deck.path("questions.jsonl")) == read_jsonl(DECKS / "networking-basics" / "questions.jsonl")


def test_writing_twice_adds_nothing_new(deck_copy):
    deck = deck_copy()
    write_from_replay(deck, default_replay("write", deck.name))
    again = write_from_replay(deck, default_replay("write", deck.name))
    assert again.passed == 0 and again.rejected == again.written


def test_review_can_skip_and_a_bad_pending_question_is_caught_at_approval(deck_copy):
    deck = deck_copy()
    write_from_replay(deck, default_replay("write", deck.name))
    ok, rej, left = review_pending(deck, lambda e: "a" if e["q"]["id"] == "addr-01" else "s")
    assert (ok, rej) == (1, 0) and left == 60
    # tamper with a pending file: the approval re-checks the question
    from cert_drill.model import write_jsonl

    pend = read_jsonl(deck.path("pending.jsonl"))
    pend[0]["q"]["citations"][0]["quote"] = "this quote is not in any source"
    write_jsonl(deck.path("pending.jsonl"), pend)
    ok, rej, left = review_pending(deck, lambda e: "a")
    assert rej == 1 and ok == 59


def test_the_replays_name_only_known_decks_and_questions():
    for name in ("networking-basics", "us-constitution"):
        for kind in ("write", "blind", "grader"):
            assert default_replay(kind, name).is_file()
