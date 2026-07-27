"""The panel's widget tree. The only module that touches GTK.

`gi` is imported inside show(), never at module scope. `ccs statusline` runs in
every prompt of every session and `ccs -p` is the passthrough; neither may be
breakable by a missing PyGObject, so importing this module must stay free.
test_cli_imports_with_pygobject_unavailable pins that.

Everything here renders a panel.build_state() dict and returns a panel.Action.
It decides nothing: no registry write, no launch, no filtering of its own.
"""
import os
import shutil
import subprocess
import sys

from . import format as fmt, panel, paths

MISSING_DEPS = ("ccs: the panel needs PyGObject, GTK4 and gtk4-layer-shell — "
                "install with: sudo pacman -S python-gobject gtk4 "
                "gtk4-layer-shell")

# Sized to its largest state rather than grown into it. A layer surface
# negotiates its size at map time, so resizing the window is janky; anything
# that moves has to move inside this.
WIDTH, HEIGHT = 900, 620

# How long the pointer probe waits for an answer it may never get. The enter
# event resolves it the moment it arrives — about 5 ms after the probes map —
# so nothing normal pays this, and it stays the fallback for the case where the
# pointer is on no probe at all: an idle output, which never sends
# wl_pointer.enter and once read as a panel that would not open.
#
# It was 120 ms, which was too short for the one case that matters. Waybar
# spawns the command on button *press*, and sway holds an implicit pointer grab
# until the button comes up — a surface mapping under the cursor during a grab
# is told nothing. So a click held longer than the timeout got no enter, fell
# through to the focused output and opened on the wrong monitor. Measured
# 2026-07-26: a 1.2 s hold timed out at 120 ms and opened on HDMI-A-1 with the
# pointer on DP-1; the enter arrived the instant the button came up. The number
# is therefore how long a human might hold a mouse button, not a repaint budget.
PROBE_MS = 1500

# How long the colour sliders wait after the last movement before writing. Long
# enough that a drag is one write rather than dozens, short enough that letting
# go and looking at the bar shows the new colour already there.
SETTLE_MS = 300

# GSK's renderer for the panel's first surface. The default here is vulkan and
# initialising it costs ~180 ms before anything is on screen; cairo costs ~2 ms
# (measured 2026-07-26). The panel is a static widget tree on one small surface
# over a transparent scrim — there is nothing for a GPU renderer to be faster
# at, and the whole cost is paid on a window that lives for a few seconds.
RENDERER = "cairo"

# GDK keyvals, spelled out rather than imported: naming them costs one comment
# and keeps the constants readable where they are used.
KEY_ESCAPE = 0xff1b
KEY_UP = 0xff52
KEY_DOWN = 0xff54
KEY_RETURN = 0xff0d
KEY_KP_ENTER = 0xff8d


def _notify(message: str) -> None:
    """Say it where a bar click can be seen.

    stderr goes to Waybar's log, which nobody is reading at the moment they
    click something and nothing happens.
    """
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "CCAS", message], check=False)
    print(message, file=sys.stderr)


def _load_css(display) -> None:
    """The user's stylesheet, if they have one. Absent is not an error — the
    panel has to open on a machine where install.sh has not run yet."""
    from gi.repository import Gtk
    path = paths.menu_css()
    if not path.exists():
        return
    provider = Gtk.CssProvider()
    provider.load_from_path(str(path))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)


def _setup_layer(window, LayerShell, output=None) -> None:
    """Make the window a full-output overlay that owns the keyboard.

    Three things, each of them a reported bug:

    - **All four edges.** The surface covers its output, so a click anywhere
      outside the panel still lands *on* the panel's surface, where it closes
      it and goes no further. A smaller surface would let that click through to
      whatever is underneath — pausing a video is not a way to dismiss a menu.
    - **Exclusive zone 0.** Keeps the surface out of the space Waybar reserved,
      so the widget that opened the panel stays clickable and can close it.
    - **EXCLUSIVE keyboard.** ON_DEMAND hands the keyboard over only once the
      surface is clicked, so Escape did nothing until the panel had been
      touched — worse on the monitor the pointer was not on. Sway resolves its
      own bindings before forwarding, so this does not lock the compositor out.
    """
    LayerShell.init_for_window(window)
    _pin_to_output(window, LayerShell, output)
    LayerShell.set_layer(window, LayerShell.Layer.OVERLAY)
    for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                 LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
        LayerShell.set_anchor(window, edge, True)
    LayerShell.set_exclusive_zone(window, 0)
    LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.EXCLUSIVE)


def _pin_to_output(window, LayerShell, output=None) -> None:
    """Put the panel on `output`, which panel.choose_output already settled.

    No precedence here: which connector wins is a decision, it belongs in
    panel.py, and the same answer has to go in the lock — the next click on the
    same account's widget compares against it to tell 'close' from 'move here'.

    Naming a connector that is not currently connected is not an error: the
    monitor list changes when a cable does, and a panel that refuses to open is
    worse than one on the wrong screen.
    """
    wanted = output
    if not wanted:
        return
    monitors = window.get_display().get_monitors()
    for i in range(monitors.get_n_items()):
        monitor = monitors.get_item(i)
        if monitor.get_connector() == wanted:
            LayerShell.set_monitor(window, monitor)
            return


