"""The panel's pure half — everything decided before a widget exists."""
import json
import os

import pytest

from ccas import panel, paths, registry, usage


@pytest.fixture
def reg(monkeypatch, tmp_path):
    """A two-account registry in a scratch root. Never the user's."""
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    monkeypatch.setenv("CCAS_HOME", str(tmp_path / "claude"))
    r = {"default": "one", "accounts": []}
    registry.add(r, "one", "one@example.com", "First")
    registry.add(r, "two", "two@example.com", None)
    registry.save(r)
    for slug in ("one", "two"):
        paths.account_dir(slug).mkdir(parents=True, exist_ok=True)
    return r


def test_build_state_marks_the_requested_account_current(reg):
    """The panel opens on the account whose bar module was clicked, and the chip
    row has to know which chip is the one you are already looking at."""
    state = panel.build_state("two")
    assert state["slug"] == "two"
    assert [a["current"] for a in state["accounts"]] == [False, True]


def test_build_state_lists_every_account_for_the_chip_row(reg):
    """Switching accounts happens inside the panel, so every account is in the
    state even though only one is displayed in the body."""
    state = panel.build_state("one")
    assert [a["slug"] for a in state["accounts"]] == ["one", "two"]
    assert [a["email"] for a in state["accounts"]] == \
        ["one@example.com", "two@example.com"]


def test_build_state_names_an_account_the_way_the_bar_does(reg):
    """label.display_name() is what the bar shows — nickname, else email. The
    chip must agree with it or one account reads as two different ones."""
    state = panel.build_state("one")
    assert state["accounts"][0]["name"] == "First"
    assert state["accounts"][1]["name"] == "two@example.com"


def test_build_state_resolves_the_colour_index_to_a_hex(reg):
    """The widget tree sets a CSS colour; it must not have to know that the
    registry stores an index into paths.PALETTE."""
    state = panel.build_state("one")
    assert state["accounts"][0]["color"] == paths.PALETTE[0][1]


def test_build_state_carries_the_three_toggles(reg):
    """They are checkboxes now, so their state is read once up front rather than
    rendered into the text of a row label."""
    registry.set_headless(reg, "one")
    registry.set_field(reg, "one", "dangerous", True)
    registry.save(reg)
    state = panel.build_state("one")
    assert state["headless"] is True
    assert state["dangerous"] is True
    assert state["hide_icon"] is False
    assert state["display"] == "nickname"


def test_build_state_reports_both_usage_windows_in_order(reg):
    """Two bars, always both, always 5h above wk. An absent window still gets a
    row saying so — a bar that vanishes is indistinguishable from one at zero."""
    state = panel.build_state("one")
    assert [w["key"] for w in state["usage"]] == ["five_hour", "seven_day"]
    assert [w["label"] for w in state["usage"]] == ["5h", "wk"]
    assert all(w["kind"] == usage.ABSENT for w in state["usage"])
    assert all(w["resets"] == "" for w in state["usage"])


def test_build_state_reads_a_bounded_window(reg):
    """A future resets_at makes the percentage a live reading, and the reset time
    is the headline — usage.py's rule, unchanged by the new rendering."""
    now = 1_700_000_000.0
    (paths.account_dir("one") / paths.USAGE_FILE).write_text(json.dumps({
        "fetched_at": now, "source": "statusline",
        "five_hour": {"percent": 61.0, "resets_at": int(now + 3600)},
        "seven_day": None,
    }))
    state = panel.build_state("one", now=now)
    five = state["usage"][0]
    assert five["kind"] == usage.BOUNDED
    assert five["percent"] == 61.0
    assert five["resets"] == usage.reset_time(int(now + 3600), now)


def test_build_state_reads_a_rolled_over_window_as_open(reg):
    """A reset in the past means the window rolled over, which is the good state
    and must not be rendered as pressure."""
    now = 1_700_000_000.0
    (paths.account_dir("one") / paths.USAGE_FILE).write_text(json.dumps({
        "fetched_at": now - 99999, "source": "statusline",
        "five_hour": {"percent": 88.0, "resets_at": int(now - 10)},
        "seven_day": None,
    }))
    state = panel.build_state("one", now=now)
    assert state["usage"][0]["kind"] == usage.OPEN


def test_build_state_survives_an_unknown_slug(reg):
    """The bar and the registry can disagree for one click after a removal; the
    panel has to open rather than traceback into a dead bar button."""
    state = panel.build_state("gone")
    assert state["slug"] == "gone"
    assert state["display"] == "nickname"
    assert state["headless"] is False


