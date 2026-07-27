"""Fetching an account's usage limits without a session.

The statusline hook is the only source while a session runs, and there is no
session for the account you are *not* using — so its numbers freeze the moment
the last one closes. This asks the endpoint Claude Code itself asks,
`GET /api/oauth/usage`, with the token already sitting in the account's
credentials file. `docs/superpowers/specs/2026-07-27-usage-poll-design.md` has
why that route, and `docs/usage-limits-research.md` has the three that lost.

Two rules hold it up:

- **Read the credentials, never write them.** No refresh, ever: rotating a token
  risks that account's login, and can invalidate the one a live session holds.
  An expired token means no fetch — never a renewal. The horizon that buys is
  about eight hours, and it is the right trade: an account idle that long has
  rolled its five-hour window over, which `usage.state()` reports from the
  timestamp alone with no fetch at all.
- **Never fetch what the hook already knows.** `due()` skips an account recorded
  more recently than the interval, so the account being used stays the hook's
  and the timer serves the idle one.

No signalling here. The caller owns the bar, the same way `cmd_statusline` does.
"""
import json
import time
from collections import namedtuple

from . import paths, usage

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


def poll_account(slug: str, now=None, fetcher=None, force=False) -> Outcome:
    """One account: decide, fetch, record. Reports; never raises."""
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
        return Outcome(slug, EXPIRED, f"token {when} — no fetch, and CCAS never renews")

    payload, error = fetcher(token)
    if payload is None:
        return Outcome(slug, FAILED, f"{error} — reading unchanged")
    reading = usage.from_oauth(payload, now)
    if reading is None:
        return Outcome(slug, FAILED, "no windows in the response — reading unchanged")
    if usage.record_reading(slug, reading):
        return Outcome(slug, OK, usage.column(reading, "five_hour", now))
    return Outcome(slug, UNCHANGED, "same numbers")


def poll(slugs, now=None, fetcher=None, force=False) -> list:
    """Every account, one outcome each. The timer runs this unattended, so one
    broken account may not stop the rest."""
    return [poll_account(slug, now, fetcher, force) for slug in slugs]
