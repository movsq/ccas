"""The panel's pure half — everything decided before a widget exists."""
import json
import os

import pytest

from ccas import format as fmt, panel, paths, poll, registry, usage


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


def test_build_state_carries_each_accounts_colour(reg):
    """The widget tree sets a CSS colour, so it gets one — whatever the account
    happens to hold."""
    state = panel.build_state("one")
    assert state["accounts"][0]["color"] == reg["accounts"][0]["color"]


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


def test_refresh_usage_rereads_the_recording_build_state_read(reg):
    """The on-demand poll's half of the panel refresh.

    It re-reads the file rather than being handed the reading, because the
    writer is poll.record_reading on another thread and the only thing the two
    share is the account directory. Same rows build_state puts in state["usage"],
    so the widget update has nothing new to understand.
    """
    now = 1_700_000_000.0
    path = paths.account_dir("one") / paths.USAGE_FILE
    path.write_text(json.dumps({
        "fetched_at": now, "source": "statusline",
        "five_hour": {"percent": 61.0, "resets_at": int(now + 3600)},
        "seven_day": None,
    }))
    state = panel.build_state("one", now=now)
    assert panel.refresh_usage("one", now=now) == state["usage"]

    path.write_text(json.dumps({
        "fetched_at": now, "source": "oauth",
        "five_hour": {"percent": 74.0, "resets_at": int(now + 3600)},
        "seven_day": None,
    }))
    rows = panel.refresh_usage("one", now=now)
    assert rows[0]["percent"] == 74.0
    assert [r["key"] for r in rows] == ["five_hour", "seven_day"]


def _recording(path, now, percent):
    """A poller that writes a reading the way poll_on_demand's fetch would."""
    def poller(slug):
        path.write_text(json.dumps({
            "fetched_at": now, "source": "oauth",
            "five_hour": {"percent": percent, "resets_at": int(now + 3600)},
            "seven_day": None,
        }))
        return poll.Outcome(slug, poll.OK, "")
    return poller


def test_poll_and_refresh_signals_the_bar_when_the_numbers_moved(reg):
    """The rule every other writer obeys: the signal rides on the write. The bar
    re-renders on its own every 30s, but half a minute of the old number right
    after asking for the new one is the staleness this feature is against."""
    now = 1_700_000_000.0
    path = paths.account_dir("one") / paths.USAGE_FILE
    signalled = []
    rows = panel.poll_and_refresh("one", now=now,
                                  poller=_recording(path, now, 42.0),
                                  signaller=signalled.append)
    assert rows[0]["percent"] == 42.0
    assert signalled == [registry.find(reg, "one")["signal"]]


def test_poll_and_refresh_does_not_signal_when_nothing_changed(reg):
    """SKIPPED by the guard, or the same numbers back: no write, no signal."""
    now = 1_700_000_000.0
    signalled = []
    panel.poll_and_refresh("one", now=now,
                           poller=lambda s: poll.Outcome(s, poll.SKIPPED, ""),
                           signaller=signalled.append)
    assert signalled == []


def test_poll_and_refresh_still_answers_rows_when_the_poll_blows_up(reg):
    """It runs on a background thread in the panel process, where an exception
    is a traceback in Waybar's log and nothing else. The panel keeps showing
    what it was showing."""
    now = 1_700_000_000.0
    (paths.account_dir("one") / paths.USAGE_FILE).write_text(json.dumps({
        "fetched_at": now, "source": "statusline",
        "five_hour": {"percent": 61.0, "resets_at": int(now + 3600)},
        "seven_day": None,
    }))

    def exploding(_slug):
        raise RuntimeError("dns went away")

    rows = panel.poll_and_refresh("one", now=now, poller=exploding,
                                  signaller=lambda _n: None)
    assert rows[0]["percent"] == 61.0


def test_build_state_reads_a_rolled_over_window_as_idle(reg):
    """A reset in the past means the window rolled over, which is the good state
    and must not be rendered as pressure."""
    now = 1_700_000_000.0
    (paths.account_dir("one") / paths.USAGE_FILE).write_text(json.dumps({
        "fetched_at": now - 99999, "source": "statusline",
        "five_hour": {"percent": 88.0, "resets_at": int(now - 10)},
        "seven_day": None,
    }))
    state = panel.build_state("one", now=now)
    assert state["usage"][0]["kind"] == usage.IDLE
    assert state["usage"][0]["percent"] == 0.0


