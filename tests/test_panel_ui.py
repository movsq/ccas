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


def test_a_token_chip_does_not_commit(gtk, monkeypatch):
    """A focusable chip steals focus, which is a focus-out, which would write
    the registry between every inserted token."""
    entry, chips, picked, _ui = _format_editor(gtk, monkeypatch)
    for chip in chips:
        assert chip.get_focusable() is False
    chips[0].emit("clicked")
    assert picked == []


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
