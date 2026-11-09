import copy

import pytest

from cert_drill.checker import check_batch, check_deck, check_question, normalise, stem_tokens

SRC = "sources/protocols.md"
QUOTE = "SSH provides encrypted remote login on TCP port 22."


def base():
    return {"id": "t-01", "type": "mc", "domain": "Protocols", "stem": "Which service gives remote login with encryption?",
            "options": ["SSH", "Telnet", "FTP", "SNMP"], "key": 0, "explanation": "SSH encrypts the session.",
            "citations": [{"source": SRC, "quote": QUOTE}]}


def free():
    return {"id": "t-f1", "type": "free", "domain": "Protocols", "stem": "Describe SSH.", "explanation": "See the notes.",
            "marking_points": [{"text": "Says SSH gives encrypted remote login", "citation": {"source": SRC, "quote": QUOTE}},
                               {"text": "Names the port", "citation": {"source": SRC, "quote": "on TCP port 22"}}]}


def mutate(fn, q=None):
    q = copy.deepcopy(q or base())
    fn(q)
    return q


def codes(q, deck, seen=()):
    return [r.split(":")[0] for r in check_question(q, deck, seen)]


def test_a_good_question_passes(net_deck):
    assert check_question(base(), net_deck) == []
    assert check_question(free(), net_deck) == []


RULES = [
    ("unknown-field", lambda q: q.update(extra=1)),
    ("missing-field", lambda q: q.pop("stem")),
    ("bad-type", lambda q: q.update(type="essay")),
    ("bad-id", lambda q: q.update(id="Bad ID")),
    ("domain-unknown", lambda q: q.update(domain="Cooking")),
    ("bad-field", lambda q: q.update(stem="   ")),
    ("bad-field", lambda q: q.update(explanation=5)),
    ("too-long", lambda q: q.update(stem="x" * 401)),
    ("too-long", lambda q: q["options"].__setitem__(1, "y" * 201)),
    ("too-long", lambda q: q.update(explanation="z" * 801)),
    ("control-characters", lambda q: q.update(stem="Which \x1b[31mservice")),
    ("options-count", lambda q: q.update(options=["SSH", "Telnet", "FTP"])),
    ("options-count", lambda q: q.update(options=["SSH", "Telnet", "FTP", "SNMP", "DNS"])),
    ("options-not-distinct", lambda q: q["options"].__setitem__(1, "ssh")),
    ("vague-option", lambda q: q["options"].__setitem__(3, "All of the above")),
    ("vague-option", lambda q: q["options"].__setitem__(3, "None of these")),
    ("bad-key", lambda q: q.update(key=4)),
    ("bad-key", lambda q: q.update(key=-1)),
    ("bad-key", lambda q: q.update(key=True)),
    ("bad-key", lambda q: q.update(key=[0, 1])),
    ("bad-key", lambda q: q.update(key="0")),
    ("bad-citations", lambda q: q.update(citations=[])),
    ("bad-citations", lambda q: q.update(citations=[q["citations"][0]] * 4)),
    ("bad-citation", lambda q: q["citations"][0].update(extra=1)),
    ("citation-source-unknown", lambda q: q["citations"][0].update(source="sources/other.md")),
    ("citation-source-unknown", lambda q: q["citations"][0].update(source="../deck.toml")),
    ("citation-quote-not-found", lambda q: q["citations"][0].update(quote="SSH provides encrypted remote login on TCP port 23.")),
    ("quote-too-short", lambda q: q["citations"][0].update(quote="SSH")),
    ("too-long", lambda q: q["citations"][0].update(quote=QUOTE * 20)),
]


@pytest.mark.parametrize("code,fn", RULES, ids=[f"{c}-{i}" for i, (c, _) in enumerate(RULES)])
def test_each_rule_refuses_with_its_reason(net_deck, code, fn):
    assert code in codes(mutate(fn), net_deck)


FREE_RULES = [
    ("points-count", lambda q: q.update(marking_points=q["marking_points"][:1])),
    ("points-count", lambda q: q.update(marking_points=q["marking_points"] * 3)),
    ("bad-point", lambda q: q["marking_points"][0].update(extra="x")),
    ("bad-point", lambda q: q["marking_points"].__setitem__(0, "text")),
    ("citation-quote-not-found", lambda q: q["marking_points"][1]["citation"].update(quote="on TCP port 9999")),
    ("citation-source-unknown", lambda q: q["marking_points"][0]["citation"].update(source="nowhere.md")),
    ("bad-field", lambda q: q["marking_points"][0].update(text="")),
    ("unknown-field", lambda q: q.update(key=1)),
]


@pytest.mark.parametrize("code,fn", FREE_RULES, ids=[f"{c}-{i}" for i, (c, _) in enumerate(FREE_RULES)])
def test_free_text_rules(net_deck, code, fn):
    assert code in codes(mutate(fn, free()), net_deck)


def test_non_objects_are_refused(net_deck):
    for bad in (None, [], "text", 5):
        assert codes(bad, net_deck) == ["bad-record"]


def test_quotes_match_through_curly_marks_and_line_wrapping(net_deck):
    q = mutate(lambda q: q["citations"][0].update(quote="SSH provides\n  encrypted   remote login"))
    assert check_question(q, net_deck) == []
    assert normalise("“It’s”  fine now") == "\"It's\" fine now"


def test_near_duplicate_stems_are_refused(net_deck):
    seen = [stem_tokens("Which service gives remote login with encryption today?")]
    assert "near-duplicate-stem" in codes(base(), net_deck, seen)
    assert "near-duplicate-stem" not in codes(base(), net_deck, [stem_tokens("What does ARP map to?")])


def test_check_deck_catches_duplicates_in_order_and_ids(net_deck):
    twin = mutate(lambda q: q.update(id="t-02"))
    rep = check_deck(net_deck, [base(), twin])
    assert list(rep.per_question) == ["t-02"] and "near-duplicate-stem" in rep.per_question["t-02"][0]
    same_id = mutate(lambda q: q.update(stem="Which port does DNS listen on, exactly?"))
    assert any(r.startswith("duplicate-id") for r in check_deck(net_deck, [base(), same_id]).per_question["t-01"])
    assert check_deck(net_deck, [base()]).ok


def make_mc(i, key_len, other_len):
    opts = ["k" * key_len + str(i)] + ["o" * other_len + str(j) for j in range(3)]
    return {"type": "mc", "options": opts, "key": 0}


def test_batch_check_flags_a_key_that_is_usually_the_longest():
    assert check_batch([make_mc(i, 30, 10) for i in range(10)])[0].startswith("key-usually-longest")
    assert check_batch([make_mc(i, 10, 30) for i in range(10)]) == []
    assert check_batch([make_mc(i, 30, 10) for i in range(5)]) == []  # too few to decide


def test_batch_check_ignores_malformed_questions():
    assert check_batch([{"type": "mc", "options": "x", "key": 0}, None] * 10) == []


def test_the_shipped_decks_pass_every_check(net_deck, net_questions):
    assert check_deck(net_deck, net_questions).ok
