"""The poll: fetching usage for an account with no session running.

No test here opens a socket — `poll_account` takes its fetcher as a parameter,
and every case below passes a stub. No test reads a real credentials file
either: CCAS_ACCOUNTS_ROOT points at tmp_path.
"""
import importlib
import json

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

def test_an_expired_token_is_not_fetched_with_and_not_renewed(monkeypatch, tmp_path):
    """A token lives ~8h and only Claude Code renews it. Past that the account
    goes quiet until it is next used — deliberately, because renewing is the one
    thing this must never do."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW - 60) * 1000))
    fetcher = answering(body())
    outcome = poll.poll_account("work", now=NOW, fetcher=fetcher)
    assert outcome.status == poll.EXPIRED
    assert fetcher.seen == []


def test_a_token_inside_the_leeway_counts_as_expired(monkeypatch, tmp_path):
    """A token with thirty seconds left will not survive the request."""
    env(monkeypatch, tmp_path)
    credentials(tmp_path, expires_at_ms=int((NOW + 30) * 1000))
    outcome = poll.poll_account("work", now=NOW, fetcher=answering(body()))
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
