"""Publish hygiene over the files git tracks in this folder: stdlib-only imports, no shell, no URLs, no private names, no key-shaped text."""
import ast
import ipaddress
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SKIP_DIRS = {".venv", "out", "__pycache__", ".git", "dist", "build", ".pytest_cache"}
SKIP_FILES = {"requirements-dev.lock", "requirements-live.lock", "requirements-dev.in", "requirements-live.in"}
BINARY = {".gif", ".png", ".jpg", ".pyc", ".whl", ".sqlite"}
DOC_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "127.0.0.1/32")]


def tracked_files():
    """Files git tracks in this folder (or would track: not ignored). Falls back to a walk outside a repository."""
    try:
        r = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=str(ROOT), stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, check=True, timeout=60)
        found = [ROOT / n for n in r.stdout.decode("utf-8").split("\0") if n]
    except (OSError, subprocess.SubprocessError):
        found = [p for p in ROOT.rglob("*") if p.is_file()]
    return [p for p in found if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS) and ".egg-info" not in str(p)]


def text_files():
    return [p for p in tracked_files() if p.suffix not in BINARY and p.name not in SKIP_FILES]


def read(p):
    return p.read_text(encoding="utf-8", errors="replace")


def rel(p):
    return p.relative_to(ROOT).as_posix()


def src_py():
    return list(SRC.rglob("*.py"))


def test_there_are_files_to_check():
    names = {rel(p) for p in tracked_files()}
    assert {"pyproject.toml", "requirements-dev.lock", "requirements-live.lock", "pytest.ini", "LICENSE", "SECURITY.md", ".gitignore"} <= names
    assert any(n.startswith("src/cert_drill/") for n in names)


def test_runtime_imports_are_stdlib_or_the_package_outside_live():
    allowed = set(sys.stdlib_module_names) | {"cert_drill"}
    bad = []
    for p in src_py():
        if "live" in p.relative_to(SRC).parts:
            continue
        for node in ast.walk(ast.parse(read(p))):
            if isinstance(node, ast.Import):
                bad += [f"{p.name}: {a.name}" for a in node.names if a.name.split(".")[0] not in allowed]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and node.module.split(".")[0] not in allowed:
                bad.append(f"{p.name}: {node.module}")
    assert bad == []


def test_no_shell_eval_or_os_system_anywhere_in_python():
    bad = []
    for p in [q for q in tracked_files() if q.suffix == ".py"]:
        for node in ast.walk(ast.parse(read(p))):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                        bad.append(f"{p.name}:{node.lineno} shell=")
                f = node.func
                if isinstance(f, ast.Name) and f.id in ("eval", "exec"):
                    bad.append(f"{p.name}:{node.lineno} {f.id}")
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "os" and f.attr in ("system", "popen"):
                    bad.append(f"{p.name}:{node.lineno} os.{f.attr}")
    assert bad == []


def test_no_url_in_src():
    """No URL in the package, except the local server's own loopback origin (127.0.0.1 / localhost with a port)."""
    pat = re.compile(r"(?i)\b(?:https?|ftp|wss?)://(?!(?:127\.0\.0\.1|localhost):\{)")
    assert [rel(p) for p in SRC.rglob("*") if p.is_file() and p.suffix not in BINARY and pat.search(read(p))] == []


def test_no_network_modules_in_src_outside_live():
    """Only server.py may import a network module, and only `http.server` (the 127.0.0.1 study page)."""
    banned = {"socket", "socketserver", "urllib", "http", "ftplib", "smtplib", "ssl", "requests", "asyncio", "selectors"}
    bad = []
    for p in src_py():
        if "live" in p.relative_to(SRC).parts:
            continue
        for node in ast.walk(ast.parse(read(p))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for n in names:
                if n.split(".")[0] in banned and not (p.name == "server.py" and n == "http.server"):
                    bad.append(f"{p.name}: {n}")
    assert bad == []


def test_the_server_binds_loopback_only():
    text = read(SRC / "cert_drill" / "server.py")
    assert 'BIND = "127.0.0.1"' in text and "(BIND, port)" in text
    assert not re.search(r"0\.0\.0\.0|::\b|\"\"\s*,\s*port", text)


def test_no_absolute_home_paths_or_user_names():
    pats = [re.compile("/ho" + r"me/\w+"), re.compile("/Us" + r"ers/\w+"), re.compile("[A-Za-z]:" + r"\\+" + "Us" + "ers" + r"\\"), re.compile("/c/Us" + "ers")]
    assert [rel(p) for p in text_files() for pat in pats if pat.search(read(p))] == []


def test_no_ipv4_outside_documentation_ranges_and_loopback():
    pat = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.]*\d)")
    bad = []
    for p in text_files():
        for m in pat.finditer(read(p)):
            try:
                ip = ipaddress.ip_address(m.group(0))
            except ValueError:
                continue
            if not any(ip in n for n in DOC_NETS):
                bad.append(f"{rel(p)}: {m.group(0)}")
    assert bad == []


def test_no_emails_outside_reserved_domains():
    pat = re.compile(r"\b[\w.+-]+@(?!(?:[\w-]+\.)*(?:example|invalid|test)\b)[\w-]+\.[A-Za-z]{2,}\b")
    assert [rel(p) for p in text_files() if pat.search(read(p))] == []


