"""The panel's state: everything decided before a widget exists.

Deliberately free of GTK. build_state() turns the registry, the usage reading
and the session history into a plain dict; filter_state() narrows it. The widget
tree in panel_ui.py renders that dict and decides nothing, which is what keeps
the panel's behaviour inside the test suite rather than inside a toolkit.

test_panel_never_imports_gi pins the split.
"""
import time
from collections import namedtuple

from . import history, label, paths, registry, usage

# What the widget tree hands back. `kind` picks the branch cli.dispatch_panel
# takes, `slug` says whose, and `value` is that branch's argument — None for the
# kinds that take none.
Action = namedtuple("Action", "kind slug value")

WINDOW_LABELS = {"five_hour": "5h", "seven_day": "wk"}

UNTITLED = "(untitled)"


def _usage_rows(slug: str, now: float):
    """One row per window, always both, in a fixed order.

    An absent window keeps its row rather than disappearing: a bar that is not
    drawn and a bar at zero look identical, and they mean opposite things.
    """
    reading = usage.read(slug)
    rows = []
    for key in usage.WINDOWS:
        st = usage.state(reading, key, now)
        rows.append({
            "key": key,
            "label": WINDOW_LABELS[key],
            "kind": st.kind,
            "percent": st.percent,
            "resets": ("" if st.resets_at is None
                       else usage.reset_time(st.resets_at, now)),
            "color": None if st.percent is None else usage.color(st.percent),
        })
    return rows


def _accounts(reg: dict, slug: str):
    return [{
        "slug": a["slug"],
        "name": label.display_name(a),
        "email": a.get("email") or "",
        "color": paths.PALETTE[a["color"]][1],
        "current": a["slug"] == slug,
    } for a in reg["accounts"]]


def _panes(now: float):
    """(projects, sessions-by-project). Ordered by mtime throughout.

    history.scan() is already mtime-descending and project_dirs() preserves that
    order while dropping directories that no longer exist, so the project column
    falls out of it — the first project is the one holding the most recent
    session by construction, not by a second sort.
    """
    sessions = history.scan()[: paths.HIST_SLOTS]
    by_project = {}
    for s in sessions:
        by_project.setdefault(s.cwd, []).append({
            "uuid": s.uuid,
            "title": s.title or UNTITLED,
            "age": history.humanise_age(now - s.mtime),
            "mtime": s.mtime,
            "cwd": s.cwd,
        })
    projects = [{
        "path": d,
        "short": history.abbreviate(d),
        "age": history.humanise_age(now - by_project[d][0]["mtime"]),
        "mtime": by_project[d][0]["mtime"],
    } for d in history.project_dirs(sessions) if d in by_project]
    return projects, by_project


def build_state(slug: str, now=None) -> dict:
    """Everything the panel renders, as plain data.

    An unknown slug is tolerated rather than raised on: the bar and the registry
    can disagree for one click after an account is removed, and a dead bar
    button should open a panel that says nothing rather than traceback.
    """
    now = time.time() if now is None else now
    reg = registry.load()
    account = registry.find(reg, slug) or {}
    projects, by_project = _panes(now)
    selected = projects[0]["path"] if projects else None
    return {
        "slug": slug,
        "accounts": _accounts(reg, slug),
        "display": account.get("display", "nickname"),
        "hide_icon": bool(account.get("hide_icon")),
        "headless": bool(account.get("headless")),
        "dangerous": bool(account.get("dangerous")),
        "usage": _usage_rows(slug, now),
        "projects": projects,
        "sessions": by_project,
        "selected_project": selected,
        "selected_session": (by_project[selected][0]["uuid"] if selected
                             else None),
    }