def _session(root, project, uuid, cwd, title, mtime):
    """Write a transcript history.scan() will pick up, then set its mtime —
    scan() sorts by the file's mtime, not by anything inside it."""
    d = root / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{uuid}.jsonl"
    p.write_text(json.dumps({"cwd": cwd, "aiTitle": title}) + "\n")
    os.utime(p, (mtime, mtime))
    return p


@pytest.fixture
def hist(reg, tmp_path):
    """Three projects, four sessions, interleaved in age so a sort by project
    cannot accidentally pass by preserving scan()'s order."""
    root = tmp_path / "claude"
    now = 1_700_000_000.0
    for name in ("alpha", "beta", "gamma"):
        (tmp_path / name).mkdir(exist_ok=True)
    _session(root, "a", "u1", str(tmp_path / "alpha"), "newest in alpha", now - 60)
    _session(root, "a", "u2", str(tmp_path / "alpha"), "older in alpha", now - 90000)
    _session(root, "b", "u3", str(tmp_path / "beta"), "only in beta", now - 3600)
    _session(root, "c", "u4", str(tmp_path / "gamma"), "only in gamma", now - 200000)
    return now


def test_projects_are_ordered_by_their_most_recent_session(reg, hist):
    """The project you last touched is first, which is what makes the default
    selection right without anything being clicked."""
    state = panel.build_state("one", now=hist)
    assert [p["short"].split("/")[-1] for p in state["projects"]] == \
        ["alpha", "beta", "gamma"]


def test_the_most_recent_project_is_selected_on_open(reg, hist, tmp_path):
    """Zero clicks to see what you were working on."""
    state = panel.build_state("one", now=hist)
    assert state["selected_project"] == str(tmp_path / "alpha")


def test_the_most_recent_session_in_it_is_selected_on_open(reg, hist):
    """One keypress to resume it."""
    state = panel.build_state("one", now=hist)
    assert state["selected_session"] == "u1"


def test_sessions_are_grouped_by_project_most_recent_first(reg, hist, tmp_path):
    """The right pane is one project's sessions, so grouping is the state's job
    — the widget tree must not have to filter a flat list."""
    alpha = panel.build_state("one", now=hist)["sessions"][str(tmp_path / "alpha")]
    assert [s["uuid"] for s in alpha] == ["u1", "u2"]
    assert [s["title"] for s in alpha] == ["newest in alpha", "older in alpha"]


def test_sessions_carry_a_humanised_age(reg, hist):
    """The same column the fzf rows show, as a field rather than inside a padded
    string, because a widget wants to align it itself."""
    state = panel.build_state("one", now=hist)
    assert state["sessions"][state["selected_project"]][0]["age"] == "1m"


def test_projects_carry_the_age_of_their_newest_session(reg, hist):
    """The left column's age is the project's, not the first session scanned."""
    state = panel.build_state("one", now=hist)
    assert [p["age"] for p in state["projects"]] == ["1m", "1.0h", "2d"]


def test_an_untitled_session_gets_a_placeholder_not_none(reg, tmp_path):
    """The widget tree must never have to guard against None in a label."""
    root = tmp_path / "claude"
    now = 1_700_000_000.0
    (tmp_path / "alpha").mkdir(exist_ok=True)
    d = root / "projects" / "a"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "u9.jsonl"
    p.write_text(json.dumps({"cwd": str(tmp_path / "alpha")}) + "\n")
    os.utime(p, (now, now))
    state = panel.build_state("one", now=now)
    assert state["sessions"][str(tmp_path / "alpha")][0]["title"] == "(untitled)"


def test_no_history_leaves_the_selection_empty_rather_than_erroring(reg):
    """A fresh machine has no transcripts and the panel still has to open."""
    state = panel.build_state("one")
    assert state["projects"] == []
    assert state["selected_project"] is None
    assert state["selected_session"] is None


def test_empty_query_restores_everything(reg, hist):
    """Clearing the box must not leave the panes narrowed."""
    state = panel.build_state("one", now=hist)
    assert panel.filter_state(state, "") == state
    assert panel.filter_state(state, "   ") == state


def test_filter_matches_a_session_title(reg, hist):
    """The common case: you remember what it was called."""
    state = panel.filter_state(panel.build_state("one", now=hist), "beta")
    uuids = [s["uuid"] for rows in state["sessions"].values() for s in rows]
    assert uuids == ["u3"]


def test_filter_is_case_insensitive(reg, hist):
    state = panel.filter_state(panel.build_state("one", now=hist), "ONLY IN BETA")
    assert [p["short"].split("/")[-1] for p in state["projects"]] == ["beta"]