def test_banned_words():
    words = ["Q" + "C", "ru" + "bric", "ju" + "dge", "gold" + r"\s+" + "label", "adver" + "sarial"]
    pat = re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)
    assert [f"{rel(p)}: {m.group(0)}" for p in text_files() for m in [pat.search(read(p))] if m] == []


def test_private_names_and_shapes_are_absent():
    names = ["session" + "lib", "session" + "_guard", "soc" + "-main", "loop" + "-tick", r"\.sessions" + "/", "soc" + "-agent-lab", "claude" + "-certs", "cissp" + "-coach"]
    pat = re.compile("|".join(names), re.IGNORECASE)
    shapes = [re.compile(r"\bw\d{2}\b"), re.compile(r"\bB\d\.\d\b")]
    bad = []
    for p in text_files():
        t = read(p)
        if pat.search(t):
            bad.append(f"{rel(p)}: private name")
        bad += [f"{rel(p)}: {s.pattern}" for s in shapes if s.search(t)]
    assert bad == []


def test_adr_numbers_do_not_exceed_the_public_decisions_file():
    decisions = ROOT / "docs" / "decisions.md"
    count = len(re.findall(r"(?m)^#+\s*ADR-\d{3}\b", read(decisions))) if decisions.exists() else 0
    bad = [f"{rel(p)}: {m.group(0)}" for p in text_files() for m in re.finditer(r"\bADR-(\d{3})\b", read(p)) if int(m.group(1)) > count]
    assert bad == []


def test_no_key_shaped_text():
    pats = [
        re.compile("sk" + r"-[A-Za-z0-9_-]{20,}"),
        re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\s*[:=]\s*[\"'][^\"']{6,}"),
        re.compile("-----BEGIN [A-Z ]*PRIVATE" + " KEY"),
        re.compile(r"\bAK" + r"IA[0-9A-Z]{16}\b"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
        re.compile(r"\b(?![0-9a-fA-F]+\b)[A-Za-z0-9]{40,}\b"),
    ]
    assert [f"{rel(p)}: {pat.pattern[:20]}" for p in text_files() for pat in pats if pat.search(read(p))] == []


def test_no_private_deck_is_tracked():
    names = [rel(p) for p in tracked_files()]
    assert [n for n in names if "decks/private/" in n or n.endswith(".sqlite")] == []


def test_every_tracked_deck_is_self_written_or_public_domain():
    decks = [p for p in tracked_files() if p.name == "deck.toml"]
    assert len(decks) >= 4  # two sample decks, each shipped twice (decks/ and package data)
    for p in decks:
        meta = tomllib.loads(read(p))
        for s in meta["sources"]:
            assert s.get("source_license") in ("self-written", "public-domain"), f"{rel(p)}: {s.get('path')}"


def test_every_replay_file_has_the_synthetic_header():
    replays = [p for p in tracked_files() if "replays" in p.parts and p.suffix == ".jsonl"]
    assert len(replays) >= 6
    for p in replays:
        first = read(p).splitlines()[0]
        assert first == '{"note": "hand-written, not model output", "synthetic": true}', rel(p)


def test_lock_files_have_hashes_and_the_windows_only_dependency():
    for name in ("requirements-dev.lock", "requirements-live.lock"):
        lock = read(ROOT / name)
        assert "--hash=sha256:" in lock, name
        bare = [ln for ln in lock.splitlines() if re.match(r"^[A-Za-z0-9_.-]+==", ln) and "\\" not in ln]
        assert bare == [], name
    assert "colorama" in read(ROOT / "requirements-dev.lock")  # the Windows-only dependency made it into the universal lock
    assert "anthropic==" in read(ROOT / "requirements-live.lock")
    assert "anthropic" not in read(ROOT / "requirements-dev.lock")


def test_no_runtime_dependencies_are_declared():
    meta = tomllib.loads(read(ROOT / "pyproject.toml"))
    assert meta["project"]["dependencies"] == [] and meta["project"]["requires-python"] == ">=3.11"
    assert meta["project"]["scripts"] == {"drill": "cert_drill.cli:main"}
    assert list(meta["project"]["optional-dependencies"]) == ["live"]


def test_wheel_carries_the_package_data(tmp_path):
    work = tmp_path / "build copy"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".venv", "build", "dist", "*.egg-info", "__pycache__", ".pytest_cache", "out"))
    out = tmp_path / "wheelhouse"
    r = subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(out), str(work)], env=dict(os.environ),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600, check=False)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")[-2000:]
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1
    names = set(zipfile.ZipFile(wheels[0]).namelist())
    for need in ("decks/networking-basics/deck.toml", "decks/networking-basics/questions.jsonl", "decks/networking-basics/sources/addressing.md",
                 "decks/us-constitution/deck.toml", "replays/write-networking-basics.jsonl", "replays/blind-networking-basics.jsonl",
                 "replays/grader-networking-basics.jsonl", "replays/write-us-constitution.jsonl", "web/index.html", "web/app.js",
                 "web/app.css", "evals/planted-faults.jsonl", "evals/expected-grades.jsonl"):
        assert "cert_drill/data/" + need in names, need
    assert not any(n.startswith("tests/") for n in names)