def show(state: dict, gate=None, output=None, apply=None):
    """Open the panel, block until it closes, return the chosen Action or None.

    None means cancelled — Esc, or a click outside the surface. cli.dispatch_panel
    reads that as an exit rather than as a command.

    `gate` is called once with the connector the panel is about to open on, and
    a False from it means don't. That is the toggle: whether a click is the
    second one on the same widget cannot be known until the probe has said which
    monitor it came from, which is here and not in cli. The gate decides — this
    module only asks it.

    `output` is a connector already settled — the caller reopening the panel it
    just closed, on a chip click. It skips the probe, which would be both a
    delay and a wrong answer: the surface that has just gone away is this very
    panel's, on the output the probe would be asking about.

    `apply` runs a panel.STAYS_OPEN action there and then — cli.dispatch_panel,
    which the returned Action would otherwise have reached after the window was
    gone. A setting is not a departure, so it does not end the panel; the
    toggles are rebuilt from fresh state instead.
    """
    try:
        # Before `import gi`, and load-bearing. gtk4-layer-shell has to come
        # ahead of libwayland or every surface it makes silently degrades to an
        # ordinary toplevel — measured 2026-07-26: the panel opened as a normal
        # window on the other monitor, with "GtkWindow is not a layer surface"
        # on stderr and nothing on the bar. The library's own advice is
        # LD_PRELOAD, but that would have to live in the ccs launcher and would
        # then be inherited by every claude session ccs execs. An RTLD_GLOBAL
        # load here is the same fix scoped to the one process that needs it.
        import ctypes
        ctypes.CDLL("libgtk4-layer-shell.so.0", mode=ctypes.RTLD_GLOBAL)

        # Also before gi, and for the same reason as the line above: GSK reads
        # this when it makes the first renderer, which is inside the first
        # present(). setdefault — someone who set it is debugging their own
        # graphics stack and outranks us.
        os.environ.setdefault("GSK_RENDERER", RENDERER)

        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gio, Gtk, Gtk4LayerShell as LayerShell
    except (ImportError, ValueError, OSError):
        _notify(MISSING_DEPS)
        return None

    chosen = {"action": None}
    # NON_UNIQUE or the second panel process is not a process at all: an
    # application_id makes Gtk.Application single-instance, so while the panel
    # this click is replacing still holds the id, app.run() forwards `activate`
    # to *it* and returns without ever starting a main loop. The click closed a
    # panel and opened nothing, which is exactly how it was reported.
    app = Gtk.Application(application_id="dev.ccas.panel",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def on_activate(_app):
        from gi.repository import GLib

        def settled(probe):
            connector = output or panel.choose_output(probe)
            if gate is not None and not gate(connector):
                app.quit()   # the same widget, on the same bar: it just closed
                return
            _open(app, Gtk, LayerShell, state, chosen, connector, apply)

        _pointer_output(app, Gtk, GLib, LayerShell, state, settled,
                        skip=output is not None)

    app.connect("activate", on_activate)
    # No arguments: ccs has already parsed its own, and GTK would reject them.
    app.run([])
    return chosen["action"]


def _once(then):
    """A `then` that runs for the first answer and ignores the rest.

    The probe has two ways to finish and both of them fire: the pointer's enter
    event, and the timeout behind it. Whichever is first is the answer.
    """
    done = []

    def resolve(connector):
        if done:
            return
        done.append(connector)
        then(connector)

    return resolve


def _skip_probe() -> bool:
    """Is there nothing worth probing for? CCAS_PANEL_OUTPUT wins over the
    pointer in _pin_to_output, so probing would spend PROBE_MS on an answer that
    is discarded — on exactly the path an agent drives the panel from."""
    return bool(paths.panel_output())


def _pointer_output(app, Gtk, GLib, LayerShell, state, then, skip=False) -> None:
    """Find the monitor the pointer is on, then call `then(connector)`.

    Wayland tells a client nothing about where the pointer is until it is over
    one of that client's surfaces, and sway's IPC has no cursor position at all
    — `get_outputs` reports which output holds the *focused window*, which with
    `focus_follows_mouse no` is not where the user just clicked. So the question
    is asked the only way the protocol allows: put a surface on every monitor and
    see which one the pointer is already inside.

    Exclusive zone -1 on the probes, deliberately: they have to cover the bar
    too, because the bar is exactly where the pointer is one moment after its
    widget was clicked. They are transparent, live for one frame, and the real
    panel is built before they are dropped so the application never runs out of
    windows and quits.
    """
    display = Gdk_display(Gtk)
    # Before the first present(): a probe that paints is a flash on every open.
    _load_base(display)
    if skip or _skip_probe():
        then(None)
        return
    monitors = display.get_monitors()
    probes = []

    def finish(connector):
        then(connector)
        # Out of the event handler before the probes go: `finish` runs from
        # inside a probe's own motion controller, and destroying the widget the
        # event is being dispatched to is a tear-down under the dispatcher's
        # feet. The real panel is presented first either way, so the application
        # never runs out of windows and quits.
        def drop():
            for probe in probes:
                probe.destroy()
            return False

        GLib.idle_add(drop)

    resolve = _once(finish)

    for i in range(monitors.get_n_items()):
        monitor = monitors.get_item(i)
        probe = Gtk.ApplicationWindow(application=app)
        probe.add_css_class("ccas-probe")
        # A child that fills it: the controller has to sit on something the
        # pointer can be picked against.
        filler = Gtk.Box()
        filler.set_hexpand(True)
        filler.set_vexpand(True)
        probe.set_child(filler)
        LayerShell.init_for_window(probe)
        LayerShell.set_monitor(probe, monitor)
        LayerShell.set_layer(probe, LayerShell.Layer.OVERLAY)
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                     LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(probe, edge, True)
        LayerShell.set_exclusive_zone(probe, -1)
        LayerShell.set_keyboard_mode(probe, LayerShell.KeyboardMode.NONE)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda _c, _x, _y, m=monitor:
                       resolve(m.get_connector()))
        filler.add_controller(motion)
        probe.present()
        probes.append(probe)

    def settle():
        resolve(None)
        return False

    # The fallback, for a pointer that is on none of the probes. When there is
    # one, `enter` has already answered long before this fires.
    GLib.timeout_add(PROBE_MS, settle)


