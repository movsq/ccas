"""Generating menu.xml and its backing history.tsv snapshot.

Waybar caches menu-file at startup (see docs/superpowers/spike-waybar-menu.md),
so writing these files is not enough to refresh what the user sees. Callers must
consult session_set_changed() and reload Waybar when it returns True. That
decision lives in the caller, not here, so that writing stays side-effect free.
"""
import os
import tempfile
import time
from xml.sax.saxutils import escape

from . import paths
from .history import format_row

from .label import MARK_ON, MARK_OFF  # noqa: F401 — re-exported until menu.py goes


def _item(item_id: str, text: str) -> str:
    return (
        f'    <child><object class="GtkMenuItem" id="{item_id}">'
        f'<property name="visible">True</property>'
        f'<property name="label">{escape(text)}</property>'
        f"</object></child>\n"
    )


def _separator() -> str:
    return (
        '    <child><object class="GtkSeparatorMenuItem">'
        '<property name="visible">True</property></object></child>\n'
    )


def _title(text: str, item_id: str = "title") -> str:
    return (
        f'    <child><object class="GtkMenuItem" id="{item_id}">'
        f'<property name="visible">True</property>'
        f'<property name="sensitive">False</property>'
        f'<property name="label">{escape(text)}</property>'
        f"</object></child>\n"
    )


def _submenu(item_id: str, text: str, children: str) -> str:
    return (
        f'    <child><object class="GtkMenuItem" id="{item_id}">'
        f'<property name="visible">True</property>'
        f'<property name="label">{escape(text)}</property>\n'
        f'      <child type="submenu"><object class="GtkMenu">\n'
        f"{children}"
        f"      </object></child>\n"
        f"    </object></child>\n"
    )


def build_tsv(sessions) -> str:
    now = time.time()
    rows = []
    for s in sessions[: paths.HIST_SLOTS]:
        label = format_row(s, now).replace("\t", " ")
        rows.append(f"{label}\t{s.uuid}\t{s.cwd}")
    return "\n".join(rows) + ("\n" if rows else "")


def build_xml(account: dict, sessions) -> str:
    now = time.time()

    shown = sessions[: paths.MENU_HIST_ITEMS]
    history = _item("search", "> ...") + _separator()
    for i, s in enumerate(shown):
        history += _item(f"hist-{i}", format_row(s, now))
    if len(sessions) > len(shown):
        history += _separator() + _item(
            "search-more", f"> {len(sessions) - len(shown)} more…")

    ids = {"nickname": "disp-nickname", "index": "disp-index",
           "claude code": "disp-cc", "icon only": "disp-icon"}
    display = ""
    for mode in paths.DISPLAY_MODES:
        mark = MARK_ON if account["display"] == mode else MARK_OFF
        display += _item(ids[mode], f"{mark} {mode}")

    colors = ""
    for i, (name, _hex) in enumerate(paths.PALETTE):
        mark = MARK_ON if account["color"] == i else MARK_OFF
        colors += _item(f"color-{i}", f"{mark} {name}")

    manage = (
        _item("mng-add", "Add account…")
        + _item("mng-rename", "Rename…")
        + _item("mng-remove", "Remove this account…")
    )

    # The email identifies the account; the nickname is only a label for the
    # bar, so it sits underneath and only appears when one is set.
    heading = _title(account["email"])
    if account.get("nickname"):
        heading += _title(account["nickname"], "title-nickname")

    body = (
        heading
        + _separator()
        + _item("new", "New session")
        + _item("last", "Resume last session")
        + _submenu("history", "Resume from history", history)
        + _separator()
        + _submenu("display", "Display as", display)
        + _item("hide", (MARK_ON if account["hide_icon"] else MARK_OFF)
                + " Hide icon")
        + _submenu("color", "Color", colors)
        + _separator()
        # Not appearance like the three above it: this one decides which account
        # a headless `claude -p` runs under, so it gets its own group.
        + _item("headless", (MARK_ON if account.get("headless") else MARK_OFF)
                + " Headless runner")
        # Beside the runner row, not among the appearance ones: both answer
        # "how does this account run", not "what does it look like".
        + _item("dangerous", (MARK_ON if account.get("dangerous") else MARK_OFF)
                + " Skip permissions (dangerous)")
        + _separator()
        + _submenu("manage", "Manage", manage)
    )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<interface>\n"
        '  <object class="GtkMenu" id="menu">\n'
        f"{body}"
        "  </object>\n"
        "</interface>\n"
    )


def _atomic_write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write(account: dict, sessions) -> None:
    directory = paths.account_dir(account["slug"])
    _atomic_write(directory / "history.tsv", build_tsv(sessions))
    _atomic_write(directory / "menu.xml", build_xml(account, sessions))


def read_tsv(slug: str):
    path = paths.account_dir(slug) / "history.tsv"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append(tuple(parts))
    return rows


def session_set_changed(slug: str, sessions) -> bool:
    """True when the menu on disk no longer holds the live set of sessions.

    Deliberately set-based, not order-based. Waybar only re-parses menu.xml on
    a full SIGUSR2 reload, so this gates a visible bar flicker and must not fire
    on every 30 s render tick — and with two live Claude sessions the newest one
    changes identity every time the user switches between them, which under an
    order-based comparison reloaded the bar every few minutes for nothing.

    Reordering does change the rows the user would see, but only their order and
    their age column. A genuinely new session is what is worth a reload.
    """
    directory = paths.account_dir(slug)
    if not (directory / "menu.xml").exists() or not (directory / "history.tsv").exists():
        return True  # nothing cached yet; an empty tsv and a missing one differ
    existing = read_tsv(slug)
    capped = sessions[: paths.HIST_SLOTS]
    if len(existing) != len(capped):
        return True
    return {row[1] for row in existing} != {s.uuid for s in capped}
