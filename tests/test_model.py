import os
import shutil

import pytest

from cert_drill.model import DeckError, load_deck, read_jsonl, safe_join, write_jsonl

TOML = """name = "tiny"
[[domains]]
name = "One"
weight = 1
[[sources]]
path = "sources/a.md"
source_license = "self-written"
"""


def make(tmp_path, toml=TOML, files=None):
    (tmp_path / "sources").mkdir(exist_ok=True)
    (tmp_path / "sources" / "a.md").write_text("some notes here\n", encoding="utf-8")
    for name, text in (files or {}).items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "deck.toml").write_text(toml, encoding="utf-8")
    return tmp_path


def test_a_valid_deck_loads(tmp_path):
    d = load_deck(make(tmp_path))
    assert d.name == "tiny" and d.domain_names == ["One"] and d.weight("One") == 1.0
    assert d.source_text("sources/a.md").startswith("some notes")


@pytest.mark.parametrize("old,new,match", [
    ('source_license = "self-written"\n', "", "no source_license"),
    ('source_license = "self-written"', 'source_license = "exam-dump"', "source_license must be one of"),
    ('name = "tiny"', 'name = "Bad Name"', "name must be"),
    ("weight = 1", "weight = 0", "weight"),
    ('path = "sources/a.md"', 'path = "../escape.md"', "inside the deck folder|bad path"),
])
def test_deck_toml_rules(tmp_path, old, new, match):
    with pytest.raises(DeckError, match=match):
        load_deck(make(tmp_path, TOML.replace(old, new, 1)))


def test_write_refuses_a_source_without_a_license(tmp_path):
    from cert_drill.writer import write_from_replay
    from cert_drill.replay import default_replay

    toml = TOML.replace('source_license = "self-written"\n', "")
    with pytest.raises(DeckError, match="no source_license"):
        write_from_replay(load_deck(make(tmp_path, toml)), default_replay("write", "networking-basics"))


def test_duplicate_domains_and_missing_files_are_refused(tmp_path):
    with pytest.raises(DeckError, match="unique"):
        load_deck(make(tmp_path, TOML + '[[domains]]\nname = "One"\nweight = 2\n'))
    with pytest.raises(DeckError, match="missing"):
        load_deck(make(tmp_path, TOML.replace("a.md", "b.md")))
    with pytest.raises(DeckError, match="deck.toml"):
        load_deck(tmp_path / "nowhere")


def test_invalid_toml_is_a_deck_error(tmp_path):
    with pytest.raises(DeckError, match="TOML"):
        load_deck(make(tmp_path, "name = [unclosed"))


@pytest.mark.parametrize("bad", ["", "/etc/passwd", "../x", "a/../../x", "a\\b", "C:/x", "a\0b", None, 5])
def test_safe_join_refuses(tmp_path, bad):
    with pytest.raises(DeckError):
        safe_join(tmp_path, bad)


def test_safe_join_accepts_a_nested_relative_path(tmp_path):
    assert safe_join(tmp_path, "sources/a.md") == (tmp_path / "sources" / "a.md").resolve()


def test_a_symlink_that_leaves_the_deck_folder_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("x", encoding="utf-8")
    deck = tmp_path / "deck"
    deck.mkdir()
    try:
        os.symlink(outside, deck / "link")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    with pytest.raises(DeckError):
        safe_join(deck, "link/secret.md")


def test_source_text_only_for_declared_sources(tmp_path):
    d = load_deck(make(tmp_path, files={"other.md": "x"}))
    with pytest.raises(DeckError):
        d.source_text("other.md")


def test_oversized_source_is_refused(tmp_path):
    root = make(tmp_path)
    (root / "sources" / "a.md").write_text("x" * 1_000_001, encoding="utf-8")
    with pytest.raises(DeckError, match="too large"):
        load_deck(root)


def test_jsonl_round_trip_caps_and_errors(tmp_path):
    p = tmp_path / "x.jsonl"
    assert read_jsonl(p) == []
    write_jsonl(p, [{"a": 1}, {"b": "ü"}])
    assert read_jsonl(p) == [{"a": 1}, {"b": "ü"}]
    p.write_text("{bad\n", encoding="utf-8")
    with pytest.raises(DeckError, match="not valid JSON"):
        read_jsonl(p)
    p.write_text("[" + "1," * 30000 + "1]\n", encoding="utf-8")
    with pytest.raises(DeckError, match="too long"):
        read_jsonl(p)
    p.write_text("1\n" * 5001, encoding="utf-8")
    with pytest.raises(DeckError, match="more than"):
        read_jsonl(p)
    shutil.rmtree(tmp_path, ignore_errors=True)
