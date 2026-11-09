"""`drill eval`: deterministic, labelled, and equal to the committed evals/results/eval.json."""
import json

from cert_drill import cli
from cert_drill.evals import EVALS, dumps, run_eval
from cert_drill.grading import VERDICTS
from cert_drill.model import read_jsonl
from conftest import ROOT

COMMITTED = ROOT / "evals" / "results" / "eval.json"


def test_the_eval_equals_the_committed_results():
    assert dumps(run_eval()) == COMMITTED.read_text(encoding="utf-8")


def test_drill_eval_writes_the_same_file(tmp_path, capsys):
    code = cli.main(["eval", "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 0 and (tmp_path / "out" / "eval.json").read_bytes() == COMMITTED.read_bytes()
    assert "model calls: 0 ($0.00)" in out and "checker-planted-faults" in out


def test_every_row_is_labelled_with_what_produced_it():
    res = run_eval()
    assert res["model_calls"] == 0 and res["cost_usd"] == 0
    for r in res["rows"]:
        assert any(w in r["label"] for w in ("code", "stand-in", "synthetic")), r["name"]
        assert r["n"] > 0 and r["result"]
        assert "Claude" not in json.dumps(r)


def test_the_checker_catches_every_fault_it_is_built_to_catch():
    row = run_eval()["rows"][0]
    assert row["code_checkable_caught"] == row["code_checkable_n"] >= 30
    assert row["misses"] and all(m["caught_by"] != "code" and m["note"] for m in row["misses"])
    assert row["caught"] == row["n"] - len(row["misses"])


def test_the_sample_questions_pass_and_the_red_team_scores_as_expected():
    rows = {r["name"]: r for r in run_eval()["rows"]}
    assert rows["checker-sample-questions"]["refused"] == 0 and rows["checker-sample-questions"]["n"] == 80
    assert rows["grader-red-team"]["matches"] == rows["grader-red-team"]["n"] >= 20


def test_the_stand_in_row_reports_its_disagreements():
    row = run_eval()["rows"][2]
    assert row["n"] == 40 and len(row["disagreements"]) == row["n"] - row["exact"]
    assert row["over_credit"] + row["under_credit"] == len(row["disagreements"])


def test_the_expected_grades_set_is_well_formed():
    rows = read_jsonl(EVALS / "expected-grades.jsonl")
    assert len(rows) == 40 and len({r["case"] for r in rows}) == 40
    assert all(set(r["expected_points"]) <= set(VERDICTS) and r["note"] for r in rows)
    planted = read_jsonl(EVALS / "planted-faults.jsonl")
    assert len({r["case"] for r in planted}) == len(planted) and any(r.get("batch") for r in planted)


def test_eval_out_does_not_follow_a_planted_link(tmp_path, capsys):
    import os

    import pytest

    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    try:
        os.symlink(victim, out / "eval.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    assert cli.main(["eval", "--out", str(out)]) == 0
    assert victim.read_text(encoding="utf-8") == "keep" and not (out / "eval.json").is_symlink()
