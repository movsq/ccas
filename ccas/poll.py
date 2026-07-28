"""Fetching an account's usage limits without a session.

The statusline hook is the only source while a session runs, and there is no
session for the account you are *not* using — so its numbers freeze the moment
the last one closes. This asks the endpoint Claude Code itself asks,
`GET /api/oauth/usage`, with the token already sitting in the account's
credentials file. `docs/superpowers/specs/2026-07-27-usage-poll-design.md` has
why that route, and `docs/usage-limits-research.md` has the three that lost.

Two rules hold it up:

- **Read the credentials, never write them.** CCAS does not hold the refresh
  token to the fire itself: rotating one behind Claude Code's back risks that
  account's login and can invalidate the token a live session holds. An expired
  token is instead handed *back* to Claude Code — `claude mcp list` refreshes it
  on the way past, under its own cross-process lock — and the fresh one is read
  off disk. `CCAS_NO_TOKEN_REFRESH=1` takes that away again and restores the
  eight-hour horizon this had before, where an idle account simply went quiet.
- **Never fetch what the hook already knows.** `due()` skips an account recorded
  more recently than the interval, so the account being used stays the hook's
  and the timer serves the idle one.

No signalling here. The caller owns the bar, the same way `cmd_statusline` does.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections import namedtuple

from . import accounts, paths, usage

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# Seconds between polls of one account. The timer fires at this cadence too; the
# check is here as well because a hand-run `ccs poll` must not spend a request
# on numbers the hook wrote seconds ago.
INTERVAL = 300.0

# A token this close to expiry will not survive the round trip.
LEEWAY = 60.0

TIMEOUT = 5.0

OK, UNCHANGED, SKIPPED, EXPIRED, FAILED, NO_TOKEN = (
    "ok", "unchanged", "skipped", "expired", "failed", "no-token")

Outcome = namedtuple("Outcome", "slug status detail")


def access_token(slug: str):
    """`(token, expires_at)` in seconds, or None. Read-only, always."""
    path = paths.account_dir(slug) / ".credentials.json"
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # The file nests under claudeAiOauth; tolerate a flat one rather than
    # assuming a shape CCAS does not own.
    oauth = blob.get("claudeAiOauth") or blob
    token, expires = oauth.get("accessToken"), oauth.get("expiresAt")
    if not token or not expires:
        return None
    return token, expires / 1000.0  # the file is milliseconds, CCAS is seconds


def renewal_wanted() -> bool:
    """The off switch, read at the point of use so a unit override takes hold
    without an install. `CCAS_NO_TOKEN_REFRESH=1` and the poll is back to what
    it was: an expired token is the end of the attempt."""
    return os.environ.get("CCAS_NO_TOKEN_REFRESH") != "1"


# What renews a token, measured 2026-07-28 against an account five hours past
# expiry. Not `claude auth status`: that reads the credentials and reports
# `loggedIn: true` over an expired token without touching it, which is how the
# first version of this shipped inert. A token is refreshed as a side effect of
# a **first-party API call**, which the bundle makes with `refreshOAuth: true`,
# and `mcp list` is the cheapest CLI command that makes one — about 2.5s, and no
# model quota. It is a side effect, so it is not promised: `renew()` re-reads
# rather than trusting it, and the poll degrades to the old eight-hour horizon
# the day a claude release stops doing it, visibly, in doctor's freshness row.
RENEW_ARGS = ("mcp", "list")
RENEW_TIMEOUT = 45.0


def ask_claude(slug: str, runner=None) -> None:
    """Run the renewing command under that account's `CLAUDE_CONFIG_DIR`.

    Nothing is read back from it — the answer is on disk, and `renew` looks
    there. `accounts.env_for` carries the `CCAS_INNER=1` guard.
    """
    runner = subprocess.run if runner is None else runner
    runner([str(paths.claude_bin()), *RENEW_ARGS], env=accounts.env_for(slug),
           capture_output=True, text=True, timeout=RENEW_TIMEOUT)


def renew(slug: str, asker=None):
    """Ask Claude Code to renew this account's token; re-read what it left.

    `(token, expires_at)` if a usable one is now on disk, else None. CCAS writes
    nothing here — claude refreshes the credentials for its own account, which
    is the whole point of routing through it rather than posting to the token
    endpoint ourselves.

    Never raises: the timer runs unattended, and a claude that is missing, slow
    or logged out is the same event as far as the caller is concerned — no fresh
    token this time.
    """
    asker = ask_claude if asker is None else asker
    try:
        asker(slug)
    except Exception:  # noqa: BLE001 — see the docstring
        return None
    return access_token(slug)


def due(slug: str, now: float, interval: float = INTERVAL) -> bool:
    """Is this account's 5-hour window one the hook is not keeping up with?

    `usage.load`, not `usage.read`: the question is when the account last had a
    reading of its own, which is what the hook writes.

    Two tests, and the second one is not redundant. `fetched_at` says when the
    *file* last changed, and Claude Code names the two windows independently —
    a statusline tick carrying only `seven_day` writes, because seven_day moved,
    and `usage._carried` rightly keeps the stored 5-hour window rather than
    erasing it with silence. That write moves `fetched_at` without anybody
    having measured the 5-hour window at all. Read alone it says "the hook has
    this account", and on 2026-07-28 it locked the timer out of an account whose
    5-hour window had rolled over mid-session: the endpoint knew about the new
    window for twenty minutes and was never asked, the bar read idle throughout,
    and only a panel click — gated by `poll-stamp`, not by this — broke it.

    So a reading whose 5-hour window is not running is due whatever its
    timestamp says. It is the window the freshness is *about*: BOUNDED is the
    only state the hook can be said to be keeping up with, and an account that
    is genuinely idle was being polled at the interval anyway, because nothing
    was writing its file either.
    """
    reading = usage.load(slug)
    if reading is None:
        return True
    if usage.state(reading, "five_hour", now).kind is not usage.BOUNDED:
        return True
    return (now - reading.get("fetched_at", 0.0)) >= interval


def age(seconds: float) -> str:
    """Short, for an outcome line and for doctor's freshness row."""
    seconds = int(max(0, seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds / 3600:.1f}h"


def fetch(token: str, opener=None):
    """`(payload, "")`, or `(None, reason)`. Never raises.

    Everything that can go wrong here is the same event as far as the caller is
    concerned — no fresh reading this time — so the reason is a string for the
    user to read rather than an exception for the timer to trip over.
    """
    opener = urllib.request.urlopen if opener is None else opener
    request = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace")), ""
    except urllib.error.HTTPError as exc:
        return None, f"http {exc.code}"
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return None, f"{type(exc).__name__}: {exc}"


def poll_account(slug: str, now=None, fetcher=None, force=False,
                 renewer=None) -> Outcome:
    """One account: decide, renew if it must, fetch, record. Never raises."""
    now = time.time() if now is None else now
    fetcher = fetch if fetcher is None else fetcher

    if not force and not due(slug, now):
        recorded = (usage.load(slug) or {}).get("fetched_at", now)
        return Outcome(slug, SKIPPED, f"recorded {age(now - recorded)} ago")

    credentials = access_token(slug)
    if credentials is None:
        return Outcome(slug, NO_TOKEN, "no usable credentials — is it logged in?")
    token, expires_at = credentials
    if expires_at <= now + LEEWAY:
        when = (f"expired {age(now - expires_at)} ago" if expires_at <= now
                else "expires within the minute")
        if not renewal_wanted():
            return Outcome(slug, EXPIRED,
                           f"token {when} — no fetch; renewal is switched off")
        renewed = renew(slug, renewer)
        if renewed is None or renewed[1] <= now + LEEWAY:
            return Outcome(slug, EXPIRED,
                           f"token {when} — claude did not renew it; is it logged in?")
        token, expires_at = renewed

    payload, error = fetcher(token)
    if payload is None:
        return Outcome(slug, FAILED, f"{error} — reading unchanged")
    reading = usage.from_oauth(payload, now)
    if reading is None:
        return Outcome(slug, FAILED, "no windows in the response — reading unchanged")
    if usage.record_reading(slug, reading):
        return Outcome(slug, OK, usage.column(reading, "five_hour", now))
    return Outcome(slug, UNCHANGED, "same numbers")


# ── the on-demand poll ────────────────────────────────────────────────────────

# The shortest gap between two panel-driven fetches for one account. Separate
# from INTERVAL on purpose: that one answers "how often should this account be
# refreshed in the background", and this one answers "how often may a human
# asking spend a request". They are free to differ, and do.
PANEL_INTERVAL = 60.0


def asked_at(slug: str):
    """When this account last asked the endpoint, or None.

    Its own file, not a key in the reading. `usage.record` defines `fetched_at`
    as *when this reading was first seen* — the write-on-change rule leaves it
    alone while the numbers hold steady — so for an idle account whose usage is
    not moving it is permanently older than any interval, and `due()` built on
    it is permanently true. That is right for the timer, whose cadence is the
    point, and useless as a rate limit. This is the other fact: when we last
    asked, whatever the answer was.

    Unreadable reads as never asked. Failing open costs one request; failing
    shut would wedge an account's panel refresh on a stray byte.
    """
    try:
        return float((paths.account_dir(slug) / paths.POLL_STAMP_FILE)
                     .read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def stamp(slug: str, now: float) -> None:
    """Record that we asked. Never raises — a read-only account directory means
    no rate limiting, not a panel that fails to open."""
    try:
        path = paths.account_dir(slug) / paths.POLL_STAMP_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{now:.3f}\n", encoding="utf-8")
    except OSError:
        pass


def _never_renew(_slug: str) -> None:
    """The renewer the on-demand path passes: it asks claude nothing.

    Renewal is a process and up to RENEW_TIMEOUT seconds, and the five-minute
    timer has already done it by the time a panel is opened. `renew()` re-reads
    the credentials after calling this, finds the same expired token, and
    poll_account reports EXPIRED without spending a request.
    """


def poll_on_demand(slug: str, now=None, fetcher=None,
                   interval: float = PANEL_INTERVAL) -> Outcome:
    """One account, because someone opened the panel and is looking at it.

    The stamp is written **before** the fetch. A fetch that fails, or hangs for
    the full TIMEOUT, still counts as having asked — stamping on success only
    would turn a broken network into an unthrottled retry, one request per panel
    open, which is the precise behaviour the limit is here to prevent. The
    endpoint is rate limited on Anthropic's side (Claude Code's own bundle
    carries a `rateLimitedVia` fallback; `fetch` has none), so spending requests
    that cannot succeed is the worst thing this could do.

    `force=True` into poll_account, deliberately: the stamp above is this path's
    gate, and `due()` is the timer's. Letting both apply would put the 5-minute
    interval back in front of a question a human just asked.
    """
    now = time.time() if now is None else now
    last = asked_at(slug)
    if last is not None and (now - last) < interval:
        return Outcome(slug, SKIPPED, f"asked {age(now - last)} ago")
    stamp(slug, now)
    return poll_account(slug, now, fetcher, force=True, renewer=_never_renew)


TIMER = "ccas-poll.timer"


def timer_state(runner=None) -> str:
    """systemd's own word for the timer, or "unknown" if it cannot be asked.

    A string rather than a bool: "inactive" and "failed" want different advice,
    and a machine without systemd is neither.
    """
    runner = subprocess.run if runner is None else runner
    try:
        proc = runner(["systemctl", "--user", "is-active", TIMER],
                      capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (getattr(proc, "stdout", "") or "").strip() or "unknown"


def poll(slugs, now=None, fetcher=None, force=False, renewer=None) -> list:
    """Every account, one outcome each. The timer runs this unattended, so one
    broken account may not stop the rest."""
    return [poll_account(slug, now, fetcher, force, renewer) for slug in slugs]
