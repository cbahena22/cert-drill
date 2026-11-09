import json

import pytest

from cert_drill.model import DeckError
from cert_drill.replay import REPLAYS, SYNTHETIC_NOTE, ReplayGrader, check_header, default_replay, is_synthetic, load_replay

SYN = {"synthetic": True, "note": SYNTHETIC_NOTE}


def put(tmp_path, *rows):
    p = tmp_path / "r.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def test_a_synthetic_header_is_accepted_and_the_rest_returned(tmp_path):
    h, rows = load_replay(put(tmp_path, SYN, {"a": 1}))
    assert is_synthetic(h) and rows == [{"a": 1}]


@pytest.mark.parametrize("first", [{"a": 1}, {"synthetic": True}, {"synthetic": False, "note": SYNTHETIC_NOTE},
                                   {"synthetic": True, "note": "model output", }, {"synthetic": True, "note": SYNTHETIC_NOTE, "extra": 1},
                                   {"recorded": True, "model": "m"}, "text", None])
def test_a_file_without_a_valid_header_is_refused(tmp_path, first):
    with pytest.raises(DeckError, match="header"):
        load_replay(put(tmp_path, first, {"a": 1}))


def test_an_empty_or_missing_file_is_refused(tmp_path):
    (tmp_path / "e.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(DeckError):
        load_replay(tmp_path / "e.jsonl")
    with pytest.raises(DeckError):
        load_replay(tmp_path / "missing.jsonl")


def test_a_recorded_header_must_name_model_and_date():
    assert check_header({"recorded": True, "model": "some-model", "date": "2027-01-01"})
    assert not check_header({"recorded": True, "model": "some-model"})


def test_every_shipped_replay_loads_and_is_synthetic():
    files = sorted(REPLAYS.glob("*.jsonl"))
    assert len(files) >= 6
    for f in files:
        h, _ = load_replay(f)
        assert is_synthetic(h), f.name


def test_default_replay_names():
    assert default_replay("write", "networking-basics").name == "write-networking-basics.jsonl"


def test_replay_grader_lookup_and_miss(tmp_path, net_questions):
    q = next(x for x in net_questions if x["id"] == "addr-f1")
    p = put(tmp_path, SYN, {"question": "addr-f1", "answer": "my  answer", "reply": {"points": []}})
    g = ReplayGrader(p)
    assert g.reply(q, "my answer") == {"points": []}  # whitespace is normalised
    with pytest.raises(DeckError, match="no replayed grader reply"):
        g.reply(q, "another answer")