def test_build_state_survives_an_unknown_slug(reg):
    """The bar and the registry can disagree for one click after a removal; the
    panel has to open rather than traceback into a dead bar button."""
    state = panel.build_state("gone")
    assert state["slug"] == "gone"
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
    assert panel.running_panel() == (os.getpid(), "one", None)


def test_the_lock_records_which_monitor_the_panel_is_on(lock):
    """Waybar draws every module on every bar, so one slug has as many widgets
    as there are monitors. Without the output in the lock, clicking the same
    account's widget on the other screen is indistinguishable from clicking the
    same widget twice — the panel closed and nothing opened."""
    panel.claim("one", "DP-1")
    assert panel.running_panel() == (os.getpid(), "one", "DP-1")


def test_a_lock_written_without_an_output_still_reads(lock):
    """A panel opened by the previous version, or one whose output nothing could
    name. Unknown is None, and never equal to a real connector — so the click
    reopens rather than dying quietly, which is the failure being fixed."""
    lock.write_text(f"{os.getpid()} one", encoding="utf-8")
    assert panel.running_panel() == (os.getpid(), "one", None)


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
    panel.claim("two", "DP-1")
    assert panel.close_running() == ("two", "DP-1")
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


# ── the layer surface ────────────────────────────────────────────────────────


class FakeLayerShell:
    """Enough of Gtk4LayerShell to record what the panel asks of it.

    The real one needs a Wayland display, which the suite does not have — and
    these four calls are exactly the ones that were wrong on the real system.
    """

    class Layer:
        OVERLAY = "overlay"

    class Edge:
        TOP, BOTTOM, LEFT, RIGHT = "top", "bottom", "left", "right"

    class KeyboardMode:
        NONE, EXCLUSIVE, ON_DEMAND = "none", "exclusive", "on-demand"

    def __init__(self):
        self.anchors = {}
        self.keyboard = None
        self.monitor = None
        self.exclusive_zone = None

    def init_for_window(self, _w):
        pass

    def set_layer(self, _w, _layer):
        pass

    def set_anchor(self, _w, edge, on):
        self.anchors[edge] = on

    def set_keyboard_mode(self, _w, mode):
        self.keyboard = mode

    def set_exclusive_zone(self, _w, zone):
        self.exclusive_zone = zone

    def set_monitor(self, _w, monitor):
        self.monitor = monitor


class FakeMonitors:
    def __init__(self, names):
        self.names = names

    def get_n_items(self):
        return len(self.names)

    def get_item(self, i):
        name = self.names[i]
        return type("M", (), {"get_connector": lambda _s, n=name: n})()


class FakeWindow:
    def __init__(self, names):
        self.names = names

    def get_display(self):
        return type("D", (), {"get_monitors": lambda _s: FakeMonitors(self.names)})()


def test_the_panel_covers_the_whole_output(monkeypatch):
    """Anchored to all four edges, so a click anywhere but the panel itself is
    still a click on the panel's surface.

    That is what makes 'click outside to close' possible without the click
    reaching what is underneath — the reason it must not, in the user's words,
    be able to unpause a video.
    """
    from ccas import panel_ui
    monkeypatch.setattr(panel_ui.panel, "current_output", lambda: None)
    shell = FakeLayerShell()
    panel_ui._setup_layer(FakeWindow([]), shell)
    assert shell.anchors == {"top": True, "bottom": True,
                             "left": True, "right": True}


def test_the_panel_leaves_the_bar_reachable(monkeypatch):
    """Exclusive zone 0 keeps the surface out of the space Waybar reserved, so
    the widget that opened the panel can still be clicked to close it."""
    from ccas import panel_ui
    monkeypatch.setattr(panel_ui.panel, "current_output", lambda: None)
    shell = FakeLayerShell()
    panel_ui._setup_layer(FakeWindow([]), shell)
    assert shell.exclusive_zone == 0


