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
