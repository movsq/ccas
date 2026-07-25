import importlib
import json
import pytest

import ccas.paths as paths
import ccas.registry as registry
import ccas.cli as cli


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    home = tmp_path / "claude"
    (home / "projects").mkdir(parents=True)
    (home / "settings.json").write_text("{}", encoding="utf-8")
    cfg = tmp_path / "config.jsonc"
    cfg.write_text('{\n    "modules-right": ["clock"]\n}\n', encoding="utf-8")
    monkeypatch.setenv("CCAS_HOME", str(home))
    monkeypatch.setenv("CCAS_CLAUDE_JSON", str(tmp_path / "claude.json"))
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    monkeypatch.setenv("CCAS_TRASH", str(tmp_path / "trash"))
    monkeypatch.setenv("CCAS_WAYBAR_CONFIG", str(cfg))
    monkeypatch.setenv("CCAS_BASHRC", str(tmp_path / "bashrc"))
    for module in (paths, registry, cli):
        importlib.reload(module)
    monkeypatch.setattr(cli.waybar, "reload", lambda: None)
    monkeypatch.setattr(cli.waybar, "signal", lambda n: None)
    return tmp_path


def make_account(slug="work"):
    reg = registry.load()
    registry.add(reg, slug, f"{slug}@x.com", slug)
    registry.save(reg)
    from ccas import accounts
    accounts.create(slug)
    return reg


def test_unknown_command_is_an_error(capsys):
    assert cli.main(["nonsense-command"]) != 0


def test_render_prints_a_pango_label(capsys):
    make_account()
    assert cli.main(["render", "work"]) == 0
    assert "✻" in capsys.readouterr().out


def test_render_regenerates_the_menu_file():
    make_account()
    cli.main(["render", "work"])
    assert (paths.account_dir("work") / "menu.xml").exists()


def test_render_for_unknown_account_fails_quietly(capsys):
    assert cli.main(["render", "ghost"]) == 1
    assert capsys.readouterr().out == ""


def test_render_reloads_waybar_only_when_the_session_set_changed(monkeypatch):
    """Waybar caches menu-file, so a changed menu needs a full reload — but an
    unconditional one would flicker the bar every 30 seconds."""
    make_account()
    reloads = []
    monkeypatch.setattr(cli.waybar, "reload", lambda: reloads.append(1))

    from ccas.history import Session
    import time
    sessions = [Session(uuid="u1", cwd="/tmp", title="one", mtime=time.time(), path=None)]
    monkeypatch.setattr(cli.history, "scan", lambda: list(sessions))

    cli.main(["render", "work"])
    assert reloads == [1], "first render writes a new menu, so it must reload"

    cli.main(["render", "work"])
    assert reloads == [1], "unchanged session set must not reload"

    sessions.insert(0, Session(uuid="u2", cwd="/tmp", title="two",
                               mtime=time.time(), path=None))
    cli.main(["render", "work"])
    assert reloads == [1, 1], "a new session must reload"


def test_display_persists_and_signals(monkeypatch):
    make_account()
    fired = []
    monkeypatch.setattr(cli.waybar, "signal", lambda n: fired.append(n))
    assert cli.main(["display", "work", "icon only"]) == 0
    assert registry.find(registry.load(), "work")["display"] == "icon only"
    assert fired == [1]


def test_display_rejects_an_invalid_mode():
    make_account()
    assert cli.main(["display", "work", "sideways"]) == 1


def test_hide_toggle_flips_the_flag():
    make_account()
    cli.main(["hide", "work", "toggle"])
    assert registry.find(registry.load(), "work")["hide_icon"] is True
    cli.main(["hide", "work", "toggle"])
    assert registry.find(registry.load(), "work")["hide_icon"] is False


def test_invisible_combination_notifies_once(monkeypatch):
    make_account()
    sent = []
    monkeypatch.setattr(cli, "notify", lambda text: sent.append(text))
    cli.main(["display", "work", "icon only"])
    cli.main(["hide", "work", "on"])
    assert len(sent) == 1 and "invisible" in sent[0]
    cli.main(["hide", "work", "off"])
    cli.main(["hide", "work", "on"])
    assert len(sent) == 2