def test_the_panel_takes_the_keyboard_without_being_clicked(monkeypatch):
    """Escape has to close it whether or not it was ever focused.

    ON_DEMAND gives the keyboard only once the surface is clicked, so a panel
    that opened on the other monitor swallowed nothing and Escape went to
    whatever was focused instead — reported as 'escape works inconsistently'.
    """
    from ccas import panel_ui
    monkeypatch.setattr(panel_ui.panel, "current_output", lambda: None)
    shell = FakeLayerShell()
    panel_ui._setup_layer(FakeWindow([]), shell)
    assert shell.keyboard == FakeLayerShell.KeyboardMode.EXCLUSIVE


def test_the_panel_opens_on_the_output_the_pointer_is_on(monkeypatch):
    """Clicked on the left screen, opened on the right — because nothing told
    the compositor which one was meant."""
    monkeypatch.delenv("CCAS_PANEL_OUTPUT", raising=False)
    monkeypatch.setattr(panel, "current_output",
                        lambda: pytest.fail("the probe answered; do not guess"))
    assert panel.choose_output("DP-1") == "DP-1"


def test_an_explicit_output_wins_over_the_pointer(monkeypatch):
    """CCAS_PANEL_OUTPUT is the user pinning it; the pointer is only a guess."""
    monkeypatch.setenv("CCAS_PANEL_OUTPUT", "HDMI-A-1")
    assert panel.choose_output("DP-1") == "HDMI-A-1"


def test_the_focused_output_is_only_reached_when_the_probe_says_nothing(monkeypatch):
    """It is the worst answer available and is kept only because it beats none:
    with `focus_follows_mouse no` it names the monitor holding the focused
    *window*, which is reliably not the one whose bar was clicked."""
    monkeypatch.delenv("CCAS_PANEL_OUTPUT", raising=False)
    monkeypatch.setattr(panel, "current_output", lambda: "HDMI-A-1")
    assert panel.choose_output(None) == "HDMI-A-1"


def test_an_output_that_is_not_connected_is_not_an_error(monkeypatch):
    """A monitor list changes when a cable does. A panel that refuses to open is
    worse than one on the wrong screen."""
    from ccas import panel_ui
    monkeypatch.setenv("CCAS_PANEL_OUTPUT", "DVI-9")
    shell = FakeLayerShell()
    panel_ui._pin_to_output(FakeWindow(["DP-1"]), shell)
    assert shell.monitor is None


def test_the_probe_answers_on_the_first_enter_rather_than_on_the_timeout():
    """The pointer enter arrives about 5 ms after the probes map; PROBE_MS is
    120. Waiting the whole timeout out spent that difference on every open, and
    it was the single largest part of the click-to-panel delay (measured
    2026-07-26: 360 ms total, 120 of it here). The timeout is now the fallback
    for the case it was written for — no pointer on any probe at all."""
    from ccas import panel_ui
    seen = []
    resolve = panel_ui._once(seen.append)
    resolve("DP-1")          # the enter event
    resolve(None)            # the timeout, firing later anyway
    assert seen == ["DP-1"]


def test_the_probe_still_answers_when_no_pointer_is_found():
    """An idle output sends no wl_pointer.enter — the case that made the probe a
    timeout in the first place. None means 'let _pin_to_output decide'."""
    from ccas import panel_ui
    seen = []
    panel_ui._once(seen.append)(None)
    assert seen == [None]


def test_a_pinned_output_skips_the_probe_entirely(monkeypatch):
    """CCAS_PANEL_OUTPUT wins in _pin_to_output regardless, so probing for an
    answer that is about to be discarded is 120 ms of pure delay — and it is the
    path agents drive the panel from."""
    from ccas import panel_ui
    monkeypatch.setenv("CCAS_PANEL_OUTPUT", "HDMI-A-1")
    assert panel_ui._skip_probe() is True
    monkeypatch.delenv("CCAS_PANEL_OUTPUT")
    assert panel_ui._skip_probe() is False


