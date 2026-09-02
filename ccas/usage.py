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

# Above this a weekly window is the binding constraint and takes the label. It
# was SEVEN_DAY_TAKES_OVER, and the rule was never about that window in
# particular: a weekly reset is days away, so above the threshold the number is
# the actionable fact where the 5-hour clock is. The model-scoped window is
# weekly too, and binds the same way.
WEEKLY_TAKES_OVER = 90

NOT_WIRED = "usage — statusline hook not wired"
# The same fact where the command itself already says the subject is usage.
NO_DATA = "no data — statusline hook not wired"
# How an IDLE window is said, wherever it is said. Not "full": that reads
# backwards, as full of usage rather than full of quota.
IDLE_TEXT = "0% used"
# The same fact in the width of a clock, for the label's reset and time-left
# tokens. They rendered empty for an idle window, which was truthful and
# unreadable: `%5hreset %5hquotaleft` collapsed to a bare `100%`, and a lone
# round number on a usage widget reads as 100% *used*. A word in the clock's
# place says which of the two it is without needing the format string changed.
IDLE_MARK = "idle"

# The two windows every subscription has, and the one it may not.
PLAN_WINDOWS = ("five_hour", "seven_day")

# The model-scoped weekly window: the key it is stored under, and — because the
# key is the model's own name in lower case — the prefix its display name is
# matched on. Claude Code meters one bucket per model its allowlist names
# (`tengu_usage_overage_included_models`, which on this machine reads
# ["Fable", "Fable 5"]), and Fable is the only bucket either account has been
# sent. A prefix so the version in the display name cannot retire the match;
# another model appearing is another commit, not a read path that understands
# both.
FABLE = "fable"

# Windows an account may simply not have, where the plan windows are only ever
# quiet. It is the whole difference between a meter and a meter for a limit that
# does not exist: measured 2026-08-17, one account's body carried a Fable window
# and the other's did not name one at all.
SCOPED = (FABLE,)

WINDOWS = PLAN_WINDOWS + SCOPED

# How each is named in a line the user reads. `fable` is spelled out: "7d" says
# which window by its length, and two weekly windows cannot both do that.
SHORT = {"five_hour": "5h", "seven_day": "7d", FABLE: "fable"}

ABSENT, IDLE, BOUNDED = "absent", "idle", "bounded"
State = namedtuple("State", "kind percent resets_at")


# ── the three input shapes ────────────────────────────────────────────────────

def _window(percent, resets_at):
    if percent is None or resets_at is None:
        return None
    return {"percent": float(percent), "resets_at": int(resets_at)}


def _scoped_window(percent, resets_at):
    """A model-scoped window, which a missing reset does not erase.

    The plan windows drop both halves together because a five_hour entry with no
    reset is a window that is not running, and a null reads as IDLE anyway. Here
    the entry is the *only* evidence the account has this window at all, so the
    percentage is kept without one: a Fable window that exists and is idle must
    not read as an account with no Fable window.
    """
    if percent is None:
        return None
    return {"percent": float(percent),
            "resets_at": None if resets_at is None else int(resets_at)}


def _is_fable(name) -> bool:
    return isinstance(name, str) and name.casefold().startswith(FABLE)


def _from_limits(limits):
    """The Fable window out of a `limits[]` array — the cache's shape and the
    endpoint's, which are the same shape.

    It is not a top-level key beside five_hour and seven_day: the body carries
    `seven_day_opus` and `seven_day_sonnet` as nulls and names the model bucket
    only here, as the weekly entry whose `scope.model` is set. Measured on the
    live endpoint 2026-08-17; `docs/usage-limits-research.md` quotes it.
    """
    for entry in limits or []:
        if not isinstance(entry, dict) or entry.get("kind") != "weekly_scoped":
            continue
        scope = entry.get("scope") or {}
        model = (scope.get("model") or {}) if isinstance(scope, dict) else {}
        if _is_fable(model.get("display_name")):
            return _scoped_window(entry.get("percent"),
                                  _epoch(entry.get("resets_at")))
    return None


def _from_model_scoped(entries):
    """The Fable window out of the hook's `rate_limits.model_scoped`.

    The bundle projects the same `limits[]` into this array, filtered by its
    allowlist — and rewrites it on the way: `utilization` where its siblings in
    the same payload say `used_percentage`, and an **ISO string** where their
    `resets_at` is epoch seconds. Two spellings inside one payload, so this
    cannot share `from_statusline`'s reader.
    """
    for entry in entries or []:
        if isinstance(entry, dict) and _is_fable(entry.get("display_name")):
            return _scoped_window(entry.get("utilization"),
                                  _epoch(entry.get("resets_at")))
    return None


def _reading(source, fetched_at, windows):
    if not any(windows.values()):
        return None  # a payload naming no window is not a reading
    return {"fetched_at": float(fetched_at), "source": source, **windows}


def from_statusline(payload: dict, now: float):
    """The hook's JSON. used_percentage is 0-100, resets_at is epoch seconds."""
    limits = (payload or {}).get("rate_limits") or {}
    return _reading("statusline", now, {
        **{key: _window(limits.get(key, {}).get("used_percentage"),
                        limits.get(key, {}).get("resets_at"))
           for key in PLAN_WINDOWS},
        FABLE: _from_model_scoped(limits.get("model_scoped"))})


