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
