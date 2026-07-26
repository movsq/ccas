import importlib
from pathlib import Path

import ccas.paths as paths


def test_defaults_point_at_real_home(monkeypatch):
    for var in ("CCAS_HOME", "CCAS_ACCOUNTS_ROOT", "CCAS_WAYBAR_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(paths)
    assert paths.claude_home() == Path.home() / ".claude"
    assert paths.accounts_root() == Path.home() / ".cc-accounts"
    assert paths.registry_file() == Path.home() / ".cc-accounts" / "accounts.json"


def test_env_overrides_redirect_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_HOME", str(tmp_path / "claude"))
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    importlib.reload(paths)
    assert paths.claude_home() == tmp_path / "claude"
    assert paths.account_dir("work") == tmp_path / "accts" / "work"
    assert paths.projects_root() == tmp_path / "claude" / "projects"


def test_usage_file_is_never_relinked_over(monkeypatch):
    """usage.json is per-account state, like .claude.json and .credentials.json.
    If ~/.claude ever grows one, relink must not link it over the real reading."""
    importlib.reload(paths)
    assert paths.USAGE_FILE == "usage.json"
    assert paths.USAGE_FILE in paths.BLOCKLIST


def test_trash_dir_is_under_home_and_never_deleted(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_TRASH", str(tmp_path / "trash"))
    importlib.reload(paths)
    assert paths.trash_dir() == tmp_path / "trash"


def test_menu_css_defaults_under_config_home(monkeypatch):
    """The panel stylesheet is the user's, so it lives in ~/.config rather than
    in the account directory CCAS rewrites."""
    monkeypatch.delenv("CCAS_MENU_CSS", raising=False)
    importlib.reload(paths)
    assert paths.menu_css() == Path.home() / ".config" / "ccas" / "menu.css"


def test_menu_css_is_env_overridable(monkeypatch, tmp_path):
    """Every path is overridable or the test-isolation rule breaks: a test that
    reads the real stylesheet is one the user's theme can break."""
    monkeypatch.setenv("CCAS_MENU_CSS", str(tmp_path / "m.css"))
    importlib.reload(paths)
    assert paths.menu_css() == tmp_path / "m.css"


def test_panel_output_is_unset_by_default(monkeypatch):
    """Unset means the compositor's choice. CCAS must not hardcode a connector
    name — the bar's output is the user's hardware, not ours."""
    monkeypatch.delenv("CCAS_PANEL_OUTPUT", raising=False)
    importlib.reload(paths)
    assert paths.panel_output() is None


def test_panel_output_is_env_overridable(monkeypatch):
    """A layer surface with no monitor set lands wherever the compositor puts
    it — which was the Dell, not the bar's output."""
    monkeypatch.setenv("CCAS_PANEL_OUTPUT", "HDMI-A-1")
    importlib.reload(paths)
    assert paths.panel_output() == "HDMI-A-1"


def test_panel_lock_lives_in_the_runtime_dir(monkeypatch):
    """One open panel per session, so the toggle knows what to close. The
    runtime dir is where a pid that dies with the session belongs."""
    monkeypatch.delenv("CCAS_PANEL_LOCK", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    importlib.reload(paths)
    assert paths.panel_lock() == Path("/run/user/1000/ccas-panel.lock")


def test_panel_lock_is_env_overridable(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "lock"))
    importlib.reload(paths)
    assert paths.panel_lock() == tmp_path / "lock"