def _epoch(iso: str):
    """The cache's ISO 8601, which carries both a trailing Z and fractions.

    Rounded to the **minute**, which is where a reset anchor actually lands —
    the statusline hands the same window a whole epoch on the minute, and every
    ISO string measured is within a second of one. The fraction is jitter, and
    truncating it made the jitter visible: three responses for one window on
    2026-07-28, twenty seconds apart, read `16:09:59.820`, `16:10:00.488` and
    `16:09:59.956`, so `int()` quantised them to two different seconds — two
    different *minutes* — and the bar's `%5hreset` flipped 18:09 ↔ 18:10 on
    every poll. Each flip was a change: a write, a signal and a repaint for a
    window that had not moved, and half of them named the wrong minute. It also
    made a hook reading and a poll reading of one window disagree by a second,
    so the two sources overwrote each other in turn.
    """
    try:
        return round(datetime.fromisoformat(iso).timestamp() / 60) * 60
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
        **{key: _window(util.get(key, {}).get("utilization"),
                        _epoch(util.get(key, {}).get("resets_at")))
           for key in PLAN_WINDOWS},
        FABLE: _from_limits(util.get("limits"))})


def from_oauth(payload: dict, now: float):
    """The `/api/oauth/usage` body — the third shape of the same fact.

    Same spelling as the cache (0-100 under `utilization`, an ISO 8601
    `resets_at`) without the `cachedUsageUtilization` wrapper, because this is
    what Claude Code stores *into* that key. The real body also carries
    `seven_day_opus` and friends as nulls; naming none of the windows we read
    makes it a non-reading, not an empty one.
    """
    payload = payload or {}
    return _reading("oauth", now, {
        **{key: _window((payload.get(key) or {}).get("utilization"),
                        _epoch((payload.get(key) or {}).get("resets_at")))
           for key in PLAN_WINDOWS},
        FABLE: _from_limits(payload.get("limits"))})


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


def _carried(stored, reading):
    """`reading`, with any window it does not name taken from `stored`.

    A payload naming neither window is already not a reading. This is the same
    rule one level finer: naming *one* window is a statement about that window
    and silence about the other, and silence must not overwrite a measurement.
    Found on the bar 2026-07-28 — an account with a live 5-hour window read
    `100%` out of nowhere, because a statusline tick carrying only `seven_day`
    wrote `five_hour: null` over it, and a null window is IDLE, and IDLE is
    0% used, and `%5hquotaleft` is 100 minus that.

    Carrying cannot resurrect a window that really did roll over: `resets_at` is
    an absolute anchor, so `state()` calls a carried window whose reset has
    passed IDLE from the timestamp alone. The worst a carry can do is keep a
    true fact one tick longer than the source mentioned it.
    """
    if stored is None:
        return reading
    return {**reading, **{key: stored.get(key) for key in WINDOWS
                          if reading.get(key) is None}}


def record_reading(slug: str, reading) -> bool:
    """Write an already-normalised reading if it says something new.

    The half of `record` that does not care which source produced the reading —
    the poll reaches the same write-on-change rule as the hook, and with it the
    signal that rides on a write.
    """
    if reading is None:
        return False  # a payload with no windows must not erase a good one
    stored = load(slug)
    reading = _carried(stored, reading)
    if _same(stored, reading):
        return False
    _atomic_write(paths.account_dir(slug) / paths.USAGE_FILE,
                  json.dumps(reading, indent=2) + "\n")
    return True


# ── the stall: why the poll stopped answering ─────────────────────────────────

def stall(slug: str):
    """What the poll last could not do, or None if it is answering.

    A dict of `reason` (the outcome's own word, plus its detail for the ones
    that have one), `since` and `at` — the first failure of this run and the
    latest — `failures`, and `retry_at`, the earliest the poll may ask again.
    """
    path = paths.account_dir(slug) / paths.POLL_FAIL_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def record_stall(slug: str, reason: str, now: float, retry_at=None,
                 counts: bool = True, note: str = "") -> bool:
    """Note that the poll could not answer. True when it wrote.

    Write-on-change, the rule the readings obey and for the same reason: a
    logged-out account is polled every five minutes for as long as it stays
    logged out, and the bar must not repaint at that cadence for a fact that has
    not moved. `counts` is False where nothing was asked — no token is a stop
    without a request behind it, so it has no failure to count and no wait to
    grow, and after the first write every later tick is a no-op.
    """
    previous = stall(slug) or {}
    failures = previous.get("failures", 0) + 1 if counts \
        else previous.get("failures", 0)
    record = {"reason": reason,
              "note": note,
              "since": previous.get("since", now),
              "at": now,
              "failures": failures,
              "retry_at": retry_at}
    if all(previous.get(key) == record[key]
           for key in ("reason", "note", "failures", "retry_at")):
        return False
    _atomic_write(paths.account_dir(slug) / paths.POLL_FAIL_FILE,
                  json.dumps(record, indent=2) + "\n")
    return True


