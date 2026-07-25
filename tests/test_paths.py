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


def test_trash_dir_is_under_home_and_never_deleted(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_TRASH", str(tmp_path / "trash"))
    importlib.reload(paths)
    assert paths.trash_dir() == tmp_path / "trash"