def Gdk_display(Gtk):
    """The default display. A function so the import stays inside show()'s try."""
    from gi.repository import Gdk
    return Gdk.Display.get_default()


def _open(app, Gtk, LayerShell, state, chosen, connector, apply=None) -> None:
    """Build the panel proper on `connector`."""
    window = Gtk.ApplicationWindow(application=app)
    window.add_css_class("ccas-scrim")

    _setup_layer(window, LayerShell, connector)
    _load_tints(window.get_display(), state)
    _load_css(window.get_display())

    keys = Gtk.EventControllerKey()

    def on_key(_c, keyval, _code, _mods):
        if keyval == KEY_ESCAPE:
            window.close()
            return True
        return False

    # On the window, in CAPTURE: Escape has to close the panel from wherever the
    # focus happens to be, including inside the search entry, which would
    # otherwise eat it to clear itself.
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    keys.connect("key-pressed", on_key)
    window.add_controller(keys)

    # The panel proper, floating on a surface the size of the output.
    frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    frame.add_css_class("ccas-panel")
    frame.set_size_request(WIDTH, HEIGHT)
    frame.set_halign(Gtk.Align.CENTER)
    frame.set_valign(Gtk.Align.START)
    frame.set_margin_top(8)
    frame.append(build_body(state, chosen, window, apply))

    scrim = Gtk.Box()
    scrim.set_hexpand(True)
    scrim.set_vexpand(True)
    scrim.append(frame)

    click = Gtk.GestureClick()
    click.set_button(0)  # any button: closing is not a left-click gesture

    def on_scrim_click(_g, _n, x, y):
        ok, rect = frame.compute_bounds(scrim)
        if not ok or not _inside(rect, x, y):
            window.close()

    click.connect("pressed", on_scrim_click)
    scrim.add_controller(click)

    window.set_child(scrim)
    window.present()


def _inside(rect, x, y) -> bool:
    """Is (x, y) within the panel's bounds? The scrim covers the whole output, so
    'outside the panel' is a hit test rather than something the compositor can
    answer by which surface was clicked."""
    return (rect.origin.x <= x <= rect.origin.x + rect.size.width
            and rect.origin.y <= y <= rect.origin.y + rect.size.height)


def _usage_text(row):
    """How a window is said. The reset time is the headline, not the percentage.

    `resets_at` is an absolute anchor, so a bounded reading is a *lower* bound
    until the window rolls over — hence `≥`. An absent window says so rather
    than drawing an empty bar, which is indistinguishable from a bar at zero and
    means the opposite.
    """
    if row["kind"] == "absent":
        return "no data"
    if row["kind"] == "open":
        return f"clears {row['resets']}"
    return f"≥{row['percent']:.0f}%  clears {row['resets']}"


def _tint(color: str) -> str:
    """The CSS class carrying `color`, defined by _load_tints().

    A class rather than a per-widget provider: Gtk.Widget.get_style_context() is
    deprecated in GTK 4, and the display-level provider is the supported way to
    say this. The hex is the class name, so two widgets asking for one colour
    share one rule.
    """
    return "ccas-tint-" + color.lstrip("#").lower()


# Nearly invisible, and deliberately not `transparent`. A GTK4 window that
# paints nothing gets no input region, and a layer surface with no input region
# receives no pointer events at all — measured 2026-07-26, and it is what made
# the first pointer probe come back empty every time. The same trap sits under
# the scrim: `background: transparent` in menu.css would silently disable
# closing on a click outside the panel, and let that click through to whatever
# is beneath. 0.01 alpha is under a quarter of one 8-bit step, so neither
# surface is visible; menu.css paints the scrim properly on top of this.
INVISIBLE = "rgba(0, 0, 0, 0.01)"
BASE_CSS = (f"window.ccas-scrim {{ background: {INVISIBLE}; }}\n"
            f"window.ccas-probe {{ background: {INVISIBLE}; }}")


