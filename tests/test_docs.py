"""Doc tests: links resolve, README numbers equal the committed eval, the demo line is real, every result row is labelled, images carry no metadata."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
READOUT = (ROOT / "docs" / "pilot-readout.md").read_text(encoding="utf-8")
AI_LINE = "Designed, specified and reviewed by me; most code written by Claude Code agents I directed."
DOCS = [ROOT / "README.md", ROOT / "SECURITY.md", *sorted((ROOT / "docs").glob("*.md"))]
EVAL = json.loads((ROOT / "evals" / "results" / "eval.json").read_text(encoding="utf-8"))
ROW_RX = re.compile(r"(?m)^\| (.+?) \| (\d+) \| (.+) \|$")
PORT_RX = re.compile(r"(?i)(?:--port\s+|127\.0\.0\.1:|localhost:)(\d+)|default port (\d+)")


def table_rows(doc):
    return [m.groups() for m in ROW_RX.finditer(doc) if m.group(1) != "row" and m.group(1) != "---"]


def gif_extension_labels(gif):
    """Walk the GIF blocks and return the extension labels used (0xF9 graphic control, 0xFF application, 0xFE comment, 0x01 plain text)."""
    flags = gif[10]
    i = 13 + (3 * 2 ** ((flags & 7) + 1) if flags & 0x80 else 0)
    labels = set()
    while i < len(gif) and gif[i] != 0x3B:
        if gif[i] == 0x21:
            labels.add(gif[i + 1]); i += 2
        elif gif[i] == 0x2C:
            f = gif[i + 9]; i += 10 + (3 * 2 ** ((f & 7) + 1) if f & 0x80 else 0) + 1
        else:
            raise AssertionError("malformed GIF")
        while gif[i] != 0:
            i += gif[i] + 1
        i += 1
    return labels


def test_every_relative_link_and_image_resolves():
    bad = []
    for doc in DOCS:
        for target in re.findall(r"\]\(([^)\s]+)\)", doc.read_text(encoding="utf-8")):
            if re.match(r"(?i)^(?:[a-z]+:|#)", target):
                continue
            path = target.split("#")[0]
            if path and not (doc.parent / path).resolve().exists():
                bad.append(f"{doc.name}: {target}")
    assert bad == []


def test_the_ai_use_line_is_present():
    assert AI_LINE in README


def test_readme_and_readout_tables_equal_eval_json():
    want = [(r["label"], str(r["n"]), r["result"]) for r in EVAL["rows"]]
    assert want
    for doc in (README, READOUT):
        assert table_rows(doc) == want


def test_every_result_row_says_code_stand_in_or_synthetic():
    for label, _, _ in table_rows(README) + table_rows(READOUT):
        assert re.match(r"(?i)^(code|stand-in)\b", label) or "synthetic" in label.lower() or "stand-in" in label.lower(), label
    for r in EVAL["rows"]:
        assert re.match(r"(?i)^(code|stand-in)\b", r["label"]) or "synthetic" in r["label"].lower()


def test_the_readme_demo_line_equals_the_last_line_of_drill_demo(tmp_path):
    r = subprocess.run([sys.executable, "-c", "import sys; from cert_drill.cli import main; sys.exit(main(['demo']))"], cwd=str(tmp_path),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")[-500:]
    last = r.stdout.decode("utf-8").rstrip().splitlines()[-1]
    assert last in README.splitlines()


def test_the_committed_images_carry_no_metadata():
    gif = (ROOT / "docs" / "demo.gif").read_bytes()
    assert gif[:6] in (b"GIF87a", b"GIF89a")
    assert gif_extension_labels(gif) <= {0xF9, 0xFF}, "comment or text extension block in the GIF"
    assert b"Created with" not in gif
    for name in ("hero-question.png", "hero-domains.png"):
        data = (ROOT / "docs" / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        chunks, i = [], 8
        while i < len(data):
            n = int.from_bytes(data[i:i + 4], "big")
            chunks.append(data[i + 4:i + 8])
            i += 12 + n
        assert not ({b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"} & set(chunks)), (name, chunks)
        assert len(data) <= 400 * 1024


def test_the_serve_default_port_is_not_the_soc_console_port_and_the_readme_matches():
    from cert_drill.cli import build_parser

    sub = next(a for a in build_parser()._actions if a.dest == "cmd" or hasattr(a, "choices") and a.choices and "serve" in a.choices)
    port = sub.choices["serve"].get_default("port")
    assert port != 8765 and 1024 <= port <= 65535
    assert f"default port {port}" in README
    assert "8765" not in README + READOUT


def test_the_readme_has_the_limits_it_promises():
    for text in ("**exists**", "synthetic", "hand-written", "no Claude numbers", "not tested on macOS", "NDA", "stand-in"):
        assert text in README, text
    for word in ("fully", "every change", "production-ready"):
        assert word not in README.lower()


def test_the_readme_gives_both_shells_one_command():
    assert "PowerShell 5.1:" in README and ".venv\\Scripts\\drill demo" in README
    assert "bash:" in README and ".venv/bin/drill demo" in README
    assert "--no-deps --no-build-isolation ." in README


def test_the_decisions_file_numbers_run_from_one():
    nums = [int(n) for n in re.findall(r"(?m)^## ADR-(\d{3}):", (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8"))]
    assert nums == list(range(1, len(nums) + 1)) and 5 <= len(nums) <= 8


def test_the_license_names_the_holder_and_security_names_the_reporting_route():
    assert "Copyright (c) 2026 cbahena22" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    sec = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "private vulnerability reporting" in sec and "Stub" not in sec


def test_the_opening_does_not_claim_a_model_run():
    """The README opens honestly: designed for Claude, hand-written replays only, no live model output yet."""
    opening = README.split("\n\n", 2)[1]
    assert "designed for Claude" in opening
    assert "hand-written replays only" in opening and "no live model output yet" in opening
    assert "Claude writes the questions" not in README
