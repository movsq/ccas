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
  token is instead handed *back* to Claude Code — `claude auth status` renews it
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


def renew(slug: str, asker=None):
    """Ask Claude Code to renew this account's token; re-read what it left.

    `(token, expires_at)` if a usable one is now on disk, else None. CCAS writes
    nothing here — `claude auth status` reads the credentials for that account
    and refreshes an expired token on its way, which is the whole point of
    routing through it rather than posting to the token endpoint ourselves.

    Never raises: the timer runs unattended, and a claude that is missing, slow
    or logged out is the same event as far as the caller is concerned — no fresh
    token this time.
    """
    asker = accounts.auth_status if asker is None else asker
    try:
        asker(slug)
    except Exception:  # noqa: BLE001 — see the docstring
        return None
    return access_token(slug)


def due(slug: str, now: float, interval: float = INTERVAL) -> bool:
    """Has it been long enough since this account last recorded anything?

    `usage.load`, not `usage.read`: the question is when the account last had a
    reading of its own, which is what the hook writes.
    """
    reading = usage.load(slug)
    if reading is None:
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
