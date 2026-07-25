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


def test_render_does_not_rewrite_the_menu_it_is_not_going_to_reload(monkeypatch):
    """hist-i actions index line i of history.tsv, and Waybar is still showing
    the menu it cached at the last reload. Rewriting the pair without reloading
    desynchronises them: the row says one session, the click resumes another."""
    make_account()
    from ccas.history import Session
    import time
    sessions = [Session(uuid=f"u{i}", cwd="/tmp", title=f"s{i}",
                        mtime=time.time() - i, path=None) for i in range(2)]
    monkeypatch.setattr(cli.history, "scan", lambda: list(sessions))
    cli.main(["render", "work"])

    xml = paths.account_dir("work") / "menu.xml"
    tsv = paths.account_dir("work") / "history.tsv"
    before = (xml.read_text(), tsv.read_text())

    sessions.append(sessions.pop(0))  # same set, new newest
    cli.main(["render", "work"])
    assert (xml.read_text(), tsv.read_text()) == before


def test_config_rebuilds_every_menu_even_when_the_session_set_is_unchanged():
    """render only writes when it is also going to reload, so after a code change
    that alters the XML nothing would refresh it. `ccs config` — what install.sh
    runs — is the unconditional rebuild."""
    make_account()
    make_account("other")
    cli.main(["render", "work"])
    for slug in ("work", "other"):
        (paths.account_dir(slug) / "menu.xml").write_text("stale", encoding="utf-8")

    assert cli.main(["config"]) == 0
    for slug in ("work", "other"):
        assert "GtkMenu" in (paths.account_dir(slug) / "menu.xml").read_text()


def test_display_persists_and_refreshes_the_bar(monkeypatch):
    make_account()
    fired = []
    monkeypatch.setattr(cli.waybar, "reload", lambda: fired.append(1))
    assert cli.main(["display", "work", "icon only"]) == 0
    assert registry.find(registry.load(), "work")["display"] == "icon only"
    assert fired == [1], "a per-module signal repaints the label but not the menu"


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


def _menu_xml():
    return (paths.account_dir("work") / "menu.xml").read_text(encoding="utf-8")


def test_changing_a_setting_moves_the_radio_dot_in_the_menu(monkeypatch):
    """The ●/○ marks are baked into menu.xml, and Waybar caches that file — so
    a setting change has to rewrite it and force a full reload, or the dot stays
    on whatever was selected when the bar last started."""
    make_account()
    reloads = []
    monkeypatch.setattr(cli.waybar, "reload", lambda: reloads.append(1))

    cli.main(["color", "work", "5"])
    assert f"● {paths.PALETTE[5][0]}" in _menu_xml()
    assert f"○ {paths.PALETTE[0][0]}" in _menu_xml()
    assert reloads, "a stale cached menu is the whole bug"

    cli.main(["display", "work", "index"])
    assert "● index" in _menu_xml()
    assert "○ nickname" in _menu_xml()

    cli.main(["hide", "work", "toggle"])
    assert f"{cli.menu.MARK_ON} Hide icon" in _menu_xml()


def test_renaming_updates_the_menu_title_row(monkeypatch):
    make_account()
    monkeypatch.setattr(cli.waybar, "reload", lambda: None)
    cli.main(["nick", "work", "personal"])
    assert ">personal<" in _menu_xml()


def test_rename_cancelled_keeps_the_existing_nickname(monkeypatch):
    """Esc used to be indistinguishable from clearing, so backing out of the
    rename dialog wiped the nickname."""
    make_account()
    monkeypatch.setattr(cli.pickers, "prompt_or_clear",
                        lambda *a, **k: cli.pickers.CANCEL)
    assert cli.main(["manage", "rename", "work"]) == 0
    assert registry.find(registry.load(), "work")["nickname"] == "work"


def test_rename_clear_row_empties_the_nickname(monkeypatch):
    make_account()
    monkeypatch.setattr(cli.pickers, "prompt_or_clear", lambda *a, **k: None)
    assert cli.main(["manage", "rename", "work"]) == 0
    assert registry.find(registry.load(), "work")["nickname"] is None


def test_rename_typed_text_is_stored(monkeypatch):
    make_account()
    monkeypatch.setattr(cli.pickers, "prompt_or_clear", lambda *a, **k: "typed")
    cli.main(["manage", "rename", "work"])
    assert registry.find(registry.load(), "work")["nickname"] == "typed"


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