def _load_base(display) -> None:
    """The rules that must be in place before the first surface maps."""
    from gi.repository import Gtk
    provider = Gtk.CssProvider()
    provider.load_from_string(BASE_CSS)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _load_tints(display, state) -> None:
    """One provider for every colour the panel needs, at APPLICATION priority.

    Below the user's menu.css (USER), deliberately: these say what a colour *is*,
    and the stylesheet stays free to override anything it names.
    """
    from gi.repository import Gtk
    colors = {c for _name, c in paths.PALETTE}
    colors |= {row["color"] for row in state["usage"] if row["color"]}
    colors |= {a["color"] for a in state["accounts"]}
    css = "\n".join(
        f".{_tint(c)} {{ background: {c}; }}\n"
        f"progressbar.{_tint(c)} {{ background: transparent; }}\n"
        f"progressbar.{_tint(c)} progress {{ background: {c}; }}"
        for c in sorted(colors))
    provider = Gtk.CssProvider()
    provider.load_from_string(css)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _usage_bar(row):
    from gi.repository import Gtk
    bar = Gtk.ProgressBar()
    bar.add_css_class("ccas-usage-bar")
    bar.set_fraction(0.0 if row["percent"] is None else row["percent"] / 100)
    if row["color"]:
        # A function of the reading, so it cannot live in menu.css as a fixed
        # rule — but the colour it picks is one of a known few.
        bar.add_css_class(_tint(row["color"]))
    return bar


def _dot(color):
    """The account's colour, as the bar shows it. A DrawingArea would be one
    more thing to redraw; a sized, rounded, background-coloured box is enough."""
    from gi.repository import Gtk
    dot = Gtk.Box()
    dot.add_css_class("ccas-chip-dot")
    dot.add_css_class(_tint(color))
    return dot


def _build_header(state, pick, close):
    from gi.repository import Gtk

    header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    header.add_css_class("ccas-header")

    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    for account in state["accounts"]:
        chip = Gtk.Button()
        chip.add_css_class("ccas-chip")
        if account["current"]:
            chip.add_css_class("current")
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        inner.set_valign(Gtk.Align.CENTER)
        dot = _dot(account["color"])
        dot.set_valign(Gtk.Align.CENTER)
        inner.append(dot)
        inner.append(Gtk.Label(label=account["name"]))
        chip.set_child(inner)
        if not account["current"]:
            # The current account's chip is a label, not a no-op button: acting
            # on it would reopen the panel we are already in.
            chip.connect("clicked", lambda _b, s=account["slug"]:
                         pick(panel.Action("switch", s, None)))
        top.append(chip)

    spacer = Gtk.Box()
    spacer.set_hexpand(True)
    top.append(spacer)

    # Escape and a click outside both close it, but neither is visible. A menu
    # with no way out you can see is one you have to be told how to leave.
    shut = Gtk.Button(label="✕")
    shut.add_css_class("ccas-close")
    shut.connect("clicked", lambda _b: close())
    top.append(shut)
    header.append(top)

    current = next((a for a in state["accounts"] if a["current"]), None)
    if current and current["email"]:
        email = Gtk.Label(label=current["email"], xalign=0)
        email.add_css_class("ccas-email")
        header.append(email)

    for row in state["usage"]:
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        key = Gtk.Label(label=row["label"], xalign=0)
        key.add_css_class("ccas-usage-key")
        line.append(key)
        bar = _usage_bar(row)
        bar.set_hexpand(True)
        bar.set_valign(Gtk.Align.CENTER)
        line.append(bar)
        text = Gtk.Label(label=_usage_text(row), xalign=1)
        text.add_css_class("ccas-usage-label")
        line.append(text)
        header.append(line)

    return header


def _project_row(project):
    from gi.repository import Gtk, Pango
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.add_css_class("ccas-project-row")
    name = Gtk.Label(label=project["short"], xalign=0)
    # MIDDLE, not END: the tail of a path is what tells two projects apart.
    name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
    name.set_hexpand(True)
    row.append(name)
    age = Gtk.Label(label=project["age"], xalign=1)
    age.add_css_class("ccas-project-age")
    row.append(age)
    return row


def _session_row(session):
    from gi.repository import Gtk, Pango
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.add_css_class("ccas-session-row")
    age = Gtk.Label(label=session["age"], xalign=0)
    age.add_css_class("ccas-session-age")
    row.append(age)
    title = Gtk.Label(label=session["title"], xalign=0)
    title.set_ellipsize(Pango.EllipsizeMode.END)
    title.set_hexpand(True)
    row.append(title)
    return row


def _empty(text):
    from gi.repository import Gtk
    label = Gtk.Label(label=text)
    label.add_css_class("ccas-empty")
    return label


def _clear(listbox):
    """Empty any GTK4 container that takes remove() — a ListBox or a Box."""
    child = listbox.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        listbox.remove(child)
        child = nxt


