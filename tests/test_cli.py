import json
import re
import shutil
import socket

import pytest

from cert_drill import cli
from cert_drill.demo import run_demo
from cert_drill.model import read_jsonl
from conftest import DECKS


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_demo_prints_the_summary_line_last(capsys):
    code, out, _ = run(capsys, "demo")
    assert code == 0
    assert out.strip().splitlines()[-1] == "written: 67 | passed checks: 61 | rejected: 6 | sessions: 3 | model calls: 0 ($0.00)"
    assert "synthetic replay (hand-written, not model output)" in out


def test_demo_is_deterministic_and_covers_the_other_sample_deck(capsys):
    a, b = run_demo(out=lambda *_: None), run_demo(out=lambda *_: None)
    assert a == b and a["sessions"] == 3
    code, out, _ = run(capsys, "demo", "--sample", "us-constitution")
    assert code == 0 and out.strip().splitlines()[-1].startswith("written: 20 | passed checks: 20 | rejected: 0")


def test_demo_makes_no_network_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "create_connection", boom)
    run_demo(out=lambda *_: None)


def test_the_weak_domain_comes_back_in_the_demo(capsys):
    _, out, _ = run(capsys, "demo")
    line = next(ln for ln in out.splitlines() if ln.startswith("accuracy by domain"))
    vals = dict(re.split(r"\s(?=[\d.]+$)", part) for part in line.split(": ", 2)[2].split(", "))
    assert float(vals["Security"]) < min(float(v) for k, v in vals.items() if k != "Security")