def test_filter_matches_a_project_path_and_keeps_its_sessions(reg, hist, tmp_path):
    """Typing a directory name is how the search doubles as a project filter, so
    a path match must not then drop the sessions under it for not matching."""
    state = panel.filter_state(panel.build_state("one", now=hist), "alpha")
    assert [p["path"] for p in state["projects"]] == [str(tmp_path / "alpha")]
    assert [s["uuid"] for s in state["sessions"][str(tmp_path / "alpha")]] == \
        ["u1", "u2"]


def test_filter_narrows_the_project_column_to_projects_with_a_match(reg, hist):
    """B, not A: the search earns its place exactly when you cannot remember
    which project it was in, so the left column has to move too."""
    state = panel.filter_state(panel.build_state("one", now=hist), "gamma")
    assert len(state["projects"]) == 1


def test_filter_moves_the_selection_to_the_best_match(reg, hist, tmp_path):
    """The selection has to follow, or Enter resumes something you filtered
    away."""
    state = panel.filter_state(panel.build_state("one", now=hist), "beta")
    assert state["selected_project"] == str(tmp_path / "beta")
    assert state["selected_session"] == "u3"


def test_filter_with_no_match_empties_both_panes(reg, hist):
    """Empty and honest. There is nothing to select and Enter must do nothing."""
    state = panel.filter_state(panel.build_state("one", now=hist), "zzzz")
    assert state["projects"] == []
    assert state["sessions"] == {}
    assert state["selected_project"] is None
    assert state["selected_session"] is None


def test_filter_keeps_the_project_column_in_recency_order(reg, hist):
    """Filtering narrows; it must not reorder. A match list sorted by relevance
    would put an old project above a new one and break the top-row default."""
    state = panel.filter_state(panel.build_state("one", now=hist), "only")
    assert [p["short"].split("/")[-1] for p in state["projects"]] == \
        ["beta", "gamma"]


def test_filter_does_not_mutate_the_state_it_was_given(reg, hist):
    """The unfiltered state is kept so clearing the box is free, rather than a
    rescan of ~/.claude/projects on every backspace."""
    state = panel.build_state("one", now=hist)
    before = len(state["projects"])
    panel.filter_state(state, "beta")
    assert len(state["projects"]) == before
    assert len(state["sessions"]) == 3


def test_filter_keeps_the_account_half_untouched(reg, hist):
    """Typing in the search must not blank the usage bars or the chip row."""
    state = panel.build_state("one", now=hist)
    narrowed = panel.filter_state(state, "beta")
    assert narrowed["usage"] == state["usage"]
    assert narrowed["accounts"] == state["accounts"]
    assert narrowed["dangerous"] == state["dangerous"]


# ── the split between the tested half and the toolkit half ────────────────────

