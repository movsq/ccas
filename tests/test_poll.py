"""The poll: fetching usage for an account with no session running.

No test here opens a socket — `poll_account` takes its fetcher as a parameter,
and every case below passes a stub. No test reads a real credentials file
either: CCAS_ACCOUNTS_ROOT points at tmp_path.
"""
import importlib
import json

import pytest

import ccas.paths as paths
import ccas.poll as poll
import ccas.usage as usage


def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_HOME", str(tmp_path / "claude"))
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    monkeypatch.setenv("CCAS_NO_RELOAD", "1")
    importlib.reload(paths)
    (tmp_path / "claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / "accts" / "work").mkdir(parents=True, exist_ok=True)
    return tmp_path


def credentials(tmp_path, expires_at_ms, token="sk-tok"):
    path = tmp_path / "accts" / "work" / ".credentials.json"
    path.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": token, "refreshToken": "sk-ref",
        "expiresAt": expires_at_ms, "subscriptionType": "pro"}}))
    return path


def body(five=3.0):
    return {"five_hour": {"utilization": five,
                          "resets_at": "2026-07-27T02:39:59.757453+00:00"},
            "seven_day": {"utilization": 0.0,
                          "resets_at": "2026-08-02T18:59:59.757477+00:00"}}


def answering(payload, error=""):
    """A fetcher that answers without a socket, recording the token it saw."""
    seen = []

    def fetcher(token):
        seen.append(token)
        return payload, error
    fetcher.seen = seen
    return fetcher


NOW = 1785104289.0


# ── the credential read ───────────────────────────────────────────────────────

def test_access_token_reads_the_account_and_converts_the_expiry(monkeypatch, tmp_path):
    """expiresAt is milliseconds in the file and seconds everywhere in CCAS."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=1785142943632)
    assert poll.access_token("work") == ("sk-tok", 1785142943.632)


def test_access_token_is_none_when_there_is_nothing_usable(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    assert poll.access_token("work") is None            # no file at all
    (tmp_path / "accts" / "work" / ".credentials.json").write_text("{ not json")
    assert poll.access_token("work") is None
    (tmp_path / "accts" / "work" / ".credentials.json").write_text('{"claudeAiOauth": {}}')
    assert poll.access_token("work") is None


def test_polling_never_writes_the_credentials_file(monkeypatch, tmp_path):
    """The rule the whole feature rests on: read the token, never renew it.
    A rotation risks the account's login and can invalidate the token a live
    session is holding."""
    env(monkeypatch, tmp_path)
    path = credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    before = path.stat().st_mtime_ns
    poll.poll_account("work", now=NOW, fetcher=answering(body()))
    assert path.stat().st_mtime_ns == before
    assert json.loads(path.read_text())["claudeAiOauth"]["accessToken"] == "sk-tok"


# ── freshness ─────────────────────────────────────────────────────────────────

def test_due_is_false_while_the_hook_is_keeping_up(monkeypatch, tmp_path):
    """The account you are working in is served by the statusline hook. Polling
    it would spend a request on numbers that arrived seconds ago."""
    env(monkeypatch, tmp_path)
    usage.record_reading("work", usage.from_oauth(body(), now=NOW))
    assert poll.due("work", now=NOW + 12) is False
    assert poll.due("work", now=NOW + 301) is True


def test_due_is_true_when_nothing_has_ever_been_recorded(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    assert poll.due("work", now=NOW) is True


def test_a_fresh_account_is_skipped_without_touching_the_network(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    usage.record_reading("work", usage.from_oauth(body(), now=NOW))
    fetcher = answering(body())
    outcome = poll.poll_account("work", now=NOW + 10, fetcher=fetcher)
    assert outcome.status == poll.SKIPPED
    assert fetcher.seen == []


def test_force_polls_a_fresh_account_anyway(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    usage.record_reading("work", usage.from_oauth(body(), now=NOW))
    fetcher = answering(body())
    poll.poll_account("work", now=NOW + 10, fetcher=fetcher, force=True)
    assert fetcher.seen == ["sk-tok"]


# ── the expiry horizon ────────────────────────────────────────────────────────

def renewing(tmp_path, to=None, token="sk-fresh"):
    """A renewer standing in for the claude that renews the token.

    The real one renews by *rewriting the credentials file* — CCAS never writes
    it — so the stub does that and nothing else. What the poll then fetches with
    is read back off disk, which is the behaviour under test.
    """
    calls = []

    def asker(slug):
        calls.append(slug)
        if to is not None:
            credentials(tmp_path, expires_at_ms=int(to * 1000), token=token)
        return {"loggedIn": True}
    asker.calls = calls
    return asker


def test_the_renewal_runs_the_command_that_actually_refreshes(monkeypatch, tmp_path):
    """`claude auth status` reads the credentials and reports on them without
    touching an expired token — measured 2026-07-28 against an account five
    hours past expiry, which is what shipped inert. What renews is a first-party
    API call, which the bundle makes with `refreshOAuth: true`, and `mcp list`
    is the cheapest CLI command that makes one. The command *is* the behaviour
    here, so it is pinned."""
    env(monkeypatch, tmp_path)
    seen = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen["config_dir"] = kwargs["env"]["CLAUDE_CONFIG_DIR"]
        return None

    poll.ask_claude("work", runner=runner)
    assert seen["argv"][1:] == ["mcp", "list"]
    assert seen["config_dir"] == str(paths.account_dir("work"))


def test_the_renewal_reports_a_claude_that_will_not_run(monkeypatch, tmp_path):
    """Missing binary, timeout, non-zero exit: all the same event to the caller
    — no fresh token this time — and none of them may reach the timer."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW - 60) * 1000))

    def runner(argv, **kwargs):
        raise OSError("no such binary")

    assert poll.renew("work", lambda slug: poll.ask_claude(slug, runner)) is None


