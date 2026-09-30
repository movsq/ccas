import importlib
import json
import os
import pytest

import ccas.paths as paths
import ccas.accounts as accounts


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    home = tmp_path / "claude"
    (home / "plugins").mkdir(parents=True)
    (home / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
    (home / ".credentials.json").write_text('{"secret":1}', encoding="utf-8")
    cfg = tmp_path / "claude.json"
    cfg.write_text(json.dumps({
        "oauthAccount": {"emailAddress": "old@example.com"},
        "projects": {"/home/x": {"hasTrustDialogAccepted": True}},
    }), encoding="utf-8")
    monkeypatch.setenv("CCAS_HOME", str(home))
    monkeypatch.setenv("CCAS_CLAUDE_JSON", str(cfg))
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    monkeypatch.setenv("CCAS_TRASH", str(tmp_path / "trash"))
    importlib.reload(paths)
    importlib.reload(accounts)
    return tmp_path


def test_relink_symlinks_shared_entries():
    accounts.create("work")
    accounts.relink("work")
    d = paths.account_dir("work")
    assert (d / "settings.json").is_symlink()
    assert (d / "plugins").is_symlink()
    assert (d / "settings.json").resolve() == (paths.claude_home() / "settings.json").resolve()


def test_relink_never_links_blocklisted_files():
    accounts.create("work")
    accounts.relink("work")
    assert not (paths.account_dir("work") / ".credentials.json").is_symlink()


def test_relink_unlinks_a_blocklisted_name_it_linked_before():
    """A name joins the blocklist after accounts already carry a link for it —
    which is how `.oauth_refresh.lock` was found, on 2026-09-30. Skipping the
    name is not enough then: the old link stays, and the account keeps sharing
    what the entry was blocklisted for. The link goes; what it pointed at in
    ~/.claude is not touched."""
    lock = paths.claude_home() / ".oauth_refresh.lock"
    lock.mkdir()
    accounts.create("work")
    link = paths.account_dir("work") / ".oauth_refresh.lock"
    link.symlink_to(lock)
    accounts.relink("work")
    assert not link.is_symlink() and not link.exists()
    assert lock.is_dir()


def test_relink_leaves_a_real_blocklisted_file_alone():
    accounts.create("work")
    own = paths.account_dir("work") / ".credentials.json"
    own.write_text('{"mine":1}', encoding="utf-8")
    accounts.relink("work")
    assert own.read_text(encoding="utf-8") == '{"mine":1}'


def test_relink_picks_up_entries_added_later():
    accounts.create("work")
    accounts.relink("work")
    (paths.claude_home() / "newthing.json").write_text("{}", encoding="utf-8")
    accounts.relink("work")
    assert (paths.account_dir("work") / "newthing.json").is_symlink()


def test_relink_prunes_dangling_symlinks():
    accounts.create("work")
    accounts.relink("work")
    (paths.claude_home() / "settings.json").unlink()
    accounts.relink("work")
    assert not (paths.account_dir("work") / "settings.json").exists()
    assert "settings.json" not in os.listdir(paths.account_dir("work"))


def test_relink_never_writes_into_claude_home():
    accounts.create("work")
    before = sorted(os.listdir(paths.claude_home()))
    accounts.relink("work")
    assert sorted(os.listdir(paths.claude_home())) == before


def test_relink_never_touches_mtimes_in_claude_home():
    """The central guarantee: reading ~/.claude must leave it byte-identical."""
    accounts.create("work")
    home = paths.claude_home()
    before = {p.name: p.lstat().st_mtime_ns for p in home.iterdir()}
    accounts.relink("work")
    accounts.relink("work")
    after = {p.name: p.lstat().st_mtime_ns for p in home.iterdir()}
    assert after == before


def test_relink_is_idempotent():
    accounts.create("work")
    accounts.relink("work")
    first = sorted(os.listdir(paths.account_dir("work")))
    accounts.relink("work")
    assert sorted(os.listdir(paths.account_dir("work"))) == first


def test_relink_leaves_real_per_account_files_alone():
    accounts.create("work")
    real = paths.account_dir("work") / ".credentials.json"
    real.write_text('{"mine":true}', encoding="utf-8")
    accounts.relink("work")
    assert not real.is_symlink()
    assert json.loads(real.read_text()) == {"mine": True}


def test_seed_config_strips_oauth_but_keeps_trust():
    accounts.create("work")
    accounts.seed_config("work")
    data = json.loads((paths.account_dir("work") / ".claude.json").read_text())
    assert "oauthAccount" not in data
    assert data["projects"]["/home/x"]["hasTrustDialogAccepted"] is True


def test_seed_config_tolerates_absent_source(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_CLAUDE_JSON", str(tmp_path / "missing.json"))
    importlib.reload(paths)
    importlib.reload(accounts)
    accounts.create("work")
    accounts.seed_config("work")
    assert json.loads((paths.account_dir("work") / ".claude.json").read_text()) == {}


def test_to_trash_moves_and_never_deletes():
    d = accounts.create("work")
    (d / "marker").write_text("keep me", encoding="utf-8")
    moved = accounts.to_trash(d)
    assert not d.exists()
    assert moved.exists()
    assert (moved / "marker").read_text() == "keep me"
    assert str(paths.trash_dir()) in str(moved)


def test_to_trash_does_not_collide_on_repeat():
    first = accounts.to_trash(accounts.create("work"))
    second = accounts.to_trash(accounts.create("work"))
    assert first != second
    assert first.exists() and second.exists()


def test_env_for_sets_config_dir_and_recursion_guard():
    env = accounts.env_for("work")
    assert env["CLAUDE_CONFIG_DIR"] == str(paths.account_dir("work"))
    assert env["CCAS_INNER"] == "1"
    assert "PATH" in env


def test_env_for_scrubs_the_parent_session_markers(monkeypatch):
    """A session CCAS starts is a new top-level one, never a child of whatever
    launched it.

    Found on the real system, twice. Claude Code sets CLAUDE_CODE_CHILD_SESSION
    in the shells it spawns, and any launcher started from one inherits it and
    hands it to every session it opens — where it silently disables transcript
    saving. First it was Waybar, restarted from inside an agent shell; then the
    GTK panel, left open by an agent, whose verbs spawn kitty with that same
    environment. Scrubbing here makes the whole class of it impossible, whatever
    CCAS itself was started from.
    """
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    env = accounts.env_for("work")
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert "CLAUDECODE" not in env
    assert "CLAUDE_CODE_ENTRYPOINT" not in env


def test_auth_status_never_invokes_claude_by_bare_name(monkeypatch):
    seen = {}

    class R:
        stdout = '{"loggedIn": true, "email": "a@b.c"}'

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return R()

    monkeypatch.setattr(accounts.subprocess, "run", fake_run)
    assert accounts.auth_status("work")["loggedIn"] is True
    assert seen["cmd"][0].startswith("/"), "must use the absolute binary path"
    assert seen["cmd"][0] != "claude"


def test_auth_status_reports_logged_out_on_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("no such binary")

    monkeypatch.setattr(accounts.subprocess, "run", boom)
    assert accounts.auth_status("work") == {"loggedIn": False}
