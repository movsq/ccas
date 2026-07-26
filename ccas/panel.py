"""The panel's state: everything decided before a widget exists.

Deliberately free of GTK. build_state() turns the registry, the usage reading
and the session history into a plain dict; filter_state() narrows it. The widget
tree in panel_ui.py renders that dict and decides nothing, which is what keeps
the panel's behaviour inside the test suite rather than inside a toolkit.

test_panel_never_imports_gi pins the split.
"""
import json
import os
import signal
import subprocess
import time
from collections import namedtuple

from . import format as fmt, history, label, paths, registry, usage

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


def filter_state(state: dict, query: str) -> dict:
    """Narrow both panes to `query`. A new dict; the argument is untouched.

    Both panes, not just the sessions: the search earns its place when you
    cannot remember which project something was in, and a left column that
    stays full while the right one empties says the opposite.

    A project whose *path* matches keeps all of its sessions, so typing a
    directory name works as a project filter rather than as a filter that finds
    the project and then hides everything inside it.

    Narrowing only — never reordering. The panes are in recency order and that
    is what makes the top row the right default; ranking matches by relevance
    would put an old project above a newer one the moment you typed.
    """
    q = query.strip().lower()
    if not q:
        return state

    sessions, projects = {}, []
    for project in state["projects"]:
        rows = state["sessions"].get(project["path"], [])
        hits = (rows if q in project["short"].lower()
                else [s for s in rows if q in s["title"].lower()])
        if hits:
            projects.append(project)
            sessions[project["path"]] = hits

    selected = projects[0]["path"] if projects else None
    return {**state,
            "projects": projects,
            "sessions": sessions,
            "selected_project": selected,
            "selected_session": (sessions[selected][0]["uuid"] if selected
                                 else None)}


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
        "format": account.get("format") or fmt.DEFAULT_FORMAT,
        "format_colors": dict(account.get("format_colors") or {}),
        # Precomputed so panel_ui parses nothing: the widget tree renders the
        # list it is handed. Ordered and de-duplicated, so the dropdown reads
        # left to right the way the label does.
        "format_tokens": fmt.tokens_in(account.get("format") or fmt.DEFAULT_FORMAT),
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


def shows_color_row(state) -> bool:
    """Whether the panel offers per-token colour. A decision, so it lives here
    and not in panel_ui, which decides nothing."""
    return state.get("display") == "custom"


# ── the open panel ───────────────────────────────────────────────────────────


def running_panel():
    """(pid, slug) of the panel that is open, or None.

    A pid that no longer exists is None, not a stale answer: the panel is killed
    with SIGTERM, which does not run the finally block that would have released
    the lock, so a leftover file is the normal case rather than the odd one.
    """
    try:
        pid_text, slug = paths.panel_lock().read_text(encoding="utf-8").split(None, 1)
        pid = int(pid_text)
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return None
    return pid, slug.strip()


def claim(slug: str) -> None:
    lock = paths.panel_lock()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{os.getpid()} {slug}", encoding="utf-8")


def release() -> None:
    paths.panel_lock().unlink(missing_ok=True)


def close_running():
    """Close whatever panel is open and say whose it was, or None if none was.

    The bar's click is a toggle. The caller compares the returned slug with the
    one it was asked to open: equal means the same widget was clicked twice and
    nothing should reopen, different means the user went to another account.
    """
    found = running_panel()
    if found is None:
        release()  # a lock with a dead pid in it, and nothing to signal
        return None
    pid, slug = found
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    release()
    return slug


def current_output():
    """The connector the pointer is on, asked of sway, or None if it cannot say.

    sway focuses the output under the pointer, so the focused output is the one
    whose bar was just clicked. None leaves the choice to the compositor, which
    is what CCAS_PANEL_OUTPUT overrides and what a non-sway session gets.
    """
    try:
        proc = subprocess.run(["swaymsg", "-t", "get_outputs"],
                              capture_output=True, text=True, timeout=2)
        outputs = json.loads(proc.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    for output in outputs:
        if isinstance(output, dict) and output.get("focused"):
            return output.get("name")
    return None