def test_an_expired_token_is_handed_to_claude_and_the_fresh_one_is_used(
        monkeypatch, tmp_path):
    """The eight-hour horizon, closed. CCAS still never writes the credentials:
    it asks `claude auth status`, which renews on the way, then re-reads."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW - 60) * 1000))
    asker = renewing(tmp_path, to=NOW + 8 * 3600)
    fetcher = answering(body())
    outcome = poll.poll_account("work", now=NOW, fetcher=fetcher, renewer=asker)
    assert asker.calls == ["work"]
    assert fetcher.seen == ["sk-fresh"]
    assert outcome.status == poll.OK


def test_a_renewal_that_does_not_take_reports_expired_and_never_fetches(
        monkeypatch, tmp_path):
    """Logged out, offline, or claude simply declined: the account goes quiet
    exactly as it did before, rather than the poll spending a request on a
    token it has already read as dead."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW - 60) * 1000))
    asker = renewing(tmp_path)  # answers, renews nothing
    fetcher = answering(body())
    outcome = poll.poll_account("work", now=NOW, fetcher=fetcher, renewer=asker)
    assert asker.calls == ["work"]
    assert outcome.status == poll.EXPIRED
    assert fetcher.seen == []


def test_the_renewal_can_be_put_away_with_one_variable(monkeypatch, tmp_path):
    """CCAS_NO_TOKEN_REFRESH=1 restores the eight-hour horizon whole: claude is
    not asked at all, which is the point — the switch has to be provable from
    outside, not just quieter."""
    env(monkeypatch, tmp_path)
    monkeypatch.setenv("CCAS_NO_TOKEN_REFRESH", "1")
    credentials(tmp_path, expires_at_ms=int((NOW - 60) * 1000))
    asker = renewing(tmp_path, to=NOW + 8 * 3600)
    fetcher = answering(body())
    outcome = poll.poll_account("work", now=NOW, fetcher=fetcher, renewer=asker)
    assert asker.calls == []
    assert outcome.status == poll.EXPIRED
    assert fetcher.seen == []