def _imported_names(path):
    import ast
    import pathlib
    names = set()
    for node in ast.walk(ast.parse(pathlib.Path(path).read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_panel_never_imports_gi():
    """The split is the point: panel.py is testable because it cannot reach a
    toolkit, and one stray convenience import would silently end that."""
    assert "gi" not in _imported_names("ccas/panel.py")


def test_panel_ui_does_not_import_gi_at_module_level():
    """A missing PyGObject must not be able to break `ccs statusline`, which
    runs in every prompt of every session. The import lives inside show()."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path("ccas/panel_ui.py").read_text())
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("gi" in ast.dump(n) for n in top)


def test_cli_imports_with_pygobject_unavailable():
    """ccs list, ccs doctor, ccs statusline and the passthrough all have to keep
    working on stdlib alone — importing the CLI must not pull in GTK."""
    import subprocess
    import sys
    code = ("import sys; sys.modules['gi'] = None; "
            "import ccas.cli, ccas.panel_ui; print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True)
    assert out.stdout.strip() == "ok", out.stderr


def test_show_reports_missing_dependencies_rather_than_dying(monkeypatch, capsys):
    """A bar click that opens nothing and says nothing is the worst available
    failure. Missing PyGObject has to be visible, not silent."""
    import sys as _sys
    from ccas import panel_ui
    monkeypatch.setitem(_sys.modules, "gi", None)
    monkeypatch.setattr(panel_ui.shutil, "which", lambda _n: None)
    assert panel_ui.show({"slug": "one"}) is None
    assert "gtk4-layer-shell" in capsys.readouterr().err


def test_layer_shell_is_loaded_before_gtk(monkeypatch):
    """gtk4-layer-shell must be linked ahead of libwayland or its surfaces
    silently degrade to ordinary windows.

    Measured on the real system 2026-07-26: without this the panel opened as a
    normal toplevel on whichever output the compositor felt like, with
    'GtkWindow is not a layer surface' on stderr and nothing on the bar. A
    ctypes RTLD_GLOBAL load before `import gi` is the fix that needs no
    LD_PRELOAD in the launcher.
    """
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path("ccas/panel_ui.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "show")
    body = ast.dump(fn)
    assert "RTLD_GLOBAL" in body
    assert body.index("RTLD_GLOBAL") < body.index("'gi'"), \
        "the preload must happen before gi is imported"


# ── the open-panel lock ──────────────────────────────────────────────────────
# A bar click is a toggle: the second one closes what the first one opened. That
# needs the panel process to be findable, which is all the lock is for. It is
# not mutual exclusion — two panels at once is not a corruption, just a mess.


@pytest.fixture
def lock(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "panel.lock"))
    return tmp_path / "panel.lock"


def test_claim_records_this_process_and_its_account(lock):
    panel.claim("one")
    assert panel.running_panel() == (os.getpid(), "one")


def test_release_leaves_nothing_behind(lock):
    panel.claim("one")
    panel.release()
    assert panel.running_panel() is None
    assert not lock.exists()


def test_nothing_is_running_without_a_lock(lock):
    assert panel.running_panel() is None
    assert panel.close_running() is None


def test_close_running_names_the_account_it_closed(monkeypatch, lock):
    """The caller compares that slug with its own: same means the click was a
    second click on the same widget and the panel should stay closed, different
    means the user asked for another account and one opens."""
    signalled = []
    monkeypatch.setattr(panel.os, "kill", lambda pid, sig: signalled.append((pid, sig)))
    panel.claim("two")
    assert panel.close_running() == "two"
    # The liveness probe is a kill(pid, 0) of its own; the signal that closes it
    # is the one being pinned here.
    assert (os.getpid(), panel.signal.SIGTERM) in signalled


def test_close_running_clears_the_lock_even_though_the_panel_cannot(monkeypatch, lock):
    """SIGTERM does not run a finally block, so the panel it kills never gets to
    release its own lock. Whoever sent the signal has to."""
    monkeypatch.setattr(panel.os, "kill", lambda pid, sig: None)
    panel.claim("two")
    panel.close_running()
    assert not lock.exists()


def test_a_dead_panel_is_nothing_to_close(monkeypatch, lock):
    """A pid outliving its process would make the widget's first click a no-op
    that closes nothing — the toggle stuck in the off position forever."""
    lock.write_text("999999 one", encoding="utf-8")

    def gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(panel.os, "kill", gone)
    assert panel.running_panel() is None
    assert panel.close_running() is None


def test_a_damaged_lock_is_ignored_rather_than_raised(lock):
    """It lives in a directory anything may write to; a bar click must open the
    panel regardless of what it finds there."""
    lock.write_text("not a pid at all", encoding="utf-8")
    assert panel.running_panel() is None


# ── which output the panel opens on ──────────────────────────────────────────


def _fake_sway(monkeypatch, payload, rc=0):
    class R:
        returncode = rc
        stdout = payload

    monkeypatch.setattr(panel.subprocess, "run", lambda *a, **k: R())


def test_current_output_is_the_one_the_pointer_is_on(monkeypatch):
    """sway focuses the output under the pointer, so the focused one is the
    monitor whose bar was clicked.

    Reported on the real system: clicked on the left screen, opened on the
    right. A layer surface with no monitor set goes wherever the compositor
    likes, and the panel then had the keyboard on a screen the user was not
    looking at — which is also why Escape appeared not to work.
    """
    _fake_sway(monkeypatch, json.dumps([
        {"name": "DP-1", "focused": False},
        {"name": "HDMI-A-1", "focused": True},
    ]))
    assert panel.current_output() == "HDMI-A-1"


def test_current_output_is_none_when_sway_is_not_there(monkeypatch):
    """Another compositor, or none. The panel still has to open."""
    def boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(panel.subprocess, "run", boom)
    assert panel.current_output() is None


def test_current_output_is_none_when_nothing_is_focused(monkeypatch):
    _fake_sway(monkeypatch, json.dumps([{"name": "DP-1", "focused": False}]))
    assert panel.current_output() is None


def test_current_output_survives_junk_from_the_socket(monkeypatch):
    _fake_sway(monkeypatch, "<html>no</html>")
    assert panel.current_output() is None
