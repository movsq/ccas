"""The colour editor's commit path.

The one part of panel_ui that decides something. It is built here directly and
driven by emitting the signals a pointer would emit — no window is ever mapped,
so this needs a display but not a compositor's attention, and it touches no
state at all: `_build_color_editor` reads the dict it is handed and writes
through the `pick` callback it is handed.
"""
import warnings

import pytest

import ccas.format as fmt
import ccas.panel as panel
import ccas.panel_ui as panel_ui
import ccas.usage as usage

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


@pytest.fixture
def gtk():
    """GTK4, or skip. PyGObject is the panel's dependency and not CCAS's — two
    tests in test_panel.py exist to keep it off `ccs statusline`'s path."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    if not Gtk.init_check():
        pytest.skip("no display")
    return Gtk


TARGETS = [
    {"token": None, "label": "icon", "color": "#c4c4ca"},
    {"token": "%5hreset", "label": "%5hreset", "color": "#c7c7c7"},
    {"token": "%5hquotaleft", "label": "%5hquotaleft", "color": "#89b4fa"},
]


def _editor(gtk, monkeypatch):
    """(chips, hue, sat, picked, ui_state). SETTLE_MS is shortened because the
    real 300 is a human's pause, and every test that waits out a timer pays it."""
    monkeypatch.setattr(panel_ui, "SETTLE_MS", 10)
    picked, ui_state = [], {"target": 0, "expanded": True}
    box = panel_ui._build_color_editor(
        {"slug": "vsed", "color_targets": TARGETS}, picked.append, ui_state)

    chips, scales = [], []

    def walk(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, gtk.Scale):
                scales.append(child)
            if isinstance(child, gtk.Button) and \
                    child.has_css_class("ccas-color-chip"):
                chips.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(box)
    return chips, scales[0], scales[1], picked, ui_state


def _settle(ms=80):
    from gi.repository import GLib
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, lambda: (loop.quit(), False)[1])
    loop.run()


def test_a_settled_drag_writes_the_selected_target(gtk, monkeypatch):
    """The null case for the two below: left alone, a drag does commit."""
    chips, hue, sat, picked, _ = _editor(gtk, monkeypatch)
    hue.set_value(200.0)
    assert picked == []          # never per motion event
    _settle()
    assert picked == [panel.Action("color", "vsed",
                                   fmt.hs_to_hex(200.0, sat.get_value()))]


def test_picking_another_chip_commits_the_drag_it_interrupts(gtk, monkeypatch):
    """The bug: choosing a target seeds the sliders, seeding cancels the settle
    timer, and the seeding flag then stops it being rearmed — so a drag the user
    had finished was thrown away, silently, if they clicked another chip inside
    SETTLE_MS. Pre-existing, but a chip is one click beside the slider where the
    dropdown was a popup and two.

    It commits against the target that was selected *while* the sliders moved,
    which is why the flush happens before ui_state["target"] changes.
    """
    chips, hue, sat, picked, ui_state = _editor(gtk, monkeypatch)
    hue.set_value(200.0)
    dragged = fmt.hs_to_hex(200.0, sat.get_value())
    chips[2].emit("clicked")

    assert picked == [panel.Action("color", "vsed", dragged)]
    _settle()
    # And exactly once: the flushed timer must not also fire against the chip
    # that has just been picked.
    assert picked == [panel.Action("color", "vsed", dragged)]
    assert ui_state["target"] == 2


def test_picking_a_chip_without_a_drag_writes_nothing(gtk, monkeypatch):
    """Seeding is not choosing. The separation this rests on is the reason
    seed() and _commit() are two functions, and flushing a *pending* drag must
    not blur it — moving through the chips writes nothing at all."""
    chips, hue, sat, picked, ui_state = _editor(gtk, monkeypatch)
    for index in (1, 2, 0):
        chips[index].emit("clicked")
    _settle()
    assert picked == []
    assert ui_state["target"] == 0