def test_a_live_token_is_never_handed_to_claude(monkeypatch, tmp_path):
    """The renewal is the expired path's alone. Asking on every poll would spawn
    a claude every five minutes for an account that needs nothing."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    asker = renewing(tmp_path, to=NOW + 8 * 3600)
    poll.poll_account("work", now=NOW, fetcher=answering(body()), renewer=asker)
    assert asker.calls == []


def test_a_renewal_that_raises_is_reported_rather_than_raised(monkeypatch, tmp_path):
    """The timer runs this unattended; a broken claude may not stop the poll."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW - 60) * 1000))

    def asker(slug):
        raise OSError("no such binary")

    outcome = poll.poll_account("work", now=NOW, fetcher=answering(body()),
                                renewer=asker)
    assert outcome.status == poll.EXPIRED


def test_a_token_inside_the_leeway_is_renewed_too(monkeypatch, tmp_path):
    """A token with thirty seconds left will not survive the request, so it is
    the same case as an expired one rather than a fetch worth trying."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 30) * 1000))
    asker = renewing(tmp_path)
    outcome = poll.poll_account("work", now=NOW, fetcher=answering(body()),
                                renewer=asker)
    assert asker.calls == ["work"]
    assert outcome.status == poll.EXPIRED


def test_a_missing_credentials_file_reports_rather_than_raises(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    assert poll.poll_account("work", now=NOW,
                             fetcher=answering(body())).status == poll.NO_TOKEN


# ── recording what came back ──────────────────────────────────────────────────

def test_a_successful_poll_records_the_reading(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    outcome = poll.poll_account("work", now=NOW, fetcher=answering(body(41.0)))
    assert outcome.status == poll.OK
    recorded = usage.load("work")
    assert recorded["five_hour"]["percent"] == 41.0
    assert recorded["source"] == "oauth"


def test_a_poll_that_finds_the_same_numbers_does_not_write(monkeypatch, tmp_path):
    """Write-on-change is what lets the bar's signal ride on the write."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    poll.poll_account("work", now=NOW, fetcher=answering(body()))
    path = tmp_path / "accts" / "work" / "usage.json"
    before = path.stat().st_mtime_ns
    outcome = poll.poll_account("work", now=NOW + 400, fetcher=answering(body()))
    assert outcome.status == poll.UNCHANGED
    assert path.stat().st_mtime_ns == before


