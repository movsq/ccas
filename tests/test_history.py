import importlib
import json
import os
import time
import pytest

import ccas.paths as paths
import ccas.history as history


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_HOME", str(tmp_path / "claude"))
    importlib.reload(paths)
    importlib.reload(history)
    (tmp_path / "claude" / "projects" / "-proj").mkdir(parents=True)
    return tmp_path


def write_session(tmp_path, uuid, cwd, titles=(), pad=0, mtime=None):
    root = tmp_path / "claude" / "projects" / "-proj"
    path = root / f"{uuid}.jsonl"
    lines = [json.dumps({"type": "user", "cwd": cwd, "sessionId": uuid})]
    for _ in range(pad):
        lines.append(json.dumps({"type": "assistant", "filler": "x" * 900}))
    for t in titles:
        lines.append(json.dumps({"type": "ai-title", "aiTitle": t, "sessionId": uuid}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def test_extract_reads_cwd_and_title(tmp_path):
    p = write_session(tmp_path, "a" * 8, "/home/x/proj", ["Do the thing"])
    assert history.extract(p) == ("/home/x/proj", "Do the thing")


def test_extract_takes_first_title_not_last(tmp_path):
    p = write_session(tmp_path, "b" * 8, "/home/x", ["Human readable title", "slugged-title"])
    assert history.extract(p)[1] == "Human readable title"


def test_extract_finds_title_beyond_the_64kb_head(tmp_path):
    p = write_session(tmp_path, "c" * 8, "/home/x", ["Deep title"], pad=100)
    assert p.stat().st_size > 64 * 1024
    assert history.extract(p) == ("/home/x", "Deep title")


def test_extract_decodes_unicode_escapes(tmp_path):
    p = write_session(tmp_path, "u" * 8, "/home/x", ["Vojtěch's 🎉 session"])
    assert p.read_bytes().count(b"\\u") > 0, "fixture must contain escapes"
    assert history.extract(p)[1] == "Vojtěch's 🎉 session"


def test_extract_handles_escaped_quotes_in_titles(tmp_path):
    p = write_session(tmp_path, "q" * 8, "/home/x", ['fix the "thing"'])
    assert history.extract(p)[1] == 'fix the "thing"'


def test_extract_tolerates_missing_title(tmp_path):
    p = write_session(tmp_path, "d" * 8, "/home/x")
    assert history.extract(p) == ("/home/x", None)


def test_extract_tolerates_malformed_and_empty_files(tmp_path):
    root = tmp_path / "claude" / "projects" / "-proj"
    (root / "empty.jsonl").write_text("", encoding="utf-8")
    (root / "junk.jsonl").write_text("not json at all\n{{{\n", encoding="utf-8")
    assert history.extract(root / "empty.jsonl") == (None, None)
    assert history.extract(root / "junk.jsonl") == (None, None)


def test_scan_sorts_newest_first_and_skips_cwdless(tmp_path):
    now = time.time()
    write_session(tmp_path, "1" * 8, "/home/x", ["old"], mtime=now - 9000)
    write_session(tmp_path, "2" * 8, "/home/x", ["new"], mtime=now - 10)
    (tmp_path / "claude" / "projects" / "-proj" / "bad.jsonl").write_text("{}\n")
    got = history.scan()
    assert [s.title for s in got] == ["new", "old"]


def test_scan_ignores_nested_subagent_transcripts(tmp_path):
    nested = tmp_path / "claude" / "projects" / "-proj" / "sub"
    nested.mkdir()
    (nested / "x.jsonl").write_text(json.dumps({"cwd": "/home/x"}) + "\n")
    write_session(tmp_path, "9" * 8, "/home/x", ["top"])
    assert [s.title for s in history.scan()] == ["top"]


def test_project_dirs_are_distinct_existing_and_recent_first(tmp_path):
    now = time.time()
    live = str(tmp_path)
    write_session(tmp_path, "1" * 8, live, ["a"], mtime=now - 500)
    write_session(tmp_path, "2" * 8, live, ["b"], mtime=now - 10)
    write_session(tmp_path, "3" * 8, "/nonexistent/gone", ["c"], mtime=now - 1)
    assert history.project_dirs(history.scan()) == [live]


def test_humanise_age():
    assert history.humanise_age(90) == "1m"
    assert history.humanise_age(3600 * 2.5) == "2.5h"
    assert history.humanise_age(86400 * 3) == "3d"


def test_format_row_abbreviates_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/home/x")
    s = history.Session(uuid="u", cwd="/home/x/4s", title="T", mtime=time.time(), path=tmp_path)
    assert "~/4s" in history.format_row(s, time.time())