def test_switching_target_does_not_remove_a_dead_timer(gtk, monkeypatch):
    """`on_value` removed the source without clearing it, so the second scale
    seed() moves emitted value-changed and removed the same id again. Harmless
    as a warning; GLib recycles source ids, so the stale one can eventually
    name a timer belonging to someone else.

    PyGObject routes a GLib warning into Python's warnings, not to stderr, so
    this catches rather than captures.
    """
    chips, hue, sat, picked, _ = _editor(gtk, monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        hue.set_value(200.0)
        chips[2].emit("clicked")
        _settle()
    assert not [w for w in caught if "Source ID" in str(w.message)]


def test_usage_text_separates_an_idle_window_from_an_unknown_one():
    """"no data" belongs to the account nothing has ever recorded for, where
    doctor's hook and timer checks are the answer. It was also what an account
    idle past its reset got, which is not missing data — it is a measured empty
    window. Pure string work: no GTK fixture, since _usage_text builds no widget.
    """
    idle = {"kind": usage.IDLE, "percent": 0.0, "resets": ""}
    absent = {"kind": usage.ABSENT, "percent": None, "resets": ""}
    bounded = {"kind": usage.BOUNDED, "percent": 61.0, "resets": "19:30"}
    assert panel_ui._usage_text(idle) == "0% used"
    assert panel_ui._usage_text(absent) == "no data"
    assert panel_ui._usage_text(bounded) == "≥61%  clears 19:30"


def _format_editor(gtk, monkeypatch, stored="%email %5hused", ui_state=None):
    """(entry, chips, picked, ui_state). No window is mapped: the editor reads
    the dict it is handed and writes through the pick callback it is handed."""
    monkeypatch.setattr(panel, "format_previewer",
                        lambda slug, now=None:
                        lambda text: (f"<span>{text}</span>",
                                      fmt.unknown_tokens(text)))
    picked = []
    ui_state = {"target": 0, "expanded": True} if ui_state is None else ui_state
    box = panel_ui._build_format_editor(
        {"slug": "vsed", "format": stored}, picked.append, ui_state)

    entries, chips = [], []

    def walk(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, gtk.Entry):
                entries.append(child)
            if isinstance(child, gtk.Button) and \
                    child.has_css_class("ccas-token-chip"):
                chips.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(box)
    return entries[0], chips, picked, ui_state


def _leave(entry):
    """The focus-out a click elsewhere would deliver. No window is mapped, so
    the controller is emitted on directly rather than moving a real focus."""
    controllers = entry.observe_controllers()
    for index in range(controllers.get_n_items()):
        controller = controllers.get_item(index)
        if isinstance(controller, Gtk.EventControllerFocus):
            controller.emit("leave")
            return
    raise AssertionError("no focus controller on the format entry")


def test_the_format_entry_is_seeded_with_the_stored_format(gtk, monkeypatch):
    entry, _chips, _picked, _ui = _format_editor(gtk, monkeypatch,
                                                 stored="%name %7dused")
    assert entry.get_text() == "%name %7dused"


def test_enter_commits_the_typed_format(gtk, monkeypatch):
    entry, _chips, picked, _ui = _format_editor(gtk, monkeypatch)
    entry.set_text("%name %5hused")
    assert picked == []          # never per keystroke
    entry.emit("activate")
    assert picked == [panel.Action("format", "vsed", "%name %5hused")]


def test_focus_out_commits_the_typed_format(gtk, monkeypatch):
    """The safety net for a change that is finished but not entered."""
    entry, _chips, picked, _ui = _format_editor(gtk, monkeypatch)
    entry.set_text("%name")
    _leave(entry)
    assert picked == [panel.Action("format", "vsed", "%name")]


def test_an_unchanged_entry_writes_nothing(gtk, monkeypatch):
    """Opening the drawer, clicking into the entry and clicking out again is
    not a change — write only on a change, with the signal riding on it."""
    entry, _chips, picked, _ui = _format_editor(gtk, monkeypatch)
    _leave(entry)
    entry.emit("activate")
    assert picked == []


def test_a_committed_format_is_not_committed_twice(gtk, monkeypatch):
    """Enter, then the focus-out that follows reaching for another widget."""
    entry, _chips, picked, _ui = _format_editor(gtk, monkeypatch)
    entry.set_text("%name")
    entry.emit("activate")
    _leave(entry)
    assert picked == [panel.Action("format", "vsed", "%name")]


def test_a_token_chip_inserts_at_the_caret(gtk, monkeypatch):
    entry, chips, _picked, _ui = _format_editor(gtk, monkeypatch,
                                                stored="%name ")
    entry.set_position(6)
    chip = next(c for c in chips if c.get_child().get_label() == "email")
    chip.emit("clicked")
    assert entry.get_text() == "%name %email"


def test_a_token_chip_commits_what_it_inserted(gtk, monkeypatch):
    """A whole token, not a half-typed one — so there is nothing to protect by
    waiting, and the colour chip row is built from the *stored* format. Left
    uncommitted, a token added here had no colour chip until the user clicked
    to the other account and back.

    The chips stay unfocusable all the same: a focus-out would commit the same
    text a second time from inside the click that just committed it.
    """
    entry, chips, picked, ui = _format_editor(gtk, monkeypatch, stored="%name")
    for chip in chips:
        assert chip.get_focusable() is False
    entry.set_position(-1)
    chip = next(c for c in chips if c.get_child().get_label() == "email")
    chip.emit("clicked")
    assert picked == [panel.Action("format", "vsed", "%name%email")]
    assert ui["format_caret"] == len("%name%email")


def test_a_commit_asks_for_the_focus_back(gtk, monkeypatch):
    """The rebuild that follows destroys this entry. ui_state survives it, the
    same way the selected colour target does."""
    entry, _chips, _picked, ui_state = _format_editor(gtk, monkeypatch)
    entry.set_text("%name")
    entry.emit("activate")
    assert ui_state["focus_format"] is True


def test_the_rebuilt_editor_takes_the_focus_flag_back(gtk, monkeypatch):
    """Read once and cleared, or every later rebuild — a ticked checkbox — would
    yank the focus into the format entry."""
    ui_state = {"target": 0, "expanded": True, "focus_format": True}
    _entry, _chips, _picked, ui_state = _format_editor(gtk, monkeypatch,
                                                       ui_state=ui_state)
    assert ui_state["focus_format"] is False


def test_the_widget_settings_carry_a_heading(gtk, monkeypatch):
    """A bare text field between two checkboxes says nothing about what it
    belongs to. The heading covers the format editor and the colour chips —
    they are one subject: what the widget says and how it is coloured."""
    monkeypatch.setattr(panel, "format_previewer",
                        lambda slug, now=None: lambda text: (text, []))
    _visible, hidden = panel_ui._build_toggles(
        {"slug": "vsed", "format": "%name", "headless": False,
         "dangerous": False, "hide_icon": False,
         "color_targets": TARGETS},
        lambda _a: None, {"target": 0, "expanded": True})

    found = []

    def walk(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, gtk.Label):
                found.append(child.get_label())
            walk(child)
            child = child.get_next_sibling()

    walk(hidden)
    assert "Bar label" in found
    assert "What this account's widget says, and how it is coloured" in found


# ── switching account inside the one window ───────────────────────────────────

PROJECTS = [{"path": "/p/alpha", "short": "p/alpha", "age": "1m", "mtime": 2.0},
            {"path": "/p/beta", "short": "p/beta", "age": "2h", "mtime": 1.0}]
SESSIONS = {
    "/p/alpha": [{"uuid": "u1", "title": "newest", "age": "1m",
                  "mtime": 2.0, "cwd": "/p/alpha"},
                 {"uuid": "u2", "title": "older", "age": "3h",
                  "mtime": 1.5, "cwd": "/p/alpha"}],
    "/p/beta": [{"uuid": "u3", "title": "only in beta", "age": "2h",
                 "mtime": 1.0, "cwd": "/p/beta"}],
}


def _state(slug):
    """What panel.build_state() answers, for two accounts and three sessions.

    The panes are deliberately identical whichever account is current: they are
    not per-account at all — history.scan() reads ~/.claude/projects — which is
    why a switch keeps the user's place in them rather than resetting it.
    """
    return {
        "slug": slug,
        "accounts": [{"slug": s, "name": s.upper(),
                      "email": f"{s}@example.com", "color": "#89b4fa",
                      "current": s == slug} for s in ("one", "two")],
        "format": "%name", "format_colors": {},
        "color_targets": [{"token": None, "label": "icon", "color": "#89b4fa"}],
        "hide_icon": False, "headless": False, "dangerous": False,
        "usage": [{"key": "five_hour", "label": "5h", "kind": usage.ABSENT,
                   "percent": None, "resets": "", "color": None}],
        "projects": PROJECTS, "sessions": SESSIONS,
        "selected_project": "/p/alpha", "selected_session": "u1",
    }


def _walk(widget, want):
    """Every descendant `want` says yes to, in tree order."""
    found = []
    child = widget.get_first_child()
    while child is not None:
        if want(child):
            found.append(child)
        found.extend(_walk(child, want))
        child = child.get_next_sibling()
    return found


def _chips(frame):
    return _walk(frame, lambda w: isinstance(w, Gtk.Button)
                 and w.has_css_class("ccas-chip"))


def _current_account(frame):
    """The name on the chip marked current — which account the body is showing."""
    chip = next(c for c in _chips(frame) if c.has_css_class("current"))
    return _walk(chip, lambda w: isinstance(w, Gtk.Label))[0].get_label()


def _body(gtk, monkeypatch, ui_state=None, rebuild=True):
    """(frame, chosen, applied, ui_state). A real body in a real frame, with no
    window mapped: nothing here needs a surface, only a display."""
    monkeypatch.setattr(panel, "format_previewer",
                        lambda slug, now=None: lambda text: (text, []))
    monkeypatch.setattr(panel, "build_state", lambda slug, now=None: _state(slug))
    chosen, applied = {"action": None}, []
    window = gtk.Window()
    frame = gtk.Box(orientation=gtk.Orientation.VERTICAL)
    ui_state = {"expanded": False, "target": 0} if ui_state is None else ui_state
    swap = (panel_ui._body_swapper(frame, chosen, window, applied.append,
                                   ui_state) if rebuild else None)
    frame.append(panel_ui.build_body(_state("one"), chosen, window,
                                     applied.append, ui_state, swap))
    return frame, chosen, applied, ui_state


def test_a_chip_click_swaps_the_body_and_keeps_the_window(gtk, monkeypatch):
    """The whole change, in one test. Switching used to close the window and
    have cmd_panel open a new application on the next account — a new layer
    surface for a change of one dict. The frame holds one child and everything
    account-shaped is inside it, so the switch is a swap of that child."""
    frame, chosen, applied, _ui = _body(gtk, monkeypatch)
    assert _current_account(frame) == "ONE"
    other = next(c for c in _chips(frame) if not c.has_css_class("current"))
    other.emit("clicked")
    assert applied == [panel.Action("switch", "two", None)]
    _settle()
    assert _current_account(frame) == "TWO"
    assert chosen["action"] is None      # the window was never asked to close


def test_the_swapped_body_replaces_the_old_one(gtk, monkeypatch):
    """One child, not two stacked: the frame is a fixed WIDTH, HEIGHT card, so a
    second body in it is not a panel with two bodies — it is a panel with half
    of each."""
    frame, _chosen, _applied, ui_state = _body(gtk, monkeypatch)
    panel_ui._body_swapper(frame, {"action": None}, gtk.Window(),
                           lambda _a: None, ui_state)("two")
    children = _walk(frame, lambda w: w.get_parent() is frame)
    assert len(children) == 1
    assert _current_account(frame) == "TWO"


def test_a_switch_still_closes_a_panel_that_cannot_swap(gtk, monkeypatch):
    """show()'s bare contract: with no way to rebuild the body, a switch is a
    departure like any other and comes back as the Action it always was."""
    frame, chosen, applied, _ui = _body(gtk, monkeypatch, rebuild=False)
    next(c for c in _chips(frame) if not c.has_css_class("current")).emit("clicked")
    assert applied == []
    assert chosen["action"] == panel.Action("switch", "two", None)


def test_a_switch_resets_the_colour_target(gtk, monkeypatch):
    """The chip row is one chip per token in *this* account's format string, so
    index 2 names a different token either side of a switch. `icon` is the one
    target every account has."""
    frame, _chosen, _applied, ui_state = _body(gtk, monkeypatch)
    ui_state["target"] = 1
    ui_state["focus_format"] = True
    panel_ui._body_swapper(frame, {"action": None}, gtk.Window(),
                           lambda _a: None, ui_state)("two")
    assert ui_state["target"] == 0
    assert "focus_format" not in ui_state


def test_the_swapped_out_body_lets_go_of_its_rows(gtk, monkeypatch):
    """A GtkListBox announces row-selected as it is disposed, and a body that
    has been swapped out is disposed whenever the GC reaches it — by which time
    its handlers' closure cells are cleared and the announcement is a NameError
    traceback in Waybar's log. Unselecting while the body is still alive leaves
    the disposal nothing to say. Measured: one traceback per discarded body,
    with an explicit gc.collect() after a swap, and none without the swap."""
    frame, _chosen, _applied, ui_state = _body(gtk, monkeypatch)
    old = frame.get_first_child()
    assert any(b.get_selected_row() is not None for b in panel_ui._list_boxes(old))
    panel_ui._body_swapper(frame, {"action": None}, gtk.Window(),
                           lambda _a: None, ui_state)("two")
    assert all(b.get_selected_row() is None for b in panel_ui._list_boxes(old))


# ── what a switch keeps ───────────────────────────────────────────────────────

def _search(frame):
    return _walk(frame, lambda w: isinstance(w, Gtk.SearchEntry))[0]


def _panes(frame):
    """(projects, sessions). Two ListBoxes, in the order they are built."""
    body = frame.get_first_child()
    projects, sessions = panel_ui._list_boxes(body)
    return projects, sessions


def _selected(listbox, rows):
    row = listbox.get_selected_row()
    return None if row is None else rows[row.get_index()]


def test_the_drawer_survives_a_switch(gtk, monkeypatch):
    """The chips are in the header, which stays on screen while the drawer is
    out — so switching account mid-edit is a reachable click, and having the
    drawer slam shut is the complaint that put the settings in place rather
    than behind a reopen."""
    frame, _chosen, _applied, ui_state = _body(
        gtk, monkeypatch, ui_state={"expanded": True, "target": 0})
    panel_ui._body_swapper(frame, {"action": None}, gtk.Window(),
                           lambda _a: None, ui_state)("two")
    revealer = _walk(frame, lambda w: isinstance(w, Gtk.Revealer))[0]
    assert revealer.get_reveal_child() is True


def test_the_typed_query_survives_a_switch(gtk, monkeypatch):
    """The panes are not per-account at all — history.scan() reads
    ~/.claude/projects, so both accounts see the same list. A switch that
    emptied the filter would be discarding work over a change that cannot have
    affected what was being looked for."""
    frame, _chosen, _applied, ui_state = _body(gtk, monkeypatch)
    _search(frame).set_text("beta")
    _settle(400)   # GtkSearchEntry emits search-changed on a delay
    assert ui_state["query"] == "beta"
    panel_ui._body_swapper(frame, {"action": None}, gtk.Window(),
                           lambda _a: None, ui_state)("two")
    assert _search(frame).get_text() == "beta"
    projects, _sessions = _panes(frame)
    assert projects.get_row_at_index(1) is None      # only p/beta is left


def test_the_selected_project_survives_a_switch(gtk, monkeypatch):
    """The left pane's selection is where the two verbs act, so losing it means
    the buttons come back naming a project nobody chose."""
    frame, _chosen, _applied, ui_state = _body(gtk, monkeypatch)
    projects, _sessions = _panes(frame)
    projects.select_row(projects.get_row_at_index(1))
    assert ui_state["project"] == "/p/beta"
    panel_ui._body_swapper(frame, {"action": None}, gtk.Window(),
                           lambda _a: None, ui_state)("two")
    projects, _sessions = _panes(frame)
    assert _selected(projects, PROJECTS)["path"] == "/p/beta"


def test_the_selected_session_survives_a_switch(gtk, monkeypatch):
    """One keypress from resuming is the state the panel opens in; a switch
    should not put the user back at the top of the list."""
    frame, _chosen, _applied, ui_state = _body(gtk, monkeypatch)
    _projects, sessions = _panes(frame)
    sessions.select_row(sessions.get_row_at_index(1))
    assert ui_state["session"] == "u2"
    panel_ui._body_swapper(frame, {"action": None}, gtk.Window(),
                           lambda _a: None, ui_state)("two")
    _projects, sessions = _panes(frame)
    assert _selected(sessions, SESSIONS["/p/alpha"])["uuid"] == "u2"


def test_retiring_a_body_does_not_forget_the_selected_session(gtk, monkeypatch):
    """_retire() unselects the rows of the body being dropped, and that is a
    row-selected of its own. Recording the *effective* session there — the top
    of the list, which is what an unselected pane resumes — would overwrite the
    one the user picked with u1, in the moment before it is read back."""
    frame, _chosen, _applied, ui_state = _body(gtk, monkeypatch)
    _projects, sessions = _panes(frame)
    sessions.select_row(sessions.get_row_at_index(1))
    panel_ui._retire(frame.get_first_child())
    assert ui_state["session"] == "u2"


def test_the_restored_query_is_not_a_search_of_its_own(gtk, monkeypatch):
    """GtkSearchEntry arms `search-changed` on a delay, so seeding the entry
    with the remembered query fires one about 150 ms into the *new* body — and
    a search is a new question, so on_search drops the selected project for the
    top hit. It would land a moment after the switch, on nothing the user did.
    The echo is recognised by the text being what was already remembered."""
    frame, _chosen, _applied, ui_state = _body(gtk, monkeypatch)
    _search(frame).set_text("p/")       # matches both projects
    _settle(400)
    projects, _sessions = _panes(frame)
    projects.select_row(projects.get_row_at_index(1))
    panel_ui._body_swapper(frame, {"action": None}, gtk.Window(),
                           lambda _a: None, ui_state)("two")
    projects, _sessions = _panes(frame)
    assert _selected(projects, PROJECTS)["path"] == "/p/beta"
    _settle(400)   # the delayed search-changed of the seeded entry lands here
    projects, _sessions = _panes(frame)
    assert _selected(projects, PROJECTS)["path"] == "/p/beta"


# ── the on-demand usage refresh ───────────────────────────────────────────────

def _rows(percent, color, kind=usage.BOUNDED):
    return [{"key": "five_hour", "label": "5h", "kind": kind,
             "percent": percent, "resets": "in 2h", "color": color}]


def test_a_refresh_moves_the_bar_and_its_label(gtk):
    """In place, not by rebuilding the body. A fetch landing half a second after
    the panel opened would otherwise tear the widget tree out from under someone
    typing in the search entry or dragging a colour slider — the grab-losing bug
    Gtk.Scale already cost us once."""
    box, apply_rows = panel_ui._build_usage(_rows(20.0, "#a6e3a1"))
    bar = _walk(box, lambda w: isinstance(w, Gtk.ProgressBar))[0]
    assert bar.get_fraction() == pytest.approx(0.20)

    apply_rows(_rows(75.0, "#f9e2af"))
    assert bar.get_fraction() == pytest.approx(0.75)
    labels = [w.get_label() for w in _walk(box, lambda w: isinstance(w, Gtk.Label))]
    assert any("75" in (text or "") for text in labels)


def test_a_refresh_carries_the_bar_across_a_ramp_threshold(gtk):
    """The colour is a function of the reading, so it moves with it. _load_tints
    installs the whole of usage.RAMP up front precisely so this needs no new
    provider — but the old class has to come off, or the bar wears both."""
    box, apply_rows = panel_ui._build_usage(_rows(20.0, "#a6e3a1"))
    bar = _walk(box, lambda w: isinstance(w, Gtk.ProgressBar))[0]
    assert bar.has_css_class(panel_ui._tint("#a6e3a1"))

    apply_rows(_rows(95.0, "#f38ba8"))
    assert bar.has_css_class(panel_ui._tint("#f38ba8"))
    assert not bar.has_css_class(panel_ui._tint("#a6e3a1"))


def test_a_refresh_of_an_absent_window_leaves_the_bar_empty(gtk):
    """ABSENT has no percent and no colour. A bar at zero and a bar not drawn
    mean opposite things, and this must not paint the first as the second."""
    box, apply_rows = panel_ui._build_usage(_rows(60.0, "#f9e2af"))
    bar = _walk(box, lambda w: isinstance(w, Gtk.ProgressBar))[0]
    apply_rows(_rows(None, None, kind=usage.ABSENT))
    assert bar.get_fraction() == pytest.approx(0.0)
    assert not bar.has_css_class(panel_ui._tint("#f9e2af"))


def test_a_refresh_paints_the_body_that_asked_for_it():
    """No GTK: the guard is a comparison, and it is the whole of what keeps a
    late answer honest."""
    painted = []
    ui_state = {"generation": 3, "apply_usage": painted.append}
    panel_ui._usage_delivery(ui_state)(_rows(42.0, None))
    assert [r[0]["percent"] for r in painted] == [42.0]


def test_a_refresh_that_lands_after_a_switch_paints_nothing():
    """The reason the guard exists at all.

    The fetch is for the account the panel opened on. If a chip click swaps the
    body while it is in flight, `apply_usage` now points at the *other* account's
    bars — so painting would show one account's quota under the other's name.
    The reading still lands on disk; only the repaint is dropped.
    """
    painted = []
    ui_state = {"generation": 3, "apply_usage": painted.append}
    deliver = panel_ui._usage_delivery(ui_state)
    ui_state["generation"] = 4                    # a chip click swapped the body
    ui_state["apply_usage"] = painted.append      # ...and with it, the bars
    deliver(_rows(42.0, None))
    assert painted == []


def test_a_refresh_that_lands_after_the_panel_closed_paints_nothing():
    """Escape, then the answer. There is no body left to hold a handle to."""
    ui_state = {"generation": 1, "apply_usage": None}
    assert panel_ui._usage_delivery(ui_state)(_rows(42.0, None)) is False


def test_each_body_takes_a_generation_and_leaves_its_bars_reachable(
        gtk, monkeypatch):
    """build_body is the only thing that moves the generation, so a swap moves
    it exactly once and the delivery built before the swap can tell."""
    _frame, _chosen, _applied, ui_state = _body(gtk, monkeypatch)
    first = ui_state["generation"]
    assert callable(ui_state["apply_usage"])
    panel_ui._body_swapper(_frame, {"action": None}, gtk.Window(),
                           lambda _a: None, ui_state)("two")
    assert ui_state["generation"] != first


def test_every_body_asks_for_its_own_account(gtk, monkeypatch):
    """A chip click is a click. The refresh started once per *panel* left the
    account you switched to showing whatever was last on disk for it — which for
    the idle account this whole feature serves is the stalest reading there is.
    Each body asks for the account it is showing; the per-account stamp is what
    stops that costing a request every time you flick between two chips."""
    asked = []
    monkeypatch.setattr(panel_ui, "_start_usage_poll",
                        lambda slug, ui_state, glib: asked.append(slug))
    frame, _chosen, _applied, _ui = _body(gtk, monkeypatch)
    assert asked == ["one"]

    other = next(c for c in _chips(frame) if not c.has_css_class("current"))
    other.emit("clicked")
    _settle()
    assert asked == ["one", "two"]


def test_the_usage_ramp_outranks_the_users_stylesheet(gtk, monkeypatch):
    """menu.css says `.ccas-usage-bar progress { background: #89b4fa }`, at
    PRIORITY_USER, and priority beats specificity — so the ramp classes, added
    at PRIORITY_APPLICATION, were on the widget and painted nothing. The bars
    had never once shown the ramp. A colour that is a function of the reading is
    not a theme choice, so it goes above the stylesheet, the way the colour
    editor's preview swatch already does."""
    added = []
    monkeypatch.setattr(Gtk.StyleContext, "add_provider_for_display",
                        lambda display, provider, priority:
                        added.append((priority, provider)))
    panel_ui._load_tints(gtk.Window().get_display(), _state("one"))

    user = Gtk.STYLE_PROVIDER_PRIORITY_USER
    ramp = {value for _threshold, value in usage.RAMP}
    above = "\n".join(p.to_string() for level, p in added if level > user)
    for color in ramp:
        assert f"progressbar.{panel_ui._tint(color)} progress" in above


def test_a_new_token_gets_its_colour_chip_without_a_switch(gtk, monkeypatch):
    """Committing a format is what tells the chip row which tokens exist. The
    rebuild it triggers read the state captured when the body was built, so a
    token added in the entry had no colour chip until a chip switch rebuilt the
    body from disk — the user found it by clicking the other account and back."""
    stored = {"format": "%name"}

    def state(slug, now=None):
        out = _state(slug)
        out["format"] = stored["format"]
        out["color_targets"] = panel.color_targets(
            {"color": "#89b4fa", "format": stored["format"]})
        return out

    monkeypatch.setattr(panel, "format_previewer",
                        lambda slug, now=None: lambda text: (text, []))
    monkeypatch.setattr(panel, "build_state", state)

    def apply(action):
        if action.kind == "format":
            stored["format"] = action.value

    window = gtk.Window()
    frame = gtk.Box(orientation=gtk.Orientation.VERTICAL)
    ui_state = {"expanded": True, "target": 0}
    frame.append(panel_ui.build_body(state("one"), {"action": None}, window,
                                     apply, ui_state,
                                     panel_ui._body_swapper(
                                         frame, {"action": None}, window,
                                         apply, ui_state)))

    entry = _walk(frame, lambda w: isinstance(w, Gtk.Entry)
                  and w.has_css_class("ccas-format-entry"))[0]
    entry.set_text("%name ")
    entry.set_position(-1)
    chip = next(c for c in _walk(frame, lambda w: isinstance(w, Gtk.Button)
                                 and w.has_css_class("ccas-token-chip"))
                if c.get_child().get_label() == "7d reset")
    chip.emit("clicked")
    _settle()

    labels = [_walk(c, lambda w: isinstance(w, Gtk.Label))[0].get_label()
              for c in _walk(frame, lambda w: isinstance(w, Gtk.Button)
                             and w.has_css_class("ccas-color-chip"))]
    assert labels == ["icon", "nickname", "7d reset"]