def test_a_failed_fetch_leaves_the_previous_reading_standing(monkeypatch, tmp_path):
    """No new bar state for failures: the last reading is still honest, because
    resets_at is an absolute anchor."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    poll.poll_account("work", now=NOW, fetcher=answering(body(41.0)))
    outcome = poll.poll_account("work", now=NOW + 400,
                                fetcher=answering(None, "http 401"))
    assert outcome.status == poll.FAILED
    assert "401" in outcome.detail
    assert usage.load("work")["five_hour"]["percent"] == 41.0


def test_a_body_with_no_windows_is_a_failure_not_an_erasure(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    poll.poll_account("work", now=NOW, fetcher=answering(body(41.0)))
    poll.poll_account("work", now=NOW + 400, fetcher=answering({"seven_day_opus": None}))
    assert usage.load("work")["five_hour"]["percent"] == 41.0


def test_poll_returns_one_outcome_per_slug_and_never_raises(monkeypatch, tmp_path):
    """One broken account must not stop the others: the timer runs unattended."""
    env(monkeypatch, tmp_path)
    (tmp_path / "accts" / "other").mkdir()
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    outcomes = poll.poll(["work", "other"], now=NOW, fetcher=answering(body()))
    assert [o.slug for o in outcomes] == ["work", "other"]
    assert outcomes[0].status == poll.OK
    assert outcomes[1].status == poll.NO_TOKEN


def test_polling_never_touches_claude_home(monkeypatch, tmp_path):
    """The invariant, checked around this command the way CLAUDE.md asks."""
    env(monkeypatch, tmp_path)
    home = tmp_path / "claude"
    (home / "settings.json").write_text("{}")
    before = {p.name: p.stat().st_mtime_ns for p in home.iterdir()}
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    poll.poll(["work"], now=NOW, fetcher=answering(body()))
    assert {p.name: p.stat().st_mtime_ns for p in home.iterdir()} == before
    assert [p for p in home.iterdir() if p.is_symlink()] == []


# ── the request ───────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_asks_the_endpoint_claude_code_asks():
    """URL, bearer token and timeout, exactly as measured from the 2.1.220
    bundle. No anthropic-beta header and no special user-agent — the research
    doc confirmed live that neither is needed."""
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        return FakeResponse(body())

    payload, error = poll.fetch("sk-tok", opener=opener)
    assert seen["url"] == "https://api.anthropic.com/api/oauth/usage"
    assert seen["auth"] == "Bearer sk-tok"
    assert seen["timeout"] == poll.TIMEOUT
    assert error == ""
    assert payload["five_hour"]["utilization"] == 3.0


def test_fetch_turns_an_http_error_into_a_reason():
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(poll.USAGE_URL, 401, "Unauthorized", {}, None)

    payload, error = poll.fetch("sk-tok", opener=opener)
    assert payload is None
    assert "401" in error


def test_fetch_turns_a_timeout_into_a_reason():
    def opener(request, timeout=None):
        raise TimeoutError("timed out")

    payload, error = poll.fetch("sk-tok", opener=opener)
    assert payload is None
    assert error


def test_fetch_survives_a_body_that_is_not_json():
    class Garbage(FakeResponse):
        def read(self):
            return b"<html>nope"

    payload, error = poll.fetch("sk-tok", opener=lambda r, timeout=None: Garbage(None))
    assert payload is None
    assert error


# ── is the timer alive ────────────────────────────────────────────────────────

def test_timer_state_reports_systemds_word():
    class Proc:
        stdout = "active\n"

    assert poll.timer_state(runner=lambda *a, **kw: Proc()) == "active"


def test_timer_state_is_unknown_when_systemd_cannot_be_asked():
    def runner(*a, **kw):
        raise FileNotFoundError("systemctl")

    assert poll.timer_state(runner=runner) == "unknown"


# ── the on-demand poll, and its own guard ─────────────────────────────────────

def test_the_on_demand_interval_is_its_own_constant():
    """Not INTERVAL. The timer's cadence answers "how often should this account
    be refreshed in the background"; this answers "how often may a human asking
    spend a request", and they are free to differ."""
    assert poll.PANEL_INTERVAL != poll.INTERVAL