def test_the_panel_picks_the_cairo_renderer_before_gtk_is_imported():
    """GSK's default renderer here is vulkan, and initialising it costs 180 ms
    on the panel's first surface — measured against cairo's 2 ms, on a widget
    tree that never animates. Set after `import gi` it would be too late, so it
    sits with the RTLD_GLOBAL load, ahead of it."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path("ccas/panel_ui.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "show")
    body = ast.dump(fn)
    assert "GSK_RENDERER" in body
    assert body.index("GSK_RENDERER") < body.index("'gi'")


def test_a_second_panel_process_is_not_handed_to_the_first(monkeypatch):
    """NON_UNIQUE, and it is the whole of 'the old one closed and no new one
    opened'.

    A Gtk.Application with an application_id is single-instance: while another
    process still holds the id, app.run() does not start a main loop at all — it
    sends `activate` to the process that has it and returns. So a click arriving
    while the previous panel was still alive built nothing, returned None, and
    released the lock on its way out, while the *old* panel quietly re-ran its
    activate handler. Measured 2026-07-26: one pid logged five probes, one per
    click, and none of the five clicks produced a panel.

    It is a race in any ordering — SIGTERM does not make a process gone by the
    time the next one reaches GTK — and closing the old panel after the probe
    rather than before it makes the overlap the normal case.
    """
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path("ccas/panel_ui.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "show")
    assert "NON_UNIQUE" in ast.dump(fn)


def test_an_explicit_renderer_is_the_users_to_choose(monkeypatch):
    """setdefault, not assignment: GSK_RENDERER in the environment is someone
    debugging their own graphics stack, and we are not the ones to overrule it."""
    import sys as _sys
    from ccas import panel_ui
    monkeypatch.setenv("GSK_RENDERER", "vulkan")
    monkeypatch.setitem(_sys.modules, "gi", None)
    monkeypatch.setattr(panel_ui.shutil, "which", lambda _n: None)
    panel_ui.show({"slug": "one"})
    assert panel_ui.os.environ["GSK_RENDERER"] == "vulkan"


def test_a_click_is_outside_the_panel_or_it_is_not():
    """The scrim covers the output, so 'outside' is a hit test rather than a
    surface boundary the compositor can answer for us."""
    from ccas import panel_ui
    rect = type("R", (), {"origin": type("P", (), {"x": 100, "y": 50})(),
                          "size": type("S", (), {"width": 200, "height": 100})()})()
    assert panel_ui._inside(rect, 150, 80)
    assert panel_ui._inside(rect, 100, 50)
    assert not panel_ui._inside(rect, 99, 80)
    assert not panel_ui._inside(rect, 150, 151)


def test_the_output_is_asked_for_explicitly_when_known(monkeypatch):
    """The pointer's monitor is discovered by probing, not by asking sway —
    `focus_follows_mouse no` means the focused output is the one holding the
    focused *window*, which is exactly the wrong answer."""
    from ccas import panel_ui
    monkeypatch.delenv("CCAS_PANEL_OUTPUT", raising=False)
    monkeypatch.setattr(panel_ui.panel, "current_output",
                        lambda: pytest.fail("a known output must not be second-guessed"))
    shell = FakeLayerShell()
    panel_ui._pin_to_output(FakeWindow(["DP-1", "HDMI-A-1"]), shell, "HDMI-A-1")
    assert shell.monitor.get_connector() == "HDMI-A-1"


def test_the_probe_outlasts_a_held_mouse_button(monkeypatch):
    """Waybar spawns the command on button *press*, and sway holds an implicit
    pointer grab for as long as the button is down — during which a surface that
    maps under the cursor is told nothing at all. So a click held longer than
    the timeout got no `enter`, fell through to the focused output and opened on
    the wrong monitor (measured 2026-07-26: 120 ms timeout, 1.2 s hold, panel on
    HDMI-A-1 with the pointer on DP-1).

    The grab always ends, and the `enter` arrives the instant it does — so the
    timeout has to outlast a human holding a mouse button, not a repaint.
    """
    from ccas import panel_ui
    assert panel_ui.PROBE_MS >= 1000


# ── the custom format ─────────────────────────────────────────────────────────

def test_build_state_carries_the_format_and_its_targets(reg):
    """The colour targets are precomputed here so panel_ui never parses a
    format string — the widget tree renders what it is handed and decides
    nothing. color_targets replaced format_tokens with its last reader."""
    registry.set_field(reg, "one", "format", "%email %name %email %5h")
    registry.save(reg)
    state = panel.build_state("one")
    assert state["format"] == "%email %name %email %5h"
    assert [t["token"] for t in state["color_targets"]] == \
        [None, "%email", "%name", "%5h"]


def test_build_state_defaults_the_format_for_an_old_account(reg):
    """An account written before the field existed is read, not migrated."""
    account = registry.find(reg, "one")
    del account["format"], account["format_colors"]
    registry.save(reg)
    state = panel.build_state("one")
    assert state["format"] == fmt.DEFAULT_FORMAT
    assert [t["token"] for t in state["color_targets"]][1:] == \
        fmt.tokens_in(fmt.DEFAULT_FORMAT)


def test_a_setting_is_applied_without_shutting_the_panel():
    """Ticking 'hide the icon' used to end the panel: every button went through
    the one Action the panel returns, so a setting and a launch closed alike.
    The settings kinds are applied in place instead — you came to change several
    things, and reopening the panel per tick is not how that reads."""
    from ccas import panel
    for kind in ("headless", "dangerous", "hide_icon",
                 "color", "format_color", "format"):
        assert kind in panel.STAYS_OPEN
    # A launch and anything that spawns a terminal still end it: the panel has
    # handed the screen to something else.
    for kind in ("new", "resume", "add", "rename", "remove"):
        assert kind not in panel.STAYS_OPEN


def test_a_switch_is_applied_without_shutting_the_panel():
    """It was a departure while it handed the screen to another panel process.
    The body is swapped inside the one window now, so it is the same window
    showing another account — which is what STAYS_OPEN means."""
    from ccas import panel
    assert "switch" in panel.STAYS_OPEN


def test_the_panel_is_given_a_way_to_apply_a_setting(monkeypatch):
    """cmd_panel passes dispatch_panel down as `apply`, so the widget tree can
    run a settings action without the show() call returning."""
    from ccas import cli, panel, panel_ui
    seen = {}

    def fake_show(state, gate=None, apply=None):
        seen["apply"] = apply
        return None

    monkeypatch.setattr(panel_ui, "show", fake_show)
    monkeypatch.setattr(panel, "close_running", lambda: None)
    monkeypatch.setattr(panel, "claim", lambda *a, **k: None)
    monkeypatch.setattr(panel, "release", lambda: None)
    monkeypatch.setattr(panel, "build_state", lambda slug: {"slug": slug})
    cli.cmd_panel("one")
    assert seen["apply"] is cli.dispatch_panel


def test_build_state_has_no_display(reg):
    assert "display" not in panel.build_state("one")


def test_stays_open_does_not_carry_display():
    assert "display" not in panel.STAYS_OPEN


def test_the_first_colour_target_is_the_icon():
    """It paints the glyph on the bar and the dot in the panel's account list.
    It was called `the widget` while it could reach neither — the account had
    no token pointing at it, so the slider moved nothing at all."""
    account = {"color": "#89b4fa", "format": "%name", "format_colors": {}}
    targets = panel.color_targets(account)
    assert targets[0] == {"token": None, "label": "icon", "color": "#89b4fa"}


def test_color_targets_then_follow_the_format_string():
    """Left to right the way the label reads, de-duplicated, and only the
    tokens this account actually uses — a target for a token that is not on the
    bar is a setting that does nothing."""
    account = {"color": "#89b4fa", "format": "%email %name %email",
               "format_colors": {}}
    assert [t["token"] for t in panel.color_targets(account)] == \
        [None, "%email", "%name"]


def test_an_unrecognised_token_colour_seeds_the_default_not_the_account():
    """Seeding from the account colour was defensible while `account` existed.
    Now it puts a colour in the sliders that has nothing to do with the token,
    and the settle timer then writes it."""
    for retired in ("auto", "dim", "account"):
        account = {"color": "#89b4fa", "format": "%name",
                   "format_colors": {"%name": retired}}
        token = next(t for t in panel.color_targets(account)
                     if t["token"] == "%name")
        assert token["color"] == fmt.DEFAULT_COLOR


def test_a_token_with_a_hex_seeds_the_sliders_with_it():
    account = {"color": "#f38ba8", "format": "%name",
               "format_colors": {"%name": "#123abc"}}
    assert panel.color_targets(account)[1]["color"] == "#123abc"


def test_build_state_carries_the_targets(reg):
    state = panel.build_state("one")
    assert state["color_targets"][0]["token"] is None


def test_color_targets_are_labelled_by_name_not_by_token():
    """`%5hused` is what you type into a format string, not the name of the
    thing being coloured; the chip row read as syntax. The token still travels
    in the entry, so what gets written is unchanged."""
    account = {"color": "#89b4fa", "format": "%email %5hused",
               "format_colors": {}}
    targets = panel.color_targets(account)
    assert [t["label"] for t in targets] == \
        ["icon", fmt.NAMES["%email"], fmt.NAMES["%5hused"]]
    assert [t["token"] for t in targets] == [None, "%email", "%5hused"]


def test_the_verbs_name_the_project_they_would_act_in():
    """Both buttons launch into the selected project, so both say which one.
    "Resume last" named no target at all — the one thing you want to know
    before pressing a button that opens a session somewhere."""
    project = {"path": "/home/x/ccas", "short": "~/ccas"}
    assert panel.verb_labels(project) == (
        "New session in ~/ccas", "Resume last session (~/ccas)")


def test_the_verbs_drop_the_project_when_there_is_none():
    """Nothing selected — a fresh account with no history. A parenthesis with
    nothing in it reads as a bug."""
    assert panel.verb_labels(None) == ("New session", "Resume last session")


def test_the_previewer_renders_the_typed_text_not_the_stored_format(reg):
    """The panel's format entry previews what is being typed. Reading the
    stored format instead would show the label you are trying to leave."""
    registry.set_field(reg, "one", "format", "%name")
    registry.save(reg)
    preview = panel.format_previewer("one")
    markup, _unknown = preview("%email")
    assert "one@example.com" in markup
    assert "First" not in markup


def test_the_previewer_answers_the_unknown_tokens_of_the_typed_text(reg):
    """Warned about under the entry, never rejected: an unknown token renders
    as its own name on the bar, which is visible and self-explaining."""
    preview = panel.format_previewer("one")
    markup, unknown = preview("%name %bogus")
    assert unknown == ["%bogus"]
    assert "%bogus" in markup


def test_the_previewer_carries_the_token_colours_already_set(reg):
    """The preview is the widget, not the format string: a colour set on a
    token is the only preview of that colour there is."""
    registry.set_field(reg, "one", "format_colors", {"%email": "#89b4fa"})
    registry.save(reg)
    markup, _unknown = panel.format_previewer("one")("%email")
    assert "#89b4fa" in markup


def test_the_previewer_reads_the_account_once(reg, monkeypatch):
    """Built once per editor, called per keystroke. A registry read per
    keystroke is the thing this shape exists to avoid."""
    reads = []
    real = panel.registry.load
    monkeypatch.setattr(panel.registry, "load",
                        lambda: reads.append(1) or real())
    preview = panel.format_previewer("one")
    for text in ("%n", "%na", "%name"):
        preview(text)
    assert len(reads) == 1


def test_the_previewer_tolerates_an_unknown_slug(reg):
    """build_state() tolerates one — the bar and the registry can disagree for
    one click after an account is removed — and this must not be the thing
    that tracebacks instead."""
    markup, unknown = panel.format_previewer("gone")("%name")
    assert unknown == []
    assert isinstance(markup, str)


def test_a_format_change_rebuilds_the_colour_chips():
    """The chips are one per token in the format string, so the set of things
    you can colour changed. Without the rebuild, adding %7dused leaves no way
    to colour it until the panel is reopened. The sliders are exempt for the
    opposite reason — a rebuild mid-drag loses the grab."""
    assert "format" not in panel.NO_REBUILD
    assert "color" in panel.NO_REBUILD
    assert "format_color" in panel.NO_REBUILD
