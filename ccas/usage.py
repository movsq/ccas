"""Per-account usage limits: recording them, reading them back, saying them.

`docs/usage-limits-research.md` is the measurement this rests on. Two things
from it decide everything here:

- Claude Code hands the numbers to the **statusline hook** (route D), the only
  documented per-account route. CCAS cannot wire the hook itself — every
  account's settings.json is a symlink to ~/.claude/settings.json — so it ships
  `ccs statusline` and the user adds the line.
- `resets_at` is an **absolute anchor**, not a rolling recompute. That is what
  makes a stale reading still worth showing: past means the window rolled over,
  future means the recorded percentage is a valid lower bound.

Three sources, three shapes, one normalised reading; nothing outside this module
parses any of them.
"""
import json
import os
import tempfile
import time
from collections import namedtuple
from datetime import datetime

from . import paths

# The mocha values paths.PALETTE carries, spelled again rather than indexed out
# of it: the palette is the user's per-account colour choice and its order is
# theirs to change, so PALETTE[6] for "yellow" would break the first time an
# account is recoloured.
YELLOW, PEACH, RED = "#f9e2af", "#fab387", "#f38ba8"
# A pango alpha, not a colour: below the ramp the clock should recede rather
# than read as an alert.
DIM = "40000"
RAMP = ((95, RED), (80, PEACH), (50, YELLOW))

# Above this the 7-day window is the binding constraint and takes the label.
SEVEN_DAY_TAKES_OVER = 90

NOT_WIRED = "usage — statusline hook not wired"
# The same fact where the command itself already says the subject is usage.
NO_DATA = "no data — statusline hook not wired"

WINDOWS = ("five_hour", "seven_day")

ABSENT, OPEN, BOUNDED = "absent", "open", "bounded"
State = namedtuple("State", "kind percent resets_at")


# ── the three input shapes ────────────────────────────────────────────────────

def _window(percent, resets_at):
    if percent is None or resets_at is None:
        return None
    return {"percent": float(percent), "resets_at": int(resets_at)}


def _reading(source, fetched_at, windows):
    if not any(windows.values()):
        return None  # a payload naming neither window is not a reading
    return {"fetched_at": float(fetched_at), "source": source, **windows}


def from_statusline(payload: dict, now: float):
    """The hook's JSON. used_percentage is 0-100, resets_at is epoch seconds."""
    limits = (payload or {}).get("rate_limits") or {}
    return _reading("statusline", now, {
        key: _window(limits.get(key, {}).get("used_percentage"),
                     limits.get(key, {}).get("resets_at"))
        for key in WINDOWS})


def _epoch(iso: str):
    """The cache's ISO 8601, which carries both a trailing Z and fractions."""
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except (TypeError, ValueError):
        return None


def from_cache(blob: dict):
    """`cachedUsageUtilization` out of an account's .claude.json.

    An opportunistic cache: only `/usage` inside a session writes it. It is the
    fallback for an account whose hook has never fired.
    """
    cached = (blob or {}).get("cachedUsageUtilization") or {}
    util = cached.get("utilization") or {}
    return _reading("cache", cached.get("fetchedAtMs", 0) / 1000, {
        key: _window(util.get(key, {}).get("utilization"),
                     _epoch(util.get(key, {}).get("resets_at")))
        for key in WINDOWS})


def from_oauth(payload: dict, now: float):
    """The `/api/oauth/usage` body — the third shape of the same fact.

    Same spelling as the cache (0-100 under `utilization`, an ISO 8601
    `resets_at`) without the `cachedUsageUtilization` wrapper, because this is
    what Claude Code stores *into* that key. The real body also carries
    `seven_day_opus` and friends as nulls; naming neither of the two windows we
    read makes it a non-reading, not an empty one.
    """
    payload = payload or {}
    return _reading("oauth", now, {
        key: _window((payload.get(key) or {}).get("utilization"),
                     _epoch((payload.get(key) or {}).get("resets_at")))
        for key in WINDOWS})


# ── which account ─────────────────────────────────────────────────────────────

def account_slug(environ=None):
    """The slug CLAUDE_CONFIG_DIR names, or None when it is not an account.

    Unset or ~/.claude is the default account — which is where an agent session
    most likely runs. Recording there would mean writing inside ~/.claude, so
    the never-write-to-~/.claude guarantee holds here by construction: this
    returns None and the caller records nothing.
    """
    environ = os.environ if environ is None else environ
    value = environ.get("CLAUDE_CONFIG_DIR")
    if not value:
        return None
    directory = os.path.realpath(value)
    root = os.path.realpath(paths.accounts_root())
    parent, slug = os.path.split(directory)
    return slug if parent == root and slug else None


# ── recording ─────────────────────────────────────────────────────────────────

def _same(a, b) -> bool:
    return a is not None and b is not None and \
        all(a.get(key) == b.get(key) for key in WINDOWS)


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