def test_an_on_demand_poll_fetches_and_records(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    fetcher = answering(body())
    outcome = poll.poll_on_demand("work", now=NOW, fetcher=fetcher)
    assert outcome.status == poll.OK
    assert fetcher.seen == ["sk-tok"]
    assert usage.load("work")["five_hour"]["percent"] == 3.0


def test_a_second_open_inside_the_interval_spends_no_request(monkeypatch, tmp_path):
    """The rate limit. The endpoint is rate limited on Anthropic's side — the
    bundle has a whole `rateLimitedVia` fallback path — and poll.fetch has none,
    so a 429 would read as exactly the staleness this exists to remove."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    poll.poll_on_demand("work", now=NOW, fetcher=answering(body()))
    again = answering(body(five=9.0))
    outcome = poll.poll_on_demand("work", now=NOW + 30, fetcher=again)
    assert outcome.status == poll.SKIPPED
    assert again.seen == []


def test_an_open_past_the_interval_asks_again(monkeypatch, tmp_path):
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    poll.poll_on_demand("work", now=NOW, fetcher=answering(body()))
    again = answering(body(five=9.0))
    outcome = poll.poll_on_demand("work", now=NOW + poll.PANEL_INTERVAL,
                                  fetcher=again)
    assert outcome.status == poll.OK
    assert again.seen == ["sk-tok"]


def test_the_guard_is_stamped_before_the_fetch_not_after(monkeypatch, tmp_path):
    """A fetch that fails or hangs still counts as having asked.

    Stamping on success only would make a broken network an unthrottled retry —
    every panel open spending a request that cannot succeed, which is the
    precise behaviour the limit is here to prevent.
    """
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))

    def exploding(_token):
        raise RuntimeError("the network went away mid-request")

    with pytest.raises(RuntimeError):
        poll.poll_on_demand("work", now=NOW, fetcher=exploding)
    assert poll.asked_at("work") == NOW


def test_a_reading_the_hook_just_wrote_does_not_hold_the_guard_off(
        monkeypatch, tmp_path):
    """due() is not the guard, and this is why it cannot be.

    `fetched_at` means "when this reading was first seen" — the write-on-change
    rule leaves it alone while the numbers hold steady, so for the idle account
    this feature serves it is permanently older than any interval. A guard built
    on it would let every open through. The stamp is a separate fact: when we
    last *asked*, as against when the answer last changed.
    """
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    # A reading recorded a moment ago, exactly as the hook leaves it...
    usage.record_reading("work", usage.from_oauth(body(), NOW - 5))
    assert poll.due("work", NOW) is False          # the timer would skip it
    outcome = poll.poll_on_demand("work", now=NOW, fetcher=answering(body(five=9.0)))
    assert outcome.status == poll.OK               # the panel does not


def test_an_on_demand_poll_never_spawns_claude(monkeypatch, tmp_path):
    """Renewal is the timer's alone. It is a process and up to RENEW_TIMEOUT
    seconds, and by the time a panel is opened the five-minute timer has already
    done it — so an expired token here is simply no fetch."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW - 60) * 1000))

    def forbidden(*a, **kw):
        raise AssertionError("the panel must not spawn claude")

    monkeypatch.setattr(poll, "ask_claude", forbidden)
    fetcher = answering(body())
    outcome = poll.poll_on_demand("work", now=NOW, fetcher=fetcher)
    assert outcome.status == poll.EXPIRED
    assert fetcher.seen == []


def test_an_unreadable_stamp_reads_as_never_asked(monkeypatch, tmp_path):
    """Fail open: a torn or hand-edited stamp must not wedge the account shut."""
    env(monkeypatch, tmp_path)
    (tmp_path / "accts" / "work" / paths.POLL_STAMP_FILE).write_text("nonsense")
    assert poll.asked_at("work") is None


def test_an_on_demand_poll_never_touches_claude_home(monkeypatch, tmp_path):
    """The invariant, on the newest path to reach a credentials file."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 3600) * 1000))
    home = tmp_path / "claude"
    (home / "settings.json").write_text("{}")
    before = {p.name: p.stat().st_mtime_ns for p in home.iterdir()}
    poll.poll_on_demand("work", now=NOW, fetcher=answering(body()))
    assert {p.name: p.stat().st_mtime_ns for p in home.iterdir()} == before


def test_a_carried_five_hour_window_does_not_count_as_freshness(monkeypatch, tmp_path):
    """Found on the bar 2026-07-28: an account idle for a whole live session.

    vsed's 5-hour window cleared at 23:10 and the user resumed a session at
    23:14. Claude Code names each window independently — the bundle builds
    `rate_limits` from whichever of the two the last response carried headers
    for — so a statusline tick naming only `seven_day` is an ordinary shape, and
    `usage._carried` rightly refuses to erase the 5-hour window with silence.

    But the carry also moved `fetched_at`, and `due()` read that as "the hook is
    keeping this account fresh". The timer then skipped the account every five
    minutes — `skipped, recorded 3s ago` in the journal from 23:18 on — so the
    one source that knew about the new window was never asked, and the label
    stayed idle until a panel click, which is gated by `poll-stamp` instead.

    Freshness is about the 5-hour window, not about when the file was last
    written: a reading whose 5-hour window is not running is not being kept
    fresh by anybody, whatever its timestamp says.
    """
    env(monkeypatch, tmp_path)
    usage.record_reading("work", {
        "fetched_at": NOW - 3000, "source": "oauth",
        # cleared twenty minutes ago
        "five_hour": {"percent": 86.0, "resets_at": int(NOW - 1200)},
        "seven_day": {"percent": 20.0, "resets_at": int(NOW + 400000)}})

    # the live session's tick: seven_day moved, five_hour was not named
    assert usage.record("work", {"rate_limits": {
        "seven_day": {"used_percentage": 21.0,
                      "resets_at": int(NOW + 400000)}}}, now=NOW) is True

    assert usage.state(usage.load("work"), "five_hour", NOW).kind == usage.IDLE
    assert poll.due("work", now=NOW) is True
