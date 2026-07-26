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


def build_body(state, chosen, window):
    """The panel's root widget. Filled in by the header/panes/toggles builders."""
    from gi.repository import Gtk
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