def test_color_persists():
    make_account()
    assert cli.main(["color", "work", "5"]) == 0
    assert registry.find(registry.load(), "work")["color"] == 5


def test_nick_sets_and_clears():
    make_account()
    cli.main(["nick", "work", "job"])
    assert registry.find(registry.load(), "work")["nickname"] == "job"
    cli.main(["nick", "work"])
    assert registry.find(registry.load(), "work")["nickname"] is None


def test_default_shows_and_sets(capsys):
    make_account("a")
    make_account("b")
    cli.main(["default"])
    assert "a" in capsys.readouterr().out
    cli.main(["default", "b"])
    assert registry.load()["default"] == "b"


def test_rm_moves_to_trash_and_updates_registry():
    make_account()
    assert cli.main(["rm", "work"]) == 0
    assert registry.find(registry.load(), "work") is None
    assert not paths.account_dir("work").exists()
    assert any(paths.trash_dir().iterdir())


def test_config_writes_the_managed_block():
    make_account()
    assert cli.main(["config"]) == 0
    assert "custom/cc-work" in paths.waybar_config().read_text()


def test_relink_all_accounts_by_default():
    make_account("a")
    make_account("b")
    assert cli.main(["relink"]) == 0
    assert (paths.account_dir("a") / "settings.json").is_symlink()
    assert (paths.account_dir("b") / "settings.json").is_symlink()


def test_slug_passthrough_runs_under_the_account_env(monkeypatch):
    make_account()
    seen = {}

    def fake_run(cmd, env=None, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = env
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.main(["work", "echo", "hi"]) == 0
    assert seen["cmd"] == ["echo", "hi"]
    assert seen["env"]["CLAUDE_CONFIG_DIR"].endswith("/work")
    assert seen["env"]["CCAS_INNER"] == "1"


def test_tty_with_arguments_uses_the_default_account(monkeypatch):
    make_account("a")
    seen = {}

    def fake_run(cmd, env=None, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = env
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.main(["tty", "-p", "hello"]) == 0
    assert seen["cmd"][1:] == ["-p", "hello"]
    assert seen["env"]["CLAUDE_CONFIG_DIR"].endswith("/a")


def test_tty_with_arguments_never_uses_a_bare_claude(monkeypatch):
    make_account("a")
    seen = {}

    def fake_run(cmd, env=None, **kwargs):
        seen["cmd"] = cmd
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli.main(["tty", "-p", "hello"])
    assert seen["cmd"][0].startswith("/"), "a bare name would re-enter the shell function"


def test_tty_with_arguments_and_no_accounts_fails(capsys):
    assert cli.main(["tty", "-p", "hello"]) == 1


def test_gui_flag_forces_gui_mode_even_on_a_tty(monkeypatch):
    """Waybar inherits the compositor's stdin, which on a TTY session is a real
    terminal. Without --gui a click took the terminal path and blocked forever
    on input() against /dev/tty1."""
    make_account()
    monkeypatch.setattr(cli.pickers, "is_gui", lambda: False)
    seen = {}
    monkeypatch.setattr(cli, "cmd_manage",
                        lambda action, slug, gui: seen.setdefault("gui", gui) or 0)
    cli.main(["--gui", "manage", "add"])
    assert seen["gui"] is True


def test_without_the_flag_gui_is_still_sniffed(monkeypatch):
    make_account()
    monkeypatch.setattr(cli.pickers, "is_gui", lambda: False)
    seen = {}
    monkeypatch.setattr(cli, "cmd_manage",
                        lambda action, slug, gui: seen.setdefault("gui", gui) or 0)
    cli.main(["manage", "add"])
    assert seen["gui"] is False


def test_gui_flag_does_not_swallow_the_command():
    make_account()
    assert cli.main(["--gui", "render", "work"]) == 0