def build_body(state, chosen, window, apply=None):
    """The panel's root widget: header, verbs, search, the two panes, toggles.

    `state` is rendered, never consulted for a decision — panel.py already made
    them. The one thing held here is the unfiltered state, so that a keystroke
    refilters it instead of rescanning ~/.claude/projects.
    """
    from gi.repository import Gtk, GLib

    slug = state["slug"]
    # Set while the toggles are being rebuilt: set_active() and set_selected()
    # emit the very signals that call pick(), so a refresh would otherwise
    # re-apply everything it just rendered.
    filling_toggles = {"on": False}
    # Outside the rebuilt subtree, for the same reason the search text and the
    # selected session are: refresh_toggles() destroys everything inside it, so
    # anything stored in there is lost the moment a checkbox is ticked.
    ui_state = {"expanded": False, "target": 0}

    def pick(action):
        if apply is not None and action.kind in panel.STAYS_OPEN:
            if filling_toggles["on"]:
                return
            apply(action)
            if action.kind not in panel.NO_REBUILD:
                # Out of the signal handler before the widget that emitted it is
                # destroyed, and after the registry write the refresh reads back.
                GLib.idle_add(refresh_toggles)
            return
        chosen["action"] = action
        window.close()
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

    # SLIDE_UP, and it grows over the panes rather than above them: the card is
    # a fixed WIDTH, HEIGHT, so a drawer that took its own row stole the height
    # from the only thing that expands — the session list shrank to about one
    # row every time Settings was opened. Overlaid, the panes keep their size and
    # are simply covered while the drawer is out.
    revealer = Gtk.Revealer()
    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
    revealer.set_valign(Gtk.Align.END)
    root.append(_build_header(state, pick, window.close))

    # ── verbs ────────────────────────────────────────────────────────────
    verbs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    verbs.add_css_class("ccas-verbs")
    new_button = Gtk.Button()
    new_button.add_css_class("ccas-verb")
    resume_button = Gtk.Button(label="Resume last")
    resume_button.add_css_class("ccas-verb")
    verbs.append(new_button)
    verbs.append(resume_button)
    root.append(verbs)

    # ── search and panes ─────────────────────────────────────────────────
    entry = Gtk.SearchEntry()
    entry.set_name("ccas-search")

    projects_list = Gtk.ListBox()
    sessions_list = Gtk.ListBox()
    for box in (projects_list, sessions_list):
        box.set_selection_mode(Gtk.SelectionMode.SINGLE)

    left = Gtk.ScrolledWindow()
    left.set_child(projects_list)
    right = Gtk.ScrolledWindow()
    right.set_child(sessions_list)
    panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
    panes.add_css_class("ccas-panes")
    panes.set_start_child(left)
    panes.set_end_child(right)
    panes.set_position(320)
    panes.set_vexpand(True)
    # The search entry is inside what the drawer covers, not above it. Left
    # outside, it stayed on screen with settings filling everything below it and
    # read as a search field *for* the settings — which it has never been.
    finder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    finder.append(entry)
    finder.append(panes)
    finder.set_vexpand(True)

    # The drawer is laid over this, anchored to its bottom edge, so it appears
    # to slide up out of the Settings row directly beneath.
    stack = Gtk.Overlay()
    stack.set_child(finder)
    stack.set_vexpand(True)
    stack.add_overlay(revealer)
    root.append(stack)

    # `view` is the filtered state; `state` stays whole so a narrowed search can
    # widen again without a rescan.
    view = {"state": state, "project": state["selected_project"]}
    # Guards: rebuilding a ListBox emits row-selected, which would recurse.
    filling = {"on": False}

    def selected_session():
        rows = view["state"]["sessions"].get(view["project"], [])
        row = sessions_list.get_selected_row()
        if row is not None and 0 <= row.get_index() < len(rows):
            return rows[row.get_index()]
        return rows[0] if rows else None

    def sync_verbs():
        project = next((p for p in view["state"]["projects"]
                        if p["path"] == view["project"]), None)
        new_button.set_label("New session"
                             if project is None
                             else f"New session in {project['short']}")
        new_button.set_sensitive(project is not None)
        resume_button.set_sensitive(selected_session() is not None)

    def fill_sessions():
        filling["on"] = True
        _clear(sessions_list)
        rows = view["state"]["sessions"].get(view["project"], [])
        if not rows:
            sessions_list.append(_empty("no sessions"))
            sessions_list.set_selection_mode(Gtk.SelectionMode.NONE)
        else:
            sessions_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
            for session in rows:
                sessions_list.append(_session_row(session))
            # The latest-modified session, highlighted: the panes are in
            # recency order, so that is row 0 by construction.
            first = sessions_list.get_row_at_index(0)
            if first is not None:
                sessions_list.select_row(first)
        filling["on"] = False
        sync_verbs()

    def fill_projects():
        filling["on"] = True
        _clear(projects_list)
        projects = view["state"]["projects"]
        if not projects:
            projects_list.append(_empty("no projects"))
            projects_list.set_selection_mode(Gtk.SelectionMode.NONE)
        else:
            projects_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
            for project in projects:
                projects_list.append(_project_row(project))
            index = next((i for i, p in enumerate(projects)
                          if p["path"] == view["project"]), 0)
            view["project"] = projects[index]["path"]
            projects_list.select_row(projects_list.get_row_at_index(index))
        filling["on"] = False
        fill_sessions()

    def on_project(_box, row):
        if filling["on"] or row is None:
            return
        projects = view["state"]["projects"]
        if 0 <= row.get_index() < len(projects):
            view["project"] = projects[row.get_index()]["path"]
            fill_sessions()

    def on_search(_entry):
        view["state"] = panel.filter_state(state, entry.get_text())
        view["project"] = view["state"]["selected_project"]
        fill_projects()

    def resume_selected():
        session = selected_session()
        if session is not None:
            pick(panel.Action("resume", slug, session["uuid"]))

    projects_list.connect("row-selected", on_project)
    sessions_list.connect("row-activated", lambda _b, _r: resume_selected())
    sessions_list.connect("row-selected", lambda _b, _r: sync_verbs())
    entry.connect("search-changed", on_search)

    def on_entry_key(_c, keyval, _code, _mods):
        """Arrows and Enter reach the session list without leaving the entry.

        The entry keeps the focus the whole time the panel is open, so typing
        never has to be interrupted to move the selection — which is the thing
        the fuzzel cascade could not do.
        """
        rows = view["state"]["sessions"].get(view["project"], [])
        if keyval in (KEY_UP, KEY_DOWN) and rows:
            row = sessions_list.get_selected_row()
            index = 0 if row is None else row.get_index()
            index += 1 if keyval == KEY_DOWN else -1
            index = max(0, min(index, len(rows) - 1))
            sessions_list.select_row(sessions_list.get_row_at_index(index))
            return True
        if keyval in (KEY_RETURN, KEY_KP_ENTER):
            resume_selected()
            return True
        return False

    keys = Gtk.EventControllerKey()
    keys.connect("key-pressed", on_entry_key)
    entry.add_controller(keys)

    new_button.connect("clicked", lambda _b:
                       pick(panel.Action("new", slug, view["project"])))
    resume_button.connect("clicked", lambda _b: resume_selected())

    # A holder, so a setting can be applied and re-rendered without rebuilding
    # the panes: the search text and the selected session are the user's place
    # in the panel, and ticking a checkbox is no reason to lose either.
    toggles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

    # Opaque, because it is drawn on top of the panes now: menu.css gives
    # .ccas-drawer the panel's own background.
    drawer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    drawer.add_css_class("ccas-drawer")

    def refresh_toggles():
        filling_toggles["on"] = True
        try:
            _clear(toggles)
            _clear(drawer)
            # Fresh, not the captured `state`: the account is on disk now.
            visible, hidden = _build_toggles(panel.build_state(slug), pick,
                                             ui_state)
            toggles.append(visible)
            drawer.append(hidden)
        finally:
            filling_toggles["on"] = False
        return False  # a one-shot idle callback

    visible, hidden = _build_toggles(state, pick, ui_state)
    toggles.append(visible)
    drawer.append(hidden)
    root.append(toggles)
    def toggle_drawer():
        open_now = not revealer.get_reveal_child()
        revealer.set_reveal_child(open_now)
        # Covered is not the same as out of reach: the search entry keeps the
        # focus for the whole life of the panel, so without this a keystroke
        # meant for the settings would land in an entry nobody can see and
        # silently refilter the list underneath. Insensitive drops the focus;
        # closing the drawer hands it back.
        finder.set_sensitive(not open_now)
        # Hidden, not merely covered. The drawer is only as tall as its content,
        # so the entry stayed on screen above it with settings filling
        # everything below — which reads as a search field *for* the settings.
        entry.set_visible(not open_now)
        # And the panes with it, or the row the drawer does not reach shows as a
        # single stranded session line above the settings.
        panes.set_visible(not open_now)
        if not open_now:
            entry.grab_focus()

    root.append(_build_settings_row(toggle_drawer, ui_state))
    revealer.set_child(drawer)
    # A rebuild restores the drawer rather than closing it under the user's
    # hand: refresh_toggles() runs on every applied setting.
    revealer.set_reveal_child(ui_state["expanded"])
    finder.set_sensitive(not ui_state["expanded"])
    entry.set_visible(not ui_state["expanded"])
    panes.set_visible(not ui_state["expanded"])

    fill_projects()
    # After the fills, so nothing that runs during them can steal it back.
    entry.grab_focus()
    return root


