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


def write_moved_session(tmp_path, uuid, old_cwd, new_cwd, pad=0, tail_pad=0):
    """A session that began in old_cwd and continued in new_cwd after a move.

    Claude Code stamps every line with the cwd as it was when that line was
    written, so a directory renamed mid-session leaves the head of the file
    naming a path that no longer exists.
    """
    root = tmp_path / "claude" / "projects" / "-proj"
    path = root / f"{uuid}.jsonl"
    lines = [json.dumps({"type": "user", "cwd": old_cwd, "sessionId": uuid})]
    for _ in range(pad):
        lines.append(json.dumps({"type": "assistant", "cwd": old_cwd, "filler": "x" * 900}))
    lines.append(json.dumps({"type": "user", "cwd": new_cwd, "sessionId": uuid}))
    for _ in range(tail_pad):
        lines.append(json.dumps({"type": "assistant", "cwd": new_cwd, "filler": "y" * 900}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_extract_follows_a_session_whose_directory_was_moved(tmp_path):
    """The head names a directory that is gone; the tail says where it went."""
    live = str(tmp_path / "moved-here")
    (tmp_path / "moved-here").mkdir()
    p = write_moved_session(tmp_path, "m" * 8, "/nonexistent/was-here", live)
    assert history.extract(p)[0] == live


def test_extract_follows_a_move_across_a_multi_megabyte_transcript(tmp_path):
    """The two cwds are in different reads: one in the head, one in the tail."""
    live = str(tmp_path / "landed")
    (tmp_path / "landed").mkdir()
    p = write_moved_session(tmp_path, "n" * 8, "/nonexistent/gone", live, pad=200, tail_pad=200)
    assert p.stat().st_size > 2 * 64 * 1024
    assert history.extract(p)[0] == live


def test_extract_keeps_the_head_cwd_when_it_still_exists(tmp_path):
    """No move happened, so the tail is never consulted and the start wins."""
    start = str(tmp_path / "start")
    later = str(tmp_path / "later")
    (tmp_path / "start").mkdir()
    (tmp_path / "later").mkdir()
    p = write_moved_session(tmp_path, "k" * 8, start, later)
    assert history.extract(p)[0] == start


def test_extract_ignores_a_nested_cwd_in_a_tool_result(tmp_path):
    """Only a line's own top-level cwd counts.

    A structured tool result carries its own keys, and a nested "cwd" is written
    unescaped in the raw bytes — so searching the tail with the regex finds it
    and attributes the session to whatever directory a tool happened to report.
    Parsing each line and reading only its top-level key is what excludes it.
    """
    live = str(tmp_path / "real")
    (tmp_path / "real").mkdir()
    root = tmp_path / "claude" / "projects" / "-proj"
    path = root / "tttttttt.jsonl"
    path.write_text(
        json.dumps({"type": "user", "cwd": "/nonexistent/gone"})
        + "\n"
        + json.dumps({"type": "user", "cwd": live})
        + "\n"
        + json.dumps({"type": "assistant", "toolUseResult": {"cwd": str(tmp_path)}})
        + "\n",
        encoding="utf-8",
    )
    assert history.extract(path)[0] == live


def test_extract_keeps_the_head_cwd_when_the_whole_project_is_gone(tmp_path):
    """Nothing to follow — the directory really was deleted, not moved."""
    p = write_moved_session(tmp_path, "g" * 8, "/nonexistent/a", "/nonexistent/b")
    assert history.extract(p)[0] == "/nonexistent/a"


def test_project_dirs_lists_a_project_that_moved(tmp_path):
    """The user-visible bug: a moved project vanished from the picker."""
    live = str(tmp_path / "Games")
    (tmp_path / "Games").mkdir()
    write_moved_session(tmp_path, "p" * 8, "/nonexistent/Downloads", live)
    assert history.project_dirs(history.scan()) == [live]


def test_humanise_age():
    assert history.humanise_age(90) == "1m"
    assert history.humanise_age(3600 * 2.5) == "2.5h"
    assert history.humanise_age(86400 * 3) == "3d"


def test_format_row_abbreviates_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/home/x")
    s = history.Session(uuid="u", cwd="/home/x/4s", title="T", mtime=time.time(), path=tmp_path)
    assert "~/4s" in history.format_row(s, time.time())