def load(slug: str):
    """The recorded reading, or None."""
    path = paths.account_dir(slug) / paths.USAGE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def record_reading(slug: str, reading) -> bool:
    """Write an already-normalised reading if it says something new.

    The half of `record` that does not care which source produced the reading —
    the poll reaches the same write-on-change rule as the hook, and with it the
    signal that rides on a write.
    """
    if reading is None:
        return False  # a payload with no windows must not erase a good one
    if _same(load(slug), reading):
        return False
    _atomic_write(paths.account_dir(slug) / paths.USAGE_FILE,
                  json.dumps(reading, indent=2) + "\n")
    return True


def record(slug: str, payload: dict, now=None) -> bool:
    """Write the hook's payload if it says something new. True when it wrote.

    Conditional on purpose. The statusline fires on the order of every few
    hundred milliseconds and utilisation moves every few minutes, so writing
    unconditionally would be thousands of writes an hour — and would make the
    per-module signal that rides on a write unaffordable. `fetched_at` therefore
    means "when this reading was first seen", which is the more useful of the
    two and the only one obtainable without a write per tick.
    """
    now = time.time() if now is None else now
    return record_reading(slug, from_statusline(payload, now))


def read(slug: str):
    """The freshest reading for this account, hook or cache, or None."""
    recorded = load(slug)
    try:
        blob = json.loads((paths.account_dir(slug) / ".claude.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        blob = {}
    cached = from_cache(blob)
    if recorded is None:
        return cached
    if cached is None:
        return recorded
    return cached if cached["fetched_at"] > recorded["fetched_at"] else recorded


# ── classification ────────────────────────────────────────────────────────────

def state(reading, key: str, now=None) -> State:
    """Where one window stands. Three states, and no age cutoff anywhere.

    A 5-hour reading older than five hours necessarily has a reset in the past,
    so "stale" and "rolled over" are the same test — there is no threshold to
    pick and none to get wrong.
    """
    now = time.time() if now is None else now
    window = (reading or {}).get(key)
    if not window:
        return State(ABSENT, None, None)
    if window["resets_at"] <= now:
        return State(OPEN, window["percent"], window["resets_at"])
    return State(BOUNDED, window["percent"], window["resets_at"])


def color(percent: float):
    """The ramp, or None below it — meaning "render dim, not coloured"."""
    for threshold, value in RAMP:
        if percent >= threshold:
            return value
    return None


# ── saying it ─────────────────────────────────────────────────────────────────

def reset_clock(epoch: int) -> str:
    """For the bar. A 5-hour window cannot be more than five hours out, so
    there is no day to disambiguate."""
    return time.strftime("%H:%M", time.localtime(epoch))


def reset_time(epoch: int, now: float) -> str:
    """For the picker and `ccs usage`, where the 7-day window also appears and
    a 5-hour one can cross midnight."""
    same_day = time.localtime(epoch)[:3] == time.localtime(now)[:3]
    return time.strftime("%H:%M" if same_day else "%a %H:%M", time.localtime(epoch))


def bar(reading, now=None):
    """`(text, pango attributes)` for the label, or None when there is nothing
    to warn about — open and absent both render as the bar you have today.

    The clock, not the percentage: colour carries the level, which is what lets
    the text stay the one fact that is still true from a stale reading.
    """
    five = state(reading, "five_hour", now)
    seven = state(reading, "seven_day", now)

    if seven.kind == BOUNDED and seven.percent >= SEVEN_DAY_TAKES_OVER \
            and seven.percent > (five.percent if five.kind == BOUNDED else -1):
        # Its reset is days away, so the number is the actionable fact, not the
        # clock. The only case where the label mentions the 7-day window at all.
        return f"7d {seven.percent:.0f}%", f"color='{RED}'"

    if five.kind != BOUNDED:
        return None
    value = color(five.percent)
    return reset_clock(five.resets_at), \
        (f"color='{value}'" if value else f"alpha='{DIM}'")


def _line(key: str, st: State, now: float) -> str:
    short = "5h" if key == "five_hour" else "7d"
    if st.kind == OPEN:
        return f"{short} window open"
    # ≥ because usage only climbs within a window: the recorded percentage is a
    # lower bound for as long as its reset is still ahead.
    return f"{short} ≥{st.percent:.0f}% · clears {reset_time(st.resets_at, now)}"


def column(reading, key: str, now: float) -> str:
    """One window, said in full — for `ccs usage`, which shows both however
    quiet either is, and so cannot leave one out the way the picker does."""
    st = state(reading, key, now)
    short = "5h" if key == "five_hour" else "7d"
    if st.kind == ABSENT:
        return f"{short} —"
    if st.kind == OPEN:
        return f"{short} window open"
    return f"{short} ≥{st.percent:.0f}% clears {reset_time(st.resets_at, now)}"


def lines(reading, now=None):
    """What the picker says: one line always, a second only when 7d binds."""
    now = time.time() if now is None else now
    five = state(reading, "five_hour", now)
    seven = state(reading, "seven_day", now)
    if five.kind == ABSENT and seven.kind == ABSENT:
        return [NOT_WIRED]
    out = [_line("five_hour", five, now)] if five.kind != ABSENT else []
    if seven.kind == BOUNDED and seven.percent >= SEVEN_DAY_TAKES_OVER:
        out.append(_line("seven_day", seven, now))
    return out or [NOT_WIRED]