def _build_toggles(state, pick, ui_state):
    """(always visible, inside the drawer). Two boxes, not one.

    Every switchable thing, with its state readable without opening anything —
    that is the panel's whole argument over the cascade, where whether
    skip-permissions is on was knowable only by opening a submenu to read a
    mark. The split is by whether a setting bears on the click being made:
    skip permissions changes how the session this panel is about to launch will
    run, so it stays on the card. `headless` only affects `ccs -p` from a
    terminal and is never relevant to a panel click, so it goes in the drawer
    with the appearance settings.

    Real GtkCheckButtons, not label.MARK_ON/MARK_OFF. That pair exists to survive
    Waybar's FontAwesome-first font stack, which this process does not inherit.
    """
    from gi.repository import Gtk

    slug = state["slug"]
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class("ccas-toggles")

    def toggle(text, key, kind):
        check = Gtk.CheckButton(label=text)
        check.set_active(state[key])
        # "toggled", not "clicked": set_active() above would fire clicked, and
        # the panel would act on its own initial render.
        check.connect("toggled", lambda _c, k=kind:
                      pick(panel.Action(k, slug, None)))
        return check

    outside = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
    outside.add_css_class("ccas-toggles")
    outside.append(toggle("skip permissions", "dangerous", "dangerous"))

    box.append(toggle("headless runner", "headless", "headless"))
    box.append(toggle("hide the icon", "hide_icon", "hide_icon"))

    bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
    edit = Gtk.Button(label="Edit format…")
    edit.add_css_class("ccas-ghost")
    edit.connect("clicked",
                 lambda _b: pick(panel.Action("edit_format", slug, None)))
    bottom.append(edit)

    box.append(bottom)
    box.append(_build_color_editor(state, pick, ui_state))
    box.append(_build_manage(state, pick))
    return outside, box


