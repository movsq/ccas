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
from .label import display_name


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


def _title(text: str) -> str:
    return (
        f'    <child><object class="GtkMenuItem" id="title">'
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
        mark = "●" if account["display"] == mode else "○"
        display += _item(ids[mode], f"{mark} {mode}")

    colors = ""
    for i, (name, _hex) in enumerate(paths.PALETTE):
        mark = "●" if account["color"] == i else "○"
        colors += _item(f"color-{i}", f"{mark} {name}")

    manage = (
        _item("mng-add", "Add account…")
        + _item("mng-rename", "Rename…")
        + _item("mng-remove", "Remove this account…")
    )

    body = (
        _title(display_name(account))
        + _separator()
        + _item("new", "New session")
        + _item("last", "Resume last session")
        + _submenu("history", "Resume from history", history)
        + _separator()
        + _submenu("display", "Display as", display)
        + _item("hide", ("☑" if account["hide_icon"] else "☐") + " Hide icon")
        + _submenu("color", "Color", colors)
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
    """True when the menu on disk no longer matches the live session set.

    Cheap by design: a row count plus the newest uuid. Waybar only re-parses
    menu.xml on a full SIGUSR2 reload, so this gates a visible bar flicker and
    must not fire on every 30 s render tick.
    """
    existing = read_tsv(slug)
    capped = sessions[: paths.HIST_SLOTS]
    if len(existing) != len(capped):
        return True
    if not existing:
        return False
    return existing[0][1] != capped[0].uuid