def test_check_passes_on_a_good_deck_and_fails_on_a_bad_one(capsys, tmp_path):
    code, out, _ = run(capsys, "check", "--deck", str(DECKS / "networking-basics"))
    assert code == 0 and "ok: 60 questions" in out
    bad = tmp_path / "d"
    shutil.copytree(DECKS / "networking-basics", bad)
    rows = read_jsonl(bad / "questions.jsonl")
    rows[0]["domain"] = "Cooking"
    (bad / "questions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    code, out, _ = run(capsys, "check", "--deck", str(bad))
    assert code == 1 and "addr-01: domain-unknown" in out


def test_errors_are_short_messages_with_exit_2(capsys, tmp_path):
    code, _, err = run(capsys, "check", "--deck", str(tmp_path))
    assert code == 2 and err.startswith("error:") and "Traceback" not in err


def test_init_creates_a_deck_and_refuses_bad_names_and_overwrites(capsys, tmp_path):
    code, out, _ = run(capsys, "init", "my-deck", "--dir", str(tmp_path))
    assert code == 0 and (tmp_path / "my-deck" / "deck.toml").is_file()
    assert run(capsys, "init", "my-deck", "--dir", str(tmp_path))[0] == 2
    assert run(capsys, "init", "../evil", "--dir", str(tmp_path))[0] == 2
    assert not (tmp_path.parent / "evil").exists()
    # the template is a valid deck once the notes exist and the licence is a public one
    from cert_drill.model import load_deck

    assert load_deck(tmp_path / "my-deck").name == "my-deck"


def test_write_review_study_stats_export_dispute_forget_flow(capsys, tmp_path, monkeypatch):
    deck = tmp_path / "net"
    shutil.copytree(DECKS / "networking-basics", deck, ignore=shutil.ignore_patterns("questions.jsonl"))
    d = str(deck)
    code, out, _ = run(capsys, "write", "--deck", d)
    assert code == 0 and "synthetic replay (hand-written, not model output): 67 proposed, 61 passed" in out
    code, out, _ = run(capsys, "review", "--deck", d, "--approve-clean")
    assert "approved 60" in out and "still pending 1" in out
    code, out, _ = run(capsys, "review", "--deck", d, "--reject", "prot-amb")
    assert "rejected 1" in out
    # study: answer the first question wrongly with a wrong letter, then stop
    answers = iter(["A", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    code, out, err = run(capsys, "study", "--deck", d, "--count", "3")
    assert code == 0 and "answers" in out, err
    assert (deck / "progress.sqlite").is_file()
    code, out, _ = run(capsys, "stats", "--deck", d)
    assert "Addressing" in out and "accuracy" in out
    out_file = tmp_path / "export.json"
    assert run(capsys, "export", "--deck", d, "--out", str(out_file))[0] == 0
    exported = json.loads(out_file.read_text(encoding="utf-8"))
    assert exported["deck"] == "networking-basics" and len(exported["questions"]) == 60 and len(exported["reviews"]) == 1
    qid = exported["reviews"][0]["qid"]
    code, out, _ = run(capsys, "dispute", "--deck", d, "--question", qid, "--grade", "5")
    assert code == 0 and "grade 5" in out
    assert run(capsys, "dispute", "--deck", d, "--question", "never-asked", "--grade", "5")[0] == 2
    assert run(capsys, "dispute", "--deck", d, "--question", qid, "--grade", "9")[0] == 2
    assert run(capsys, "dispute", "--deck", d, "--question", "../x", "--grade", "1")[0] == 2
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    assert run(capsys, "forget", "--deck", d)[0] == 1 and (deck / "progress.sqlite").is_file()
    assert run(capsys, "forget", "--deck", d, "--yes")[0] == 0 and not (deck / "progress.sqlite").exists()


def test_study_without_questions_says_what_to_do(capsys, tmp_path):
    deck = tmp_path / "net"
    shutil.copytree(DECKS / "networking-basics", deck, ignore=shutil.ignore_patterns("questions.jsonl"))
    code, _, err = run(capsys, "study", "--deck", str(deck))
    assert code == 2 and "drill write" in err


def test_terminal_ui_removes_escape_sequences_and_handles_end_of_input(capsys):
    assert cli.clean("a\x1b[31mb\x07c") == "a [31mb c"
    assert cli.clean("x\u202ey\u200bz\nw") == "xyz w"
    q = {"type": "mc", "domain": "D", "stem": "S\x1b[2J", "options": ["a", "b", "c", "d"], "key": 0}
    ui = cli.TerminalUI(inp=lambda p="": (_ for _ in ()).throw(EOFError()))
    assert ui.ask(q, False) is None
    assert "\x1b" not in capsys.readouterr().out


def test_terminal_ui_free_text_self_grade(capsys):
    q = {"type": "free", "domain": "D", "stem": "S", "marking_points": [{"text": "t", "citation": {"source": "s", "quote": "q"}}]}
    replies = iter(["my answer", "x", "4"])
    ui = cli.TerminalUI(self_grading=True, inp=lambda p="": next(replies))
    a = ui.ask(q, False)
    assert a.self_grade == 4
    ui2 = cli.TerminalUI(inp=lambda p="": "typed answer")
    assert ui2.ask(q, False).text == "typed answer"


def test_terminal_ui_rejects_a_bad_letter_then_accepts(capsys):
    q = {"type": "mc", "domain": "D", "stem": "S", "options": ["a", "b", "c", "d"], "key": 0}
    replies = iter(["z", "", "c"])
    assert cli.TerminalUI(inp=lambda p="": next(replies)).ask(q, False).choice == 2


def test_the_tests_run_with_no_key_and_no_network():
    import os

    assert "ANTHROPIC_API_KEY" not in os.environ
    with pytest.raises(OSError, match="disabled"):
        socket.socket().connect(("192.0.2.1", 9))
    with pytest.raises(OSError, match="disabled"):
        socket.socket().bind(("127.0.0.1", 0))


def test_study_refuses_a_hand_edited_deck_with_a_bad_key(capsys, tmp_path):
    deck = tmp_path / "net"
    shutil.copytree(DECKS / "networking-basics", deck)
    rows = read_jsonl(deck / "questions.jsonl")
    rows[0]["key"] = True
    (deck / "questions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    for cmd in ("study", "stats", "export"):
        code, _, err = run(capsys, cmd, "--deck", str(deck))
        assert code == 2 and "fails the checks" in err


def test_trailing_newline_ids_and_names_are_refused(net_deck):
    from cert_drill.checker import check_question
    from cert_drill.model import NAME_RE

    assert not NAME_RE.fullmatch("abc\n")
    q = {"id": "abc\n", "type": "free", "domain": "Protocols", "stem": "s", "explanation": "e", "marking_points": []}
    assert any(r.startswith("bad-id") for r in check_question(q, net_deck))


def test_a_planted_tmp_symlink_is_not_followed(tmp_path):
    import os

    from cert_drill.model import write_jsonl

    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    try:
        os.symlink(victim, tmp_path / "pending.jsonl.tmp")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    write_jsonl(tmp_path / "pending.jsonl", [{"a": 1}])
    assert victim.read_text(encoding="utf-8") == "keep"


def test_a_corrupt_progress_file_is_a_short_error(capsys, tmp_path):
    deck = tmp_path / "net"
    shutil.copytree(DECKS / "networking-basics", deck)
    (deck / "progress.sqlite").write_bytes(b"this is not a database" * 50)
    code, _, err = run(capsys, "stats", "--deck", str(deck))
    assert code == 2 and "Traceback" not in err


@pytest.mark.parametrize("options", [["a", "b", "c", "d", "e"], ["a", "b"], "abcd", None])
def test_review_of_a_hand_edited_pending_entry_with_the_wrong_option_count_is_no_traceback(capsys, tmp_path, monkeypatch, options):
    from cert_drill.model import write_jsonl

    deck = tmp_path / "net"
    shutil.copytree(DECKS / "networking-basics", deck)
    q = dict(read_jsonl(deck / "questions.jsonl")[0], id="hand-edited", options=options)
    write_jsonl(deck / "pending.jsonl", [{"q": q, "flags": []}])
    monkeypatch.setattr("builtins.input", lambda prompt="": "a")
    code, out, err = run(capsys, "review", "--deck", str(deck))
    assert code == 0 and "Traceback" not in err, err
    assert "MALFORMED" in out and "approved 0, rejected 1, still pending 0" in out
    rej = read_jsonl(deck / "rejected.jsonl")[-1]
    assert rej["q"]["id"] == "hand-edited" and any(r.startswith("options-count") for r in rej["reasons"])