def _build_color_editor(state, pick, ui_state):
    """A row of chips, two sliders, one preview. The two colour rows this
    replaces showed the same eight swatches under the word Colour and meant
    different things — the account's colour and one token's — which is what
    made the section unreadable.

    Nothing is parsed here: state["color_targets"] arrives decided.
    """
    from gi.repository import GLib, Gtk

    slug = state["slug"]
    targets = state["color_targets"]
    # Write once the drag settles, never per motion event: usage.py's rule is
    # write only on a change with waybar.signal() riding on the write, and a
    # live drag would fire both continuously.
    #
    # Not a release event, which is what this looked like it wanted. Gtk.Scale
    # has no "released" signal in GTK4; a GestureClick added to one claims the
    # event sequence and denies the scale's own drag, so the knob stops
    # tracking the pointer entirely, and EventControllerLegacy's handler is
    # passed a None event. Both measured on screen. A settle timer needs
    # neither, and it gives keyboard adjustment the same single write.
    settling = {"source": None, "seeding": False}
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.add_css_class("ccas-color-editor")

    # A row of chips, not a dropdown. The dropdown showed one target at a time,
    # cost a click and a popup to change, and — greyed by an over-broad CSS
    # rule — read as a disabled control. The chips show every target's current
    # colour at once, which is the thing the user could not see.
    chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    chips.add_css_class("ccas-color-chips")

    # The name stays at full contrast and the colour goes in a 2 px strip under
    # it: a name painted in its own colour is unreadable at low saturation and
    # indistinguishable from "unset" at white, which is exactly the colour an
    # unset token renders as.
    strips = []
    buttons = []
    for entry_target in targets:
        chip = Gtk.Button()
        chip.add_css_class("ccas-color-chip")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        inner.append(Gtk.Label(label=entry_target["label"]))
        strip = Gtk.Box()
        strip.add_css_class("ccas-color-chip-strip")
        # Its own provider, at PRIORITY_USER + 1, for the reason the preview
        # swatch has one: menu.css is installed at PRIORITY_USER, so an
        # application-priority rule loses to it silently, and a colour part-way
        # through a drag belongs to none of the classes _load_tints() built.
        strip_provider = Gtk.CssProvider()
        strip.get_style_context().add_provider(
            strip_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        strip_provider.load_from_string(
            f".ccas-color-chip-strip {{ background: {entry_target['color']}; }}")
        inner.append(strip)
        chip.set_child(inner)
        chips.append(chip)
        strips.append(strip_provider)
        buttons.append(chip)
    box.append(Gtk.Label(label="Color of", xalign=0))
    box.append(chips)

    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

    def target():
        return targets[min(ui_state["target"], len(targets) - 1)]

    # A colour halfway through a drag is in neither set _load_tints() built the
    # CSS classes from, so _tint() would name a class that does not exist and
    # the swatch would show nothing. Its own provider, updated per motion.
    preview = Gtk.Box()
    preview.add_css_class("ccas-preview")
    provider = Gtk.CssProvider()
    # Above PRIORITY_USER, not APPLICATION: _load_css() installs menu.css at
    # PRIORITY_USER, so its `.ccas-preview { background: #353535 }` fallback
    # outranks an application-priority rule and the swatch stays bar-grey
    # whatever the sliders say. Measured on screen, not reasoned about.
    preview.get_style_context().add_provider(
        provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

    # No `account` button, and no `account` value behind it either now: a
    # token's colour is a hex or it is nothing. What the panel offers for every
    # target, the icon included, is one colour.
    top.append(preview)
    box.append(top)

    hue = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 360, 1)
    sat = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.01)
    for scale, name in ((hue, "hue"), (sat, "saturation")):
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.add_css_class("ccas-slider")
        scale.add_css_class(f"ccas-slider-{name}")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        label_widget = Gtk.Label(label=name, xalign=0)
        label_widget.set_size_request(70, -1)
        row.append(label_widget)
        row.append(scale)
        box.append(row)

    def current_hex():
        return fmt.hs_to_hex(hue.get_value(), sat.get_value())

    # Before paint(), which fills it. The escape hatch for a colour the sliders
    # cannot reach: they hold lightness at format.LIGHTNESS, and a colour is any
    # hex. It carried a "#rrggbb" placeholder and nothing else, which said what
    # to type but never what was there — so the one field showing an exact value
    # was the one field that never showed the current one.
    entry = Gtk.Entry(max_length=7, width_chars=8)
    entry.add_css_class("ccas-hex")

    def paint(value):
        # The bar's own background, so an uncommitted preview is still honest
        # about what the label will look like once it lands.
        provider.load_from_string(f".ccas-preview {{ background: {value}; }}")
        # The selected chip's strip tracks the drag, so the row keeps saying
        # what every target's colour is while one of them is being changed.
        strips[ui_state["target"]].load_from_string(
            f".ccas-color-chip-strip {{ background: {value}; }}")
        # Not while it has the focus: that is the user part-way through typing
        # a colour, and overwriting it under the caret is the same mistake as
        # committing mid-drag.
        if not entry.has_focus():
            entry.set_text(value)

    def repaint(*_a):
        paint(current_hex())

    def _commit(value=None):
        entry_value = current_hex() if value is None else value
        chosen = target()
        if chosen["token"] is None:
            pick(panel.Action("color", slug, entry_value))
        else:
            pick(panel.Action("format_color", slug,
                              (chosen["token"], entry_value)))

    def seed(*_a):
        """Slider positions for the selected target, without committing: setting
        a Gtk.Scale emits value-changed, and acting on it would write the
        registry every time the dropdown moved."""
        settling["seeding"] = True
        h, s = fmt.hex_to_hs(target()["color"])
        hue.set_value(h)
        sat.set_value(s)
        settling["seeding"] = False
        # The stored colour, not current_hex(): the sliders pin lightness at
        # LIGHTNESS and this one may not be there yet. Nothing shifts merely
        # because the editor opened — the first drag is what snaps it.
        paint(target()["color"])

    def on_value(*_a):
        repaint()
        if settling["source"] is not None:
            GLib.source_remove(settling["source"])
        # Never while seed() is moving the sliders: that is a chip click
        # changing target, not the user choosing a colour.
        if not settling["seeding"]:
            settling["source"] = GLib.timeout_add(SETTLE_MS, on_settled)

    def on_settled():
        settling["source"] = None
        _commit()
        return False

    hue.connect("value-changed", on_value)
    sat.connect("value-changed", on_value)

    def show_selection():
        for index, chip in enumerate(buttons):
            if index == ui_state["target"]:
                chip.add_css_class("ccas-color-chip-on")
            else:
                chip.remove_css_class("ccas-color-chip-on")

    def choose(index):
        # Selection is a background, never a colour: the strip means "this is
        # the colour" and the background means "this is what you are editing".
        # One cue doing both jobs is unreadable.
        #
        # seed() only moves sliders and never commits, which is why picking a
        # target writes nothing. That separation is why seed and _commit are
        # two functions.
        ui_state["target"] = index
        show_selection()
        seed()

    for index, chip in enumerate(buttons):
        chip.connect("clicked", lambda _b, i=index: choose(i))

    # Restored from outside the rebuilt subtree: refresh_toggles() destroys this
    # whole editor on every applied setting, and reopening on the icon after
    # every tick would lose the target the user picked.
    ui_state["target"] = min(ui_state.get("target", 0), len(targets) - 1)
    show_selection()

    entry.connect("activate", lambda e: _commit(e.get_text()))
    top.append(entry)

    seed()
    return box


