"""The panel's widget tree. The only module that touches GTK.

`gi` is imported inside show(), never at module scope. `ccs statusline` runs in
every prompt of every session and `ccs -p` is the passthrough; neither may be
breakable by a missing PyGObject, so importing this module must stay free.
test_cli_imports_with_pygobject_unavailable pins that.

Everything here renders a panel.build_state() dict and returns a panel.Action.
It decides nothing: no registry write, no launch, no filtering of its own.
"""
import shutil
import subprocess
import sys

from . import panel, paths

MISSING_DEPS = ("ccs: the panel needs PyGObject, GTK4 and gtk4-layer-shell — "
                "install with: sudo pacman -S python-gobject gtk4 "
                "gtk4-layer-shell")

# Sized to its largest state rather than grown into it. A layer surface
# negotiates its size at map time, so resizing the window is janky; anything
# that moves has to move inside this.
WIDTH, HEIGHT = 900, 620

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


def _pin_to_output(window, LayerShell) -> None:
    """Put the panel on the output CCAS_PANEL_OUTPUT names, if it names one.

    Unset leaves it to the compositor, which on a two-head setup is not
    reliably the output Waybar is on — measured 2026-07-26, where it opened on
    the other monitor. Naming a connector that is not currently connected is
    not an error: the monitor list changes when a cable does, and a panel that
    refuses to open is worse than one on the wrong screen.
    """
    wanted = paths.panel_output()
    if not wanted:
        return
    monitors = window.get_display().get_monitors()
    for i in range(monitors.get_n_items()):
        monitor = monitors.get_item(i)
        if monitor.get_connector() == wanted:
            LayerShell.set_monitor(window, monitor)
            return


def show(state: dict):
    """Open the panel, block until it closes, return the chosen Action or None.

    None means cancelled — Esc, or a click outside the surface. cli.dispatch_panel
    reads that as an exit rather than as a command.
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

        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk, Gtk4LayerShell as LayerShell
    except (ImportError, ValueError, OSError):
        _notify(MISSING_DEPS)
        return None

    chosen = {"action": None}
    app = Gtk.Application(application_id="dev.ccas.panel")

    def on_activate(_app):
        window = Gtk.ApplicationWindow(application=app)
        window.set_default_size(WIDTH, HEIGHT)
        window.add_css_class("ccas-panel")

        LayerShell.init_for_window(window)
        _pin_to_output(window, LayerShell)
        LayerShell.set_layer(window, LayerShell.Layer.OVERLAY)
        LayerShell.set_anchor(window, LayerShell.Edge.TOP, True)
        LayerShell.set_margin(window, LayerShell.Edge.TOP, 8)
        # ON_DEMAND, not EXCLUSIVE: the panel takes the keyboard while it has
        # focus, without locking the compositor out if it fails to close.
        LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.ON_DEMAND)

        _load_tints(window.get_display(), state)
        _load_css(window.get_display())

        keys = Gtk.EventControllerKey()

        def on_key(_c, keyval, _code, _mods):
            if keyval == KEY_ESCAPE:
                window.close()
                return True
            return False

        keys.connect("key-pressed", on_key)
        window.add_controller(keys)

        window.set_child(build_body(state, chosen, window))
        window.present()

    app.connect("activate", on_activate)
    # No arguments: ccs has already parsed its own, and GTK would reject them.
    app.run([])
    return chosen["action"]


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


def _build_header(state, pick, reveal_toggle):
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

    gear = Gtk.Button(label="⚙")
    gear.add_css_class("ccas-gear")
    gear.connect("clicked", lambda _b: reveal_toggle())
    top.append(gear)
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
    child = listbox.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        listbox.remove(child)
        child = nxt


def build_body(state, chosen, window):
    """The panel's root widget: header, verbs, search, the two panes, toggles.

    `state` is rendered, never consulted for a decision — panel.py already made
    them. The one thing held here is the unfiltered state, so that a keystroke
    refilters it instead of rescanning ~/.claude/projects.
    """
    from gi.repository import Gtk

    def pick(action):
        chosen["action"] = action
        window.close()

    slug = state["slug"]
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

    revealer = Gtk.Revealer()
    revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
    root.append(_build_header(
        state, pick, lambda: revealer.set_reveal_child(
            not revealer.get_reveal_child())))

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
    root.append(entry)

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
    root.append(panes)

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

    root.append(_build_toggles(state, pick))
    revealer.set_child(_build_manage(state, pick))
    root.append(revealer)

    fill_projects()
    # After the fills, so nothing that runs during them can steal it back.
    entry.grab_focus()
    return root


def _build_toggles(state, pick):
    """Every switchable thing, with its state readable without opening anything.

    That is the panel's whole argument over the cascade: whether skip-permissions
    is on used to be knowable only by opening a submenu to read a mark.

    Real GtkCheckButtons, not label.MARK_ON/MARK_OFF. That pair exists to survive
    Waybar's FontAwesome-first font stack, which this process does not inherit.
    """
    from gi.repository import Gtk

    slug = state["slug"]
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class("ccas-toggles")

    checks = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
    for text, key, kind in (("headless runner", "headless", "headless"),
                            ("skip permissions", "dangerous", "dangerous"),
                            ("hide icon", "hide_icon", "hide_icon")):
        check = Gtk.CheckButton(label=text)
        check.set_active(state[key])
        # "toggled", not "clicked": set_active() above would fire clicked, and
        # the panel would act on its own initial render.
        check.connect("toggled", lambda _c, k=kind:
                      pick(panel.Action(k, slug, None)))
        checks.append(check)
    box.append(checks)

    bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
    modes = Gtk.DropDown.new_from_strings(paths.DISPLAY_MODES)
    if state["display"] in paths.DISPLAY_MODES:
        modes.set_selected(paths.DISPLAY_MODES.index(state["display"]))
    modes.connect("notify::selected", lambda d, _p: _on_mode(d, slug, state, pick))
    bottom.append(modes)

    swatches = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    swatches.set_valign(Gtk.Align.CENTER)
    current = next((a for a in state["accounts"] if a["current"]), None)
    for index, (_name, hexcolor) in enumerate(paths.PALETTE):
        swatch = Gtk.Button()
        swatch.add_css_class("ccas-swatch")
        swatch.add_css_class(_tint(hexcolor))
        if current and current["color"] == hexcolor:
            swatch.add_css_class("current")
        swatch.connect("clicked", lambda _b, i=index:
                       pick(panel.Action("color", slug, i)))
        swatches.append(swatch)
    bottom.append(swatches)
    box.append(bottom)
    return box


def _on_mode(dropdown, slug, state, pick):
    """Only on a real change. set_selected() during the build emits this too, and
    acting on it would rewrite the registry every time the panel opened."""
    mode = paths.DISPLAY_MODES[dropdown.get_selected()]
    if mode != state["display"]:
        pick(panel.Action("display", slug, mode))


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