def clear_stall(slug: str) -> bool:
    """The poll answered. True when there was a stall to clear.

    The file is removed rather than emptied — `stall()` reading None is the
    absence of a stall, and one shape for that is one shape to get right. This
    is the one place in CCAS that unlinks, and it is exempt from the trash rule
    on purpose: it is a lock-shaped file CCAS wrote itself two minutes ago, not
    anything the user could want back.
    """
    path = paths.account_dir(slug) / paths.POLL_FAIL_FILE
    try:
        path.unlink()
        return True
    except OSError:
        return False


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

    ABSENT is *no reading*, and nothing else. A reading that names no window is
    IDLE: the endpoint stops reporting a window an account has not been using,
    which is not ignorance — it says the quota refilled. Both were ABSENT once,
    and an account left alone lost its whole label at the moment its window
    rolled over, with the panel calling a measurement "no data".

    IDLE carries 0.0, not None, because that is the fact: nothing has been spent
    in a window that is not running. It is also what keeps `%5hused` rendering,
    and it makes a bar at zero *mean* zero — ignorance is a separate kind now.
    The percentage from before the reset is not carried across; it is dead the
    moment the window rolls over.

    That last rule inverts for a SCOPED window, and only for the unnamed case.
    "The payload did not name it" means the quota refilled when every account
    has the window; for a model-scoped one it means this account was never told
    about that model, which is ignorance of a limit rather than a measurement of
    one. So an unnamed Fable window is ABSENT and drops out of the panel and the
    label entirely, while a Fable window that was named once and has since
    rolled over is IDLE from its own timestamp, exactly like the other two.
    """
    now = time.time() if now is None else now
    if reading is None:
        return State(ABSENT, None, None)
    window = reading.get(key)
    if not window:
        return State(ABSENT, None, None) if key in SCOPED \
            else State(IDLE, 0.0, None)
    if not window["resets_at"] or window["resets_at"] <= now:
        return State(IDLE, 0.0, None)
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


# The weekly windows, in the order a tie between them is broken. Ties do not
# happen in practice — one is every model's usage and the other one model's —
# but "the highest one binds" needs an order to be a function.
WEEKLY_WINDOWS = ("seven_day", FABLE)


def _binding_weekly(reading, now):
    """`(key, State)` for the weekly window that is over the threshold, highest
    first, or None. A window an account does not have cannot bind: ABSENT and
    IDLE are both simply not BOUNDED here."""
    over = [(key, state(reading, key, now)) for key in WEEKLY_WINDOWS]
    over = [pair for pair in over if pair[1].kind == BOUNDED
            and pair[1].percent >= WEEKLY_TAKES_OVER]
    return max(over, key=lambda pair: pair[1].percent) if over else None


def bar(reading, now=None):
    """`(text, pango attributes)` for the label, or None when there is nothing
    to warn about — open and absent both render as the bar you have today.

    The clock, not the percentage: colour carries the level, which is what lets
    the text stay the one fact that is still true from a stale reading.
    """
    five = state(reading, "five_hour", now)

    binding = _binding_weekly(reading, now)
    if binding is not None:
        key, week = binding
        if week.percent > (five.percent if five.kind == BOUNDED else -1):
            # Its reset is days away, so the number is the actionable fact, not
            # the clock. The only case where the label names a weekly window.
            return f"{SHORT[key]} {week.percent:.0f}%", f"color='{RED}'"

    if five.kind != BOUNDED:
        return None
    value = color(five.percent)
    return reset_clock(five.resets_at), \
        (f"color='{value}'" if value else f"alpha='{DIM}'")


def _line(key: str, st: State, now: float) -> str:
    if st.kind == IDLE:
        return f"{SHORT[key]} {IDLE_TEXT}"
    # ≥ because usage only climbs within a window: the recorded percentage is a
    # lower bound for as long as its reset is still ahead.
    return (f"{SHORT[key]} ≥{st.percent:.0f}% · "
            f"clears {reset_time(st.resets_at, now)}")


def column(reading, key: str, now: float) -> str:
    """One window, said in full — for `ccs usage`, which shows both plan windows
    however quiet either is, and so cannot leave one out the way the picker
    does."""
    st = state(reading, key, now)
    if st.kind == ABSENT:
        return f"{SHORT[key]} —"
    if st.kind == IDLE:
        return f"{SHORT[key]} {IDLE_TEXT}"
    return (f"{SHORT[key]} ≥{st.percent:.0f}% "
            f"clears {reset_time(st.resets_at, now)}")


def lines(reading, now=None):
    """What the picker says: one line always, a second only when a weekly window
    binds."""
    now = time.time() if now is None else now
    five = state(reading, "five_hour", now)
    seven = state(reading, "seven_day", now)
    if five.kind == ABSENT and seven.kind == ABSENT:
        return [NOT_WIRED]
    out = [_line("five_hour", five, now)] if five.kind != ABSENT else []
    binding = _binding_weekly(reading, now)
    if binding is not None:
        out.append(_line(binding[0], binding[1], now))
    return out or [NOT_WIRED]