def _build_settings_row(reveal_toggle, ui_state):
    """A labelled disclosure, not a gear. It hides three account verbs and every
    widget setting, and an unlabelled icon is a guess about which.

    The two glyphs are drawn by the panel's own stylesheet, not Waybar's, so the
    vetted-glyph rule does not reach here — this process has no FontAwesome in
    it at all.
    """
    from gi.repository import Gtk

    button = Gtk.Button()
    button.add_css_class("ccas-settings-row")
    inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    arrow = Gtk.Label(label="⌄" if ui_state["expanded"] else "›")
    arrow.add_css_class("ccas-settings-arrow")
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    title = Gtk.Label(label="Settings", xalign=0)
    title.add_css_class("ccas-settings-title")
    hint = Gtk.Label(label="Account and widget customisation", xalign=0)
    hint.add_css_class("ccas-settings-hint")
    text.append(title)
    text.append(hint)
    inner.append(arrow)
    inner.append(text)
    button.set_child(inner)

    def toggled(_b):
        ui_state["expanded"] = not ui_state["expanded"]
        arrow.set_label("⌄" if ui_state["expanded"] else "›")
        reveal_toggle()

    button.connect("clicked", toggled)
    return button


def _build_manage(state, pick):
    """The rare, destructive half — behind the header's ⚙ rather than in reach.

    Rename and remove route to cmd_manage, which prompts in the terminal. The
    revealer names the action; cli runs it.
    """
    from gi.repository import Gtk

    slug = state["slug"]
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    box.add_css_class("ccas-manage")
    for text, kind, danger in (("Rename…", "rename", False),
                               (f"Remove {slug}…", "remove", True),
                               ("Add account…", "add", False)):
        button = Gtk.Button(label=text)
        if danger:
            button.add_css_class("ccas-danger")
        button.connect("clicked", lambda _b, k=kind:
                       pick(panel.Action(k, slug, None)))
        box.append(button)
    return box
