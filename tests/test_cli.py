import importlib
import os
import time
import io
import json
from datetime import datetime, timezone
import types
import pytest

import ccas.format as fmt
import ccas.panel as panel
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
    monkeypatch.setenv("CCAS_WAYBAR_STYLE", str(tmp_path / "style.css"))
    # cmd_panel reads and writes it, and the real one names a live pid.
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "panel.lock"))
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


def test_render_for_unknown_account_fails_quietly(capsys):
    assert cli.main(["render", "ghost"]) == 1
    assert capsys.readouterr().out == ""


def test_render_prints_the_label_and_never_reloads(monkeypatch):
    """The bug this whole change exists for: the first prompt of a new session
    created a jsonl, the next 30 s tick saw a uuid it had not cached, and the
    bar rebuilt itself while the user was typing."""
    make_account()
    reloads = []
    monkeypatch.setattr(cli.waybar, "reload", lambda: reloads.append(1))
    monkeypatch.setattr(cli.history, "scan", lambda: [])
    assert cli.main(["render", "work"]) == 0

    from ccas.history import Session
    fresh = [Session(uuid="brand-new", cwd=str(paths.projects_root()), title="T",
                     mtime=9e9, path=None)]
    monkeypatch.setattr(cli.history, "scan", lambda: fresh)
    assert cli.main(["render", "work"]) == 0
    assert reloads == [], "a new session is not a reason to rebuild the bar"


def test_format_persists_and_refreshes_the_bar(monkeypatch):
    """What `ccs display` used to pin: a label change repaints its module."""
    make_account()
    fired = []
    monkeypatch.setattr(cli.waybar, "signal", lambda n: fired.append(n))
    assert cli.main(["format", "work", "%name"]) == 0
    assert registry.find(registry.load(), "work")["format"] == "%name"
    assert fired == [1], "the label changed, so the module repaints"


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
    cli.main(["format", "work", "   "])
    cli.main(["hide", "work", "on"])
    assert len(sent) == 1 and "invisible" in sent[0]
    cli.main(["hide", "work", "off"])
    cli.main(["hide", "work", "on"])
    assert len(sent) == 2


def test_color_persists():
    make_account()
    assert cli.main(["color", "work", "5"]) == 0
    assert registry.find(registry.load(), "work")["color"] == paths.PALETTE[5][1]


def test_a_setting_change_signals_the_label_and_does_not_reload(monkeypatch):
    """The reload existed because the ●/○ marks were baked into a menu file
    Waybar caches. With no cached file, a label repaint is the whole job — and
    SIGRTMIN+n does that without rebuilding the bar."""
    make_account()
    reloads, signals = [], []
    monkeypatch.setattr(cli.waybar, "reload", lambda: reloads.append(1))
    monkeypatch.setattr(cli.waybar, "signal", lambda n: signals.append(n))

    assert cli.main(["color", "work", "5"]) == 0
    assert cli.main(["format", "work", "%index"]) == 0
    assert cli.main(["nick", "work", "personal"]) == 0
    assert cli.main(["hide", "work", "toggle"]) == 0

    assert signals == [1, 1, 1, 1]
    assert reloads == [], "nothing here changes the set of modules"


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


def _stub_claude(monkeypatch):
    """Capture the env a claude invocation would get, without running it."""
    runs = []

    class Done:
        returncode = 0

    def fake_run(argv, env=None, check=False, **kw):
        runs.append((argv, (env or {}).get("CLAUDE_CONFIG_DIR", "")))
        return Done()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return runs


def test_headless_prompts_once_then_remembers_the_choice(monkeypatch):
    """`claude -p "hi"` used to run under the default account silently. It now
    asks on the first interactive run — even with a single account, so the user
    knows which one is answering — and never asks again."""
    make_account("work")
    make_account("other")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    asked = []

    def fake_choose(prompt, options, note=None):
        asked.append(options)
        return "other"

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)

    assert cli.main(["tty", "-p", "say hi"]) == 0
    assert len(asked) == 1
    assert runs[0][1].endswith("/other")
    assert registry.headless_slug(registry.load()) == "other"

    assert cli.main(["tty", "-p", "again"]) == 0
    assert len(asked) == 1, "the choice is remembered, not re-asked"
    assert runs[1][1].endswith("/other")


def test_headless_prompts_even_for_a_single_account(monkeypatch):
    make_account("work")
    _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    asked = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda p, o, note=None: asked.append(o) or "work")
    cli.main(["tty", "-p", "hi"])
    assert asked, "the point is knowing which account answered"


def test_declining_the_headless_prompt_runs_nothing(monkeypatch):
    make_account("work")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, note=None: None)
    assert cli.main(["tty", "-p", "hi"]) == 1
    assert runs == [], "Esc must not fall back to some arbitrary account"


def test_piped_stdin_never_prompts(monkeypatch):
    """`echo hi | claude -p` and anything in a script or cron: prompting there
    hangs forever against a stdin that is not a terminal."""
    make_account("work")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda *a, **k: pytest.fail("must not prompt"))
    assert cli.main(["tty", "-p", "hi"]) == 0
    assert runs[0][1].endswith("/work"), "falls back to the default account"


def test_maintenance_commands_skip_the_prompt(monkeypatch):
    """--version and friends do not start a session, so which account is
    irrelevant and being asked would just be noise."""
    make_account("work")
    make_account("other")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda *a, **k: pytest.fail("must not prompt"))
    for args in (["--version"], ["doctor"], ["update"], ["--help"]):
        assert cli.main(["tty", *args]) == 0
    assert len(runs) == 4
    assert registry.headless_slug(registry.load()) is None


def test_ccas_account_env_var_wins_and_is_not_remembered(monkeypatch):
    make_account("work")
    make_account("other")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda *a, **k: pytest.fail("must not prompt"))
    monkeypatch.setenv("CCAS_ACCOUNT", "other")
    assert cli.main(["tty", "-p", "hi"]) == 0
    assert runs[0][1].endswith("/other")
    assert registry.headless_slug(registry.load()) is None, "a one-off override"


def test_unknown_ccas_account_is_an_error_not_a_silent_fallback(monkeypatch):
    make_account("work")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setenv("CCAS_ACCOUNT", "ghost")
    assert cli.main(["tty", "-p", "hi"]) == 1
    assert runs == []


def test_headless_command_reports_and_toggles(monkeypatch, capsys):
    make_account("work")
    make_account("other")
    assert cli.main(["headless"]) == 0
    assert capsys.readouterr().out.strip() == ""

    assert cli.main(["headless", "other"]) == 0
    assert registry.headless_slug(registry.load()) == "other"
    cli.main(["headless"])
    assert capsys.readouterr().out.strip() == "other"

    assert cli.main(["headless", "other"]) == 0
    assert registry.headless_slug(registry.load()) is None, "clicking it again clears"
    assert cli.main(["headless", "ghost"]) == 1


def test_the_headless_toggle_touches_the_bar_not_at_all(monkeypatch):
    """It is not in the label — it decides which account answers `ccs -p`."""
    make_account("work")
    make_account("other")
    reloads, signals = [], []
    monkeypatch.setattr(cli.waybar, "reload", lambda: reloads.append(1))
    monkeypatch.setattr(cli.waybar, "signal", lambda n: signals.append(n))
    assert cli.main(["headless", "work"]) == 0
    assert registry.headless_slug(registry.load()) == "work"
    assert (reloads, signals) == ([], [])


def test_mode_menu_offers_the_headless_runner_and_shows_its_state(monkeypatch):
    """The third way in. The Waybar row and `ccs headless` both existed, but the
    terminal path — pick an account, then pick what to do with it — had no way to
    say "this is the one that answers claude -p"."""
    make_account("work")
    monkeypatch.setattr(cli.launch, "run", lambda *a, **k: pytest.fail("no session"))
    seen = []

    def fake_choose(prompt, options, note=None):
        seen.append(options)
        return next(o for o in options if "headless runner" in o.lower())

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)

    assert cli.cmd_mode_menu("work", False) == 0
    assert f"{cli.label.MARK_OFF} Select as headless runner" in seen[0]
    assert registry.headless_slug(registry.load()) == "work"

    assert cli.cmd_mode_menu("work", False) == 0
    # The mark carries the state, but on its own it does not read as something
    # you can pick — in a flat fzf list of verbs it looks like a status line.
    # The wording has to say what picking it does, in both directions.
    assert f"{cli.label.MARK_ON} Headless runner — pick to clear" in seen[1]
    assert registry.headless_slug(registry.load()) is None, "picking it again clears"


def test_mode_menu_still_launches_the_other_four_rows(monkeypatch):
    """The headless row is appended, not spliced in — 'All projects…' is the
    catch-all in this dispatch and must not swallow the new row."""
    make_account("work")
    calls = []
    monkeypatch.setattr(cli.launch, "run",
                        lambda *a, **k: calls.append(a) or 0)
    for i in range(4):
        monkeypatch.setattr(cli.pickers, "choose", lambda p, o, i=i, note=None: o[i])
        assert cli.cmd_mode_menu("work", False) == 0
    assert [c[1] for c in calls] == ["new", "last", "search", "new"]
    assert len(calls) == 4


def test_the_terminal_keeps_its_cwd_scoped_verbs(monkeypatch):
    """There the cwd is the user's own, and scoping to it is the whole reason
    that screen is shaped the way it is.

    The bar's half of this test is gone with the rule it pinned: the two doors
    used to differ in their launch verbs, because `os.getcwd()` from a bar click
    is Waybar's directory — `~`, wherever the compositor started it — and
    directory-free labels were the only honest ones. The panel's project pane
    supplies a real directory, so it says "New session in ~/4s" and means it.
    """
    make_account("work")
    monkeypatch.setattr(cli.launch, "run", lambda *a, **k: pytest.fail("no session"))
    seen = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda p, o, note=None: seen.append(o))

    cli.cmd_mode_menu("work", gui=False)
    assert seen[0][0].startswith("New here")
    assert "All projects…" in seen[0]


def test_mode_menu_offers_colour_and_hide(monkeypatch):
    """The settings that only the Waybar menu could reach. Once the bar's click
    opens this picker instead of a GtkMenu, this is the only way in."""
    make_account("work")
    monkeypatch.setattr(cli.launch, "run", lambda *a, **k: pytest.fail("no session"))
    seen = []

    def fake_choose(prompt, options, note=None):
        seen.append((prompt, options))
        return None

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)
    cli.cmd_mode_menu("work", False)
    prompt, options = seen[0]
    assert "Display as…" not in options, "the modes are retired"
    assert "Color…" in options
    assert f"{cli.label.MARK_OFF} Hide icon" in options
    # The identity that used to head the menu. Once the bar's click opens this,
    # a picker prompted "mode" does not say which account it belongs to.
    assert prompt == "work"


def test_colour_submenu_marks_the_current_colour_and_sets_the_new_one(monkeypatch):
    make_account("work")
    # A palette colour on purpose: a new account gets a random one, which is
    # not in the list this submenu marks.
    cli.main(["color", "work", paths.PALETTE[0][1]])
    target = paths.PALETTE[5][0]
    seen = []

    def fake_choose(prompt, options, note=None):
        seen.append(options)
        return "Color…" if len(seen) == 1 else \
            next(o for o in options if o.endswith(target))

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)
    assert cli.cmd_mode_menu("work", False) == 0
    assert f"{cli.label.MARK_ON} {paths.PALETTE[0][0]}" in seen[1], seen[1]
    assert registry.find(registry.load(), "work")["color"] == paths.PALETTE[5][1]


def test_hide_icon_toggles_in_place_and_shows_its_state(monkeypatch):
    """A mark row like headless, not a submenu — there are only two states."""
    make_account("work")
    seen = []

    def fake_choose(prompt, options, note=None):
        seen.append(options)
        return next(o for o in options if "Hide icon" in o)

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)
    assert cli.cmd_mode_menu("work", False) == 0
    assert registry.find(registry.load(), "work")["hide_icon"] is True
    assert cli.cmd_mode_menu("work", False) == 0
    assert f"{cli.label.MARK_ON} Hide icon" in seen[1]
    assert registry.find(registry.load(), "work")["hide_icon"] is False


def test_a_cancelled_submenu_changes_nothing(monkeypatch):
    """Esc out of the second prompt must not fall through to a launch."""
    make_account("work")
    monkeypatch.setattr(cli.launch, "run", lambda *a, **k: pytest.fail("no session"))
    calls = []

    def fake_choose(prompt, options, note=None):
        calls.append(options)
        return "Color…" if len(calls) == 1 else None

    before = registry.find(registry.load(), "work")["color"]
    monkeypatch.setattr(cli.pickers, "choose", fake_choose)
    assert cli.cmd_mode_menu("work", False) == 0
    assert registry.find(registry.load(), "work")["color"] == before


def test_a_leading_flag_is_passed_through_to_claude(monkeypatch):
    """`ccs -p "hi"` replaces the `claude()` shell function. Owning the user's
    `claude` bought nothing that this does not, and cost the recursion hazard."""
    make_account("a")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["-p", "hi"]) == 0
    argv, config_dir = runs[0]
    assert argv[1:] == ["-p", "hi"]
    assert argv[0].startswith("/"), "never a bare name; see paths.claude_bin"
    assert config_dir.endswith("/a")


def test_a_double_dash_passes_claude_subcommands_through(monkeypatch):
    """`mcp`, `doctor`, `update` are claude subcommands but read as ccs ones, so
    the non-flag case needs the explicit escape."""
    make_account("a")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["--", "mcp", "list"]) == 0
    assert runs[0][0][1:] == ["mcp", "list"]


def test_the_gui_flag_still_comes_off_before_the_passthrough(monkeypatch):
    make_account("a")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["--gui", "-p", "hi"]) == 0
    assert runs[0][0][1:] == ["-p", "hi"], "--gui is ours, not claude's"


def test_passthrough_with_no_accounts_fails(capsys):
    assert cli.main(["-p", "hi"]) == 1


def test_passthrough_does_not_open_the_panel(monkeypatch):
    """The other half of test_waybar's --gui test: `ccs -p` is typed at a real
    terminal, so it prompts there. --gui is Waybar's alone, and it is the only
    thing that reaches the GTK panel."""
    make_account("a")
    make_account("b")
    _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.pickers, "is_gui", lambda: False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.panel_ui, "show", _panel_never_called)
    seen = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda prompt, options, note=None: seen.append(prompt) or options[0])
    assert cli.main(["-p", "hi"]) == 0
    assert seen == ["account for headless runs"]


def _stub_login(monkeypatch, email, ok=True):
    """Stand in for `claude auth login`, which writes into the account dir."""
    written = {}

    class Done:
        returncode = 0

    def fake_run(argv, env=None, **kw):
        directory = paths.Path(env["CLAUDE_CONFIG_DIR"]) if env else None
        if directory is not None:
            (directory / ".credentials.json").write_text("token", encoding="utf-8")
            written["dir"] = directory
        return Done()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.accounts, "auth_status",
                        lambda slug: {"loggedIn": ok, "email": email})
    monkeypatch.setattr(cli.pickers, "prompt", lambda *a, **k: "")
    return written


def test_add_names_the_directory_after_the_email(monkeypatch):
    """The nickname is optional, so it cannot be what the directory is called.
    It only got the job because the directory has to exist *before* login and
    the email is not known until after — so rename once the email arrives."""
    _stub_login(monkeypatch, "someone1337@gmail.com")
    assert cli.main(["add"]) == 0
    reg = registry.load()
    assert [a["slug"] for a in reg["accounts"]] == ["someone1337"]
    assert paths.account_dir("someone1337").is_dir()
    assert not paths.account_dir("account").exists(), "the temp name must not linger"


def test_add_moves_the_directory_rather_than_recreating_it(monkeypatch):
    """Login has already written credentials in there. A fresh mkdir would
    strand them under the old name and the account would not be logged in."""
    _stub_login(monkeypatch, "someone@x.com")
    assert cli.main(["add"]) == 0
    assert (paths.account_dir("someone") / ".credentials.json").read_text() == "token"


def test_add_keeps_the_nickname_the_user_typed(monkeypatch):
    _stub_login(monkeypatch, "someone@x.com")
    monkeypatch.setattr(cli.pickers, "prompt", lambda *a, **k: "day job")
    assert cli.main(["add"]) == 0
    account = registry.load()["accounts"][0]
    assert account["nickname"] == "day job"
    assert account["slug"] == "someone", "the slug is the email's, not the nickname's"


def test_add_does_not_collide_with_an_existing_account(monkeypatch):
    """Two logins to addresses that slugify the same must not share a directory
    — that would put two accounts in one CLAUDE_CONFIG_DIR."""
    _stub_login(monkeypatch, "someone@x.com")
    assert cli.main(["add"]) == 0
    _stub_login(monkeypatch, "someone@y.com")
    assert cli.main(["add"]) == 0
    slugs = [a["slug"] for a in registry.load()["accounts"]]
    assert slugs == ["someone", "someone-2"]
    assert (paths.account_dir("someone") / ".credentials.json").exists()
    assert (paths.account_dir("someone-2") / ".credentials.json").exists()


def test_add_falls_back_when_the_email_is_missing(monkeypatch):
    """`auth status` reporting loggedIn with no address should not name a
    directory after an empty string."""
    _stub_login(monkeypatch, "")
    assert cli.main(["add"]) == 0
    slug = registry.load()["accounts"][0]["slug"]
    assert slug and paths.account_dir(slug).is_dir()


def test_add_trashes_the_directory_when_login_fails(monkeypatch):
    _stub_login(monkeypatch, "someone@x.com", ok=False)
    assert cli.main(["add"]) == 1
    assert registry.load()["accounts"] == []
    assert not paths.account_dir("account").exists()
    assert list(paths.trash_dir().iterdir()), "moved to trash, never deleted"


def test_add_makes_nothing_when_the_nickname_prompt_is_cancelled(monkeypatch):
    """Ctrl-C at the first question is a no, not an empty nickname. It used to
    read as one, so backing out of `ccs add` left a directory behind."""
    _stub_login(monkeypatch, "someone@x.com")
    monkeypatch.setattr(cli.pickers, "prompt", lambda *a, **k: cli.pickers.CANCEL)
    assert cli.main(["add"]) == 1
    assert registry.load()["accounts"] == []
    assert not list(paths.accounts_root().glob("*/"))


def test_add_trashes_the_directory_when_the_login_step_raises(monkeypatch):
    """Anything at all going wrong between the mkdir and the registry write —
    a Ctrl-C, a missing `claude`, a full disk — leaves no half-made account.
    Trashed, not deleted, in case the login had already written credentials."""
    _stub_login(monkeypatch, "someone@x.com")

    def boom(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.subprocess, "run", boom)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["add"])
    assert registry.load()["accounts"] == []
    assert not paths.account_dir("account").exists()
    assert list(paths.trash_dir().iterdir()), "moved to trash, never deleted"


def test_add_leaves_no_module_on_the_bar_when_it_does_not_finish(monkeypatch):
    """The registry and config.jsonc are written last and together, so an add
    that stops short cannot leave a widget pointing at nothing."""
    _stub_login(monkeypatch, "someone@x.com", ok=False)
    config = paths.waybar_config()
    before = config.read_text(encoding="utf-8")
    assert cli.main(["add"]) == 1
    assert config.read_text(encoding="utf-8") == before


def test_add_registers_the_module_without_generating_any_files(monkeypatch):
    """`cmd_add` used to have to write menu.xml before waybar.apply pointed at
    it, because menu-file is read on the reload that follows. There is no
    menu-file, so there is no ordering rule left — the account directory holds
    symlinks and nothing generated."""
    _stub_login(monkeypatch, "someone@x.com")
    assert cli.main(["add"]) == 0
    directory = paths.account_dir("someone")
    assert directory.exists()
    assert not (directory / "menu.xml").exists()
    assert not (directory / "history.tsv").exists()


def test_account_picker_offers_add_even_with_one_account(monkeypatch):
    """The picker used to be skipped when there was only one account — a
    one-row list asks nothing. Once it carries the Add row that stops being
    true, and skipping it puts Add out of reach for exactly the user most
    likely to want a second account."""
    make_account("work")
    seen = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda p, o, note=None: seen.append(o) or o[0])
    monkeypatch.setattr(cli, "cmd_mode_menu", lambda slug, gui: 0)

    assert cli.cmd_tty([], gui=False) == 0
    assert seen, "the picker was skipped"
    assert seen[0] == ["work", cli.ADD_ROW]


def test_choosing_the_add_row_adds_an_account(monkeypatch):
    """Not a slug, so the account lookup would raise StopIteration on it."""
    make_account("work")
    calls = []
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, note=None: cli.ADD_ROW)
    monkeypatch.setattr(cli, "cmd_add", lambda gui: calls.append(gui) or 0)
    monkeypatch.setattr(cli, "cmd_mode_menu",
                        lambda *a: pytest.fail("add is not a launch"))

    assert cli.cmd_tty([], gui=False) == 0
    assert calls == [False], "gui is passed through to the login window"


def test_choosing_an_account_still_reaches_the_mode_menu(monkeypatch):
    """The common path. The Add row is compared before the account lookup, so
    this pins that it did not swallow real accounts."""
    make_account("work")
    make_account("other")
    seen = []
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, note=None: o[1])
    monkeypatch.setattr(cli, "cmd_mode_menu",
                        lambda slug, gui: seen.append(slug) or 0)
    monkeypatch.setattr(cli, "cmd_add", lambda gui: pytest.fail("no login"))

    assert cli.cmd_tty([], gui=False) == 0
    assert seen == ["other"]


def test_no_accounts_still_goes_straight_to_add(monkeypatch):
    """fuzzel exits instantly on empty stdin, so a picker whose only row is Add
    would be a dead end in GUI mode. With nothing to choose between, don't ask."""
    calls = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda p, o, note=None: pytest.fail("nothing to pick between"))
    monkeypatch.setattr(cli, "cmd_add", lambda gui: calls.append(gui) or 0)

    assert cli.cmd_tty([], gui=False) == 0
    assert calls == [False]


def test_cancelling_the_account_picker_adds_nothing(monkeypatch):
    make_account("work")
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, note=None: None)
    monkeypatch.setattr(cli, "cmd_add", lambda gui: pytest.fail("no login"))
    monkeypatch.setattr(cli, "cmd_mode_menu",
                        lambda *a: pytest.fail("no session"))

    assert cli.cmd_tty([], gui=False) == 1


def test_mode_menu_offers_manage(monkeypatch):
    """The three management actions existed as `ccs manage …` from the day they
    were written, but only a Waybar click ever called them."""
    make_account("work")
    monkeypatch.setattr(cli.launch, "run", lambda *a, **k: pytest.fail("no session"))
    seen = []

    def fake_choose(prompt, options, note=None):
        seen.append(options)
        return "Manage…" if len(seen) == 1 else None

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)

    assert cli.cmd_mode_menu("work", False) == 1  # cancelled at the manage picker
    assert seen[0][-1] == "Manage…", "last, as it is in the Waybar menu"
    assert seen[1] == ["Rename work…", "Remove work…"], "no Add here"


@pytest.mark.parametrize("row, action", [("Rename work…", "rename"),
                                         ("Remove work…", "remove")])
def test_manage_menu_dispatches_with_the_slug(monkeypatch, row, action):
    """cmd_manage runs the real login/prompt/trash paths, so this pins the call
    rather than its effect."""
    make_account("work")
    calls = []
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, note=None: row)
    monkeypatch.setattr(cli, "cmd_manage",
                        lambda a, s, g: calls.append((a, s, g)) or 0)

    assert cli.cmd_manage_menu("work", False) == 0
    assert calls == [(action, "work", False)]


def test_manage_menu_names_the_account_the_way_the_user_sees_it(monkeypatch):
    """Rename shows the nickname — that is what is on the bar and what is about
    to change. Remove shows the slug, because removal trashes the account
    directory and the slug is what that directory is called."""
    reg = registry.load()
    registry.add(reg, "me@x.com", "me@x.com", "work laptop")
    registry.save(reg)
    seen = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda p, o, note=None: seen.append(o) or None)

    assert cli.cmd_manage_menu("me@x.com", False) == 1
    assert seen[0] == ["Rename work laptop…", "Remove me@x.com…"]


def test_cancelling_the_manage_menu_manages_nothing(monkeypatch):
    make_account("work")
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, note=None: None)
    monkeypatch.setattr(cli, "cmd_manage",
                        lambda *a: pytest.fail("cancel is not a command"))

    assert cli.cmd_manage_menu("work", False) == 1


DANGER = "--dangerously-skip-permissions"


def _danger_account(slug="a"):
    make_account(slug)
    reg = registry.load()
    registry.set_field(reg, slug, "dangerous", True)
    registry.save(reg)
    return reg


def test_passthrough_stays_clean_for_an_ordinary_account(monkeypatch):
    make_account("a")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["-p", "hi"]) == 0
    assert DANGER not in runs[0][0]


def test_passthrough_injects_the_flag_for_a_dangerous_account(monkeypatch):
    """`claude -p` cannot prompt — it refuses the tool and exits 2 — so the
    passthrough is the path that most needs the flag, not the least."""
    _danger_account("a")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["-p", "hi"]) == 0
    assert runs[0][0][1:] == [DANGER, "-p", "hi"]


def test_the_flag_is_not_injected_twice_when_typed(monkeypatch):
    """Typing it yourself must not double it."""
    _danger_account("a")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main([DANGER, "-p", "hi"]) == 0
    assert runs[0][0].count(DANGER) == 1


def test_an_explicit_permission_mode_wins_over_the_account_setting(monkeypatch):
    """A flag typed now beats a row clicked days ago; injecting alongside it
    would silently override what the user just asked for."""
    _danger_account("a")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["--permission-mode", "plan", "-p", "hi"]) == 0
    assert DANGER not in runs[0][0]


def test_installation_subcommands_never_get_the_flag(monkeypatch):
    """NO_ACCOUNT_ARGS inspect the install and never run a session."""
    _danger_account("a")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["--version"]) == 0
    assert DANGER not in runs[0][0]


def test_dangerous_command_toggles_and_is_not_exclusive(monkeypatch):
    make_account("a")
    make_account("b")
    assert cli.main(["dangerous", "a"]) == 0
    assert registry.find(registry.load(), "a")["dangerous"] is True
    assert cli.main(["dangerous", "b"]) == 0
    assert [x["dangerous"] for x in registry.load()["accounts"]] == [True, True]
    assert cli.main(["dangerous", "a"]) == 0
    assert registry.find(registry.load(), "a")["dangerous"] is False


def test_dangerous_with_no_argument_lists_the_dangerous_accounts(capsys):
    make_account("a")
    make_account("b")
    cli.main(["dangerous", "b"])
    capsys.readouterr()
    assert cli.main(["dangerous"]) == 0
    assert capsys.readouterr().out.split() == ["b"]


def test_dangerous_rejects_an_unknown_slug():
    assert cli.main(["dangerous", "nope"]) == 1


def test_list_marks_a_dangerous_account(capsys):
    """The setting must be visible without opening a menu."""
    make_account("a")
    make_account("b")
    cli.main(["dangerous", "b"])
    capsys.readouterr()
    cli.main(["list"])
    lines = capsys.readouterr().out.splitlines()
    assert "!" not in lines[0] and "!" in lines[1]


def test_the_mode_menu_offers_the_dangerous_row(monkeypatch):
    """Worded as an action, like the headless row: sat among verbs, a bare
    status line reads as something you cannot click."""
    make_account("a")
    seen = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda prompt, options, note=None: seen.append(options) or None)
    cli.cmd_mode_menu("a", gui=False)
    assert any("Skip permissions (dangerous)" in o for o in seen[0])


def test_picking_the_dangerous_row_toggles_it(monkeypatch):
    make_account("a")
    monkeypatch.setattr(
        cli.pickers, "choose",
        lambda prompt, options, note=None: next(o for o in options if "permissions" in o))
    assert cli.cmd_mode_menu("a", gui=False) == 0
    assert registry.find(registry.load(), "a")["dangerous"] is True


# ── the statusline wrapper ────────────────────────────────────────────────────

PAYLOAD = json.dumps({"rate_limits": {
    "five_hour": {"used_percentage": 94.0, "resets_at": 1785000600}}})


def _delegate(tmp_path, body):
    path = tmp_path / "delegate.sh"
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _stdin(monkeypatch, text):
    monkeypatch.setattr(cli.sys, "stdin",
                        types.SimpleNamespace(buffer=io.BytesIO(text.encode())))


def _feed(monkeypatch, text, config_dir):
    _stdin(monkeypatch, text)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))


def test_statusline_records_under_the_account_it_was_run_for(monkeypatch, tmp_path):
    make_account()
    _feed(monkeypatch, PAYLOAD, paths.account_dir("work"))
    assert cli.main(["statusline"]) == 0
    written = json.loads((paths.account_dir("work") / paths.USAGE_FILE).read_text())
    assert written["five_hour"]["percent"] == 94.0


def test_statusline_under_the_default_account_writes_nothing(monkeypatch, tmp_path):
    """settings.json is shared, so the hook fires for ~/.claude too — including
    the session an agent is most likely running in. Nothing under ~/.claude may
    ever be written by CCAS, and here that holds by construction."""
    make_account()
    home = paths.claude_home()
    before = {p: p.stat().st_mtime_ns for p in home.rglob("*")}

    for config_dir in (home, home / "projects"):
        _feed(monkeypatch, PAYLOAD, config_dir)
        assert cli.main(["statusline"]) == 0
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    _stdin(monkeypatch, PAYLOAD)
    assert cli.main(["statusline"]) == 0

    assert {p: p.stat().st_mtime_ns for p in home.rglob("*")} == before
    assert not list(paths.accounts_root().rglob(paths.USAGE_FILE))


def test_statusline_hands_the_delegate_the_same_bytes_and_its_output_back(
        monkeypatch, tmp_path, capfd):
    make_account()
    _feed(monkeypatch, PAYLOAD, paths.account_dir("work"))
    echo = _delegate(tmp_path, f"cat > {tmp_path}/seen.json\necho -n 'my prompt'\n")
    assert cli.main(["statusline", echo]) == 0
    assert capfd.readouterr().out == "my prompt"
    assert (tmp_path / "seen.json").read_text() == PAYLOAD


def test_statusline_passes_the_delegate_its_own_arguments(monkeypatch, tmp_path, capfd):
    make_account()
    _feed(monkeypatch, PAYLOAD, paths.account_dir("work"))
    args = _delegate(tmp_path, "echo -n \"$1/$2\"\n")
    assert cli.main(["statusline", args, "one", "two"]) == 0
    assert capfd.readouterr().out == "one/two"


def test_a_broken_delegate_still_leaves_the_recording(monkeypatch, tmp_path, capfd):
    """The two halves are independent: the user's statusline is theirs and must
    not be able to lose us a reading, and a lost reading must not blank it."""
    make_account()
    _feed(monkeypatch, PAYLOAD, paths.account_dir("work"))
    assert cli.main(["statusline", str(tmp_path / "does-not-exist")]) != 0
    capfd.readouterr()

    _feed(monkeypatch, PAYLOAD, paths.account_dir("work"))
    assert cli.main(["statusline", _delegate(tmp_path, "exit 3")]) == 3
    assert json.loads((paths.account_dir("work") / paths.USAGE_FILE).read_text())


def test_garbage_on_stdin_does_not_break_the_statusline(monkeypatch, tmp_path, capfd):
    """A statusline that raises is visible in every session; recording is the
    optional half of this command."""
    make_account()
    _feed(monkeypatch, "not json at all", paths.account_dir("work"))
    assert cli.main(["statusline", _delegate(tmp_path, "echo -n ok")]) == 0
    assert capfd.readouterr().out == "ok"


def test_statusline_signals_the_bar_only_when_the_reading_changed(monkeypatch, tmp_path):
    """A per-module signal repaints the label and never rebuilds the bar — but
    the hook fires every few hundred ms, so it may only ride on a real change."""
    make_account()
    fired = []
    monkeypatch.setattr(cli.waybar, "signal", lambda n: fired.append(n))
    signal = registry.find(registry.load(), "work")["signal"]

    _feed(monkeypatch, PAYLOAD, paths.account_dir("work"))
    assert cli.main(["statusline"]) == 0
    assert fired == [signal]

    _feed(monkeypatch, PAYLOAD, paths.account_dir("work"))
    assert cli.main(["statusline"]) == 0
    assert fired == [signal], "an unchanged reading is not news for the bar"


def test_render_puts_the_recorded_reading_on_the_bar(monkeypatch):
    """The 30 s tick is what keeps the reading on screen when no signal fired.

    Usage lives in the format string's tokens, which is the only place it can
    be asked for at all — %5hused, in the default format.
    """
    make_account()
    payload = json.dumps({"rate_limits": {"five_hour": {
        "used_percentage": 94.0, "resets_at": time.time() + 3600}}})
    _feed(monkeypatch, payload, paths.account_dir("work"))
    cli.main(["statusline"])

    out = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    assert cli.main(["render", "work"]) == 0
    assert "94%" in out.getvalue()


def test_the_mode_picker_says_where_the_account_stands(monkeypatch):
    """The row the retired GtkMenu would have carried. It says in words what
    the label cannot: an open window is the good state, not missing data."""
    make_account()
    payload = json.dumps({"rate_limits": {"five_hour": {
        "used_percentage": 94.0, "resets_at": time.time() + 3600}}})
    _feed(monkeypatch, payload, paths.account_dir("work"))
    cli.main(["statusline"])

    seen = {}
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda prompt, options, note=None: seen.update(note=note))
    cli.cmd_mode_menu("work", gui=False)
    assert seen["note"].startswith("5h ≥94% · clears ")


def test_an_account_with_no_reading_is_told_the_hook_is_not_wired(monkeypatch):
    make_account()
    seen = {}
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda prompt, options, note=None: seen.update(note=note))
    cli.cmd_mode_menu("work", gui=False)
    assert seen["note"] == cli.usage.NOT_WIRED


# ── ccs usage ─────────────────────────────────────────────────────────────────

def _record(monkeypatch, slug, five=None, seven=None, fable=None):
    limits = {}
    if five:
        limits["five_hour"] = {"used_percentage": five[0], "resets_at": five[1]}
    if seven:
        limits["seven_day"] = {"used_percentage": seven[0], "resets_at": seven[1]}
    if fable:
        # The hook's own spelling for a model-scoped window: `utilization`, and
        # an ISO string where its siblings above carry epoch seconds.
        limits["model_scoped"] = [
            {"display_name": "Fable", "utilization": fable[0],
             "resets_at": datetime.fromtimestamp(
                 fable[1], timezone.utc).isoformat()}]
    _feed(monkeypatch, json.dumps({"rate_limits": limits}), paths.account_dir(slug))
    cli.main(["statusline"])


def test_usage_shows_both_windows_with_the_readings_age(monkeypatch, capsys):
    """The one place that always shows both windows, however quiet the 7-day
    one is, plus where the number came from and how old it is."""
    make_account("a")
    _record(monkeypatch, "a", five=(94.0, time.time() + 3600),
            seven=(19.0, time.time() + 86400))
    capsys.readouterr()
    assert cli.main(["usage"]) == 0
    out = capsys.readouterr().out
    assert "5h ≥94% clears " in out and "7d ≥19% clears " in out
    assert "statusline, 0m ago" in out


def test_usage_shows_the_fable_window_only_for_an_account_that_has_one(
        monkeypatch, capsys):
    """`ccs usage` is the one place that shows a window however quiet it is —
    but a model-scoped window an account was never told about is not quiet, it
    is not there. Measured on the two real accounts 2026-08-17: one endpoint
    body carried a weekly Fable entry and the other named none.
    """
    make_account("a")
    make_account("b")
    _record(monkeypatch, "a", five=(94.0, time.time() + 3600),
            fable=(5.0, time.time() + 86400))
    _record(monkeypatch, "b", five=(20.0, time.time() + 3600))
    capsys.readouterr()
    assert cli.main(["usage"]) == 0
    lines = {l.split()[0]: l for l in capsys.readouterr().out.splitlines() if l.strip()}
    assert "fable ≥5% clears " in lines["a"]
    assert "fable" not in lines["b"]


def test_usage_names_the_state_of_an_account_with_nothing_recorded(capsys):
    make_account("a")
    capsys.readouterr()
    assert cli.main(["usage"]) == 0
    assert cli.usage.NO_DATA in capsys.readouterr().out


def test_usage_can_be_asked_about_one_account(monkeypatch, capsys):
    make_account("a")
    make_account("b")
    capsys.readouterr()
    assert cli.main(["usage", "b"]) == 0
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 1 and lines[0].startswith("b")
    assert cli.main(["usage", "ghost"]) == 1


def test_usage_says_a_rolled_over_window_has_nothing_spent_in_it(monkeypatch, capsys):
    make_account("a")
    _record(monkeypatch, "a", five=(94.0, time.time() - 60))
    capsys.readouterr()
    cli.main(["usage"])
    assert "5h 0% used" in capsys.readouterr().out


# ── the poll ──────────────────────────────────────────────────────────────────

def test_poll_signals_only_the_accounts_that_changed(monkeypatch, capsys):
    """The signal rides on the write, exactly as it does for the hook: a poll
    that finds the same numbers must not repaint anything."""
    make_account("a")
    make_account("b")
    sent = []
    monkeypatch.setattr(cli.waybar, "signal", lambda n: sent.append(n))
    monkeypatch.setattr(cli.poll, "poll", lambda slugs, **kw: [
        cli.poll.Outcome(slugs[0], cli.poll.OK, "5h ≥41%"),
        cli.poll.Outcome(slugs[1], cli.poll.UNCHANGED, "same numbers")])

    capsys.readouterr()
    assert cli.main(["poll"]) == 0
    assert sent == [registry.find(registry.load(), "a")["signal"]]
    out = capsys.readouterr().out
    assert "5h ≥41%" in out and "same numbers" in out


def test_poll_takes_one_slug(monkeypatch):
    make_account("a")
    make_account("b")
    asked = {}
    monkeypatch.setattr(cli.poll, "poll",
                        lambda slugs, **kw: [asked.setdefault("slugs", slugs)] and [])
    cli.main(["poll", "b"])
    assert asked["slugs"] == ["b"]


def test_poll_rejects_an_unknown_slug(capsys):
    make_account("a")
    assert cli.main(["poll", "ghost"]) == 1


def test_poll_passes_force_through(monkeypatch):
    make_account("a")
    asked = {}
    monkeypatch.setattr(cli.poll, "poll", lambda slugs, **kw: asked.update(kw) or [])
    cli.main(["poll", "--force"])
    assert asked["force"] is True


def test_poll_fails_only_when_every_account_failed(monkeypatch):
    """A timer that reports failure for one dead account would cry wolf in the
    journal every five minutes."""
    make_account("a")
    make_account("b")
    monkeypatch.setattr(cli.waybar, "signal", lambda n: None)
    monkeypatch.setattr(cli.poll, "poll", lambda slugs, **kw: [
        cli.poll.Outcome(slugs[0], cli.poll.OK, ""),
        cli.poll.Outcome(slugs[1], cli.poll.FAILED, "http 401")])
    assert cli.main(["poll"]) == 0

    monkeypatch.setattr(cli.poll, "poll", lambda slugs, **kw: [
        cli.poll.Outcome(s, cli.poll.FAILED, "http 401") for s in slugs])
    assert cli.main(["poll"]) == 1


def test_poll_does_not_open_the_panel(monkeypatch):
    """`ccs poll` runs from a systemd timer with no display at all."""
    make_account("a")
    monkeypatch.setattr(cli.poll, "poll", lambda slugs, **kw: [])
    monkeypatch.setattr(cli, "cmd_panel", _panel_never_called)
    assert cli.main(["poll"]) == 0


# ── the panel's action dispatch ───────────────────────────────────────────────

def _panel_never_called(*a, **k):
    raise AssertionError("this door must not open the panel")


def test_dispatch_new_launches_in_the_selected_project(monkeypatch, tmp_path):
    """The panel supplies a real cwd, which is what lets the GUI verb say
    'New session in ~/alpha' honestly rather than lie about a directory."""
    make_account("work")
    seen = {}
    monkeypatch.setattr(cli.launch, "run", lambda *a: seen.setdefault("args", a) or 0)
    cli.dispatch_panel(cli.panel.Action("new", "work", str(tmp_path / "alpha")))
    assert seen["args"] == ("work", "new", str(tmp_path / "alpha"), True,
                            str(tmp_path / "alpha"))


def test_dispatch_resume_passes_the_uuid_through(monkeypatch):
    """No picker in between — resume mode takes the uuid the panel already has."""
    make_account("work")
    seen = {}
    monkeypatch.setattr(cli.launch, "run", lambda *a: seen.setdefault("args", a) or 0)
    cli.dispatch_panel(cli.panel.Action("resume", "work", "uuid-1"))
    assert seen["args"][1:3] == ("resume", "uuid-1")


def test_dispatch_headless_stays_exclusive(monkeypatch):
    """registry.set_headless() is what keeps it exclusive; the panel must route
    through it rather than set the field on one account."""
    make_account("work")
    make_account("other")
    cli.dispatch_panel(cli.panel.Action("headless", "other", None))
    assert registry.headless_slug(registry.load()) == "other"
    cli.dispatch_panel(cli.panel.Action("headless", "other", None))
    assert registry.headless_slug(registry.load()) is None


def test_dispatch_dangerous_flips_only_its_own_account(monkeypatch):
    """Unlike headless, dangerous is per-account and not exclusive."""
    make_account("work")
    make_account("other")
    cli.dispatch_panel(cli.panel.Action("dangerous", "work", None))
    reg = registry.load()
    assert registry.find(reg, "work")["dangerous"] is True
    assert registry.find(reg, "other")["dangerous"] is False


def test_dispatch_hide_icon_toggles(monkeypatch):
    make_account("work")
    cli.dispatch_panel(cli.panel.Action("hide_icon", "work", None))
    assert registry.find(registry.load(), "work")["hide_icon"] is True


def test_dispatch_color_goes_through_the_registry(monkeypatch):
    """set_field() validates the colour. The panel must not get its own
    unchecked write path."""
    make_account("work")
    cli.dispatch_panel(cli.panel.Action("color", "work", "#89b4fa"))
    assert registry.find(registry.load(), "work")["color"] == "#89b4fa"
    assert cli.dispatch_panel(cli.panel.Action("color", "work", "nonsense")) == 1


def test_dispatch_switch_reclaims_the_lock_for_the_new_account(monkeypatch, tmp_path):
    """A switch is applied inside the open panel now, so the only thing left for
    cli to do is move the lock: the bar's toggle compares (slug, output) against
    the widget clicked, and a panel showing `two` behind a lock saying `one`
    makes `two`'s own widget read as a different one — it would close the panel
    and open a fresh one, which is the reload this removed."""
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "panel.lock"))
    make_account("one")
    make_account("two")
    cli.panel.claim("one", "DP-1")
    assert cli.dispatch_panel(cli.panel.Action("switch", "two", None)) == 0
    assert cli.panel.running_panel() == (os.getpid(), "two", "DP-1")


def test_dispatch_switch_without_a_lock_still_answers(monkeypatch, tmp_path):
    """The connector is read back from the lock this process wrote. There is
    always one — but a panel that lost it must switch account anyway rather than
    traceback out of a chip click."""
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "panel.lock"))
    make_account("two")
    assert cli.dispatch_panel(cli.panel.Action("switch", "two", None)) == 0
    assert cli.panel.running_panel() == (os.getpid(), "two", None)


def test_dispatch_none_is_a_cancel(monkeypatch):
    """Esc and click-outside both arrive as no action."""
    assert cli.dispatch_panel(None) == 1


def test_dispatch_rejects_an_unknown_kind(monkeypatch):
    """A typo in the widget tree must not silently do nothing."""
    make_account("work")
    with pytest.raises(ValueError):
        cli.dispatch_panel(cli.panel.Action("teleport", "work", None))


# ── the GUI door ──────────────────────────────────────────────────────────────

def test_gui_mode_menu_opens_the_panel_not_the_picker(monkeypatch):
    """The whole change, in one test: a bar click reaches the panel."""
    make_account("one")
    monkeypatch.setattr(cli.pickers, "choose", _panel_never_called)
    monkeypatch.setattr(cli.panel_ui, "show", lambda s, gate=None, apply=None: None)
    assert cli.cmd_mode_menu("one", True) == 1


def test_the_panel_is_opened_once(monkeypatch):
    """One click, one surface. cmd_panel used to loop, because a chip click came
    back as an action and the next account needed a whole new window; the panel
    swaps its own body instead, so there is nothing left to loop over."""
    make_account("one")
    make_account("two")
    seen = []

    def fake_show(state, gate=None, apply=None):
        seen.append(state["slug"])
        return None

    monkeypatch.setattr(cli.panel_ui, "show", fake_show)
    cli.cmd_panel("one")
    assert seen == ["one"]


def test_terminal_mode_menu_still_uses_fzf(monkeypatch):
    """A TTY and an ssh session cannot run GTK. The terminal door is unchanged
    and must not be routed through the panel."""
    make_account("one")
    monkeypatch.setattr(cli.panel_ui, "show", _panel_never_called)
    monkeypatch.setattr(cli.pickers, "choose", lambda *a, **k: None)
    assert cli.cmd_mode_menu("one", False) == 1


def _gated(monkeypatch, connector):
    """Run cmd_panel with a panel that reports `connector` as its output.

    The toggle cannot be decided before the panel process knows which monitor
    was clicked, and only the pointer probe knows that — so cmd_panel hands
    panel_ui.show a gate and show calls it with the answer. Returns the slugs
    that were actually opened.
    """
    opened = []

    def fake_show(state, gate=None, apply=None):
        if gate is not None and not gate(connector):
            return None
        opened.append(state["slug"])
        return None

    monkeypatch.setattr(cli.panel_ui, "show", fake_show)
    return opened


def test_clicking_the_same_widget_twice_closes_the_panel(monkeypatch, tmp_path):
    """The bar's click is a toggle. A second one on the widget that opened the
    panel closes it and opens nothing — anything else means the only way to
    dismiss it is to find its close button."""
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "panel.lock"))
    make_account("one")
    monkeypatch.setattr(cli.panel, "close_running", lambda: ("one", "DP-1"))
    opened = _gated(monkeypatch, "DP-1")
    assert cli.cmd_panel("one") == 0
    assert opened == []


def test_the_same_account_clicked_on_the_other_bar_moves_the_panel(monkeypatch, tmp_path):
    """Waybar draws every module on every monitor, so one account has one widget
    per bar. Clicking its copy on the other screen used to close the panel and
    open nothing — the toggle compared slugs, and the slug was the same. It is
    the same widget only if it is also the same monitor.
    """
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "panel.lock"))
    make_account("one")
    monkeypatch.setattr(cli.panel, "close_running", lambda: ("one", "DP-1"))
    opened = _gated(monkeypatch, "HDMI-A-1")
    cli.cmd_panel("one")
    assert opened == ["one"]


def test_the_open_panel_is_closed_after_the_probe_and_not_before(monkeypatch, tmp_path):
    """Order, and it is what the per-monitor toggle rests on.

    Closing first is what a toggle reads like, but the decision needs the
    connector the click came from and only the probe knows it — so closing
    before there is an answer means deciding without one, which is the bug this
    file's neighbour pins. A panel that is still up costs the probe nothing:
    measured 2026-07-26 at 29 ms to answer with another panel open on the same
    output. `docs/why.md` has the part of this that could not be measured.
    """
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "panel.lock"))
    make_account("one")
    order = []
    monkeypatch.setattr(cli.panel, "close_running",
                        lambda: order.append("closed") or None)

    def fake_show(state, gate=None, apply=None):
        order.append("probed")
        gate("DP-1")
        return None

    monkeypatch.setattr(cli.panel_ui, "show", fake_show)
    cli.cmd_panel("one")
    assert order == ["probed", "closed"]


def test_the_panel_is_only_ever_asked_to_close_one_panel(monkeypatch, tmp_path):
    """The gate is the toggle, and it runs exactly once now. It used to run
    again whenever a chip reopened the panel on another account, by which time
    the lock named *this* process — a second close_running() there would have
    been the panel sending itself SIGTERM, which is why the gate carried a
    `first` flag it no longer needs."""
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "panel.lock"))
    make_account("one")
    closes = []
    monkeypatch.setattr(cli.panel, "close_running",
                        lambda: closes.append(1) or None)
    gates = []

    def fake_show(state, gate=None, apply=None):
        gates.append(gate("DP-1"))
        return None

    monkeypatch.setattr(cli.panel_ui, "show", fake_show)
    cli.cmd_panel("one")
    assert gates == [True]
    assert len(closes) == 1


def test_the_open_panel_records_the_monitor_it_opened_on(monkeypatch, tmp_path):
    """Which is what the next click compares against, so it has to be the
    output the panel actually took — after the probe, not before it."""
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "panel.lock"))
    make_account("one")
    monkeypatch.setattr(cli.panel, "close_running", lambda: None)
    held = []

    def fake_show(state, gate=None, apply=None):
        gate("HDMI-A-1")
        held.append(cli.panel.running_panel())
        return None

    monkeypatch.setattr(cli.panel_ui, "show", fake_show)
    cli.cmd_panel("one")
    assert held == [(os.getpid(), "one", "HDMI-A-1")]


def test_clicking_another_account_replaces_the_open_panel(monkeypatch, tmp_path):
    """Two widgets, one panel: the second account's click closes the first
    account's panel and opens its own, rather than stacking them."""
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "panel.lock"))
    make_account("one")
    make_account("two")
    monkeypatch.setattr(cli.panel, "close_running", lambda: ("one", "DP-1"))
    opened = _gated(monkeypatch, "DP-1")
    cli.cmd_panel("two")
    assert opened == ["two"]


def test_the_open_panel_is_findable_and_stops_being_so(monkeypatch, tmp_path):
    """The lock is claimed for the whole time the panel is up and gone after,
    including when the panel raises — a lock outliving its process makes the
    next click a no-op."""
    monkeypatch.setenv("CCAS_PANEL_LOCK", str(tmp_path / "panel.lock"))
    make_account("one")
    held = []

    def fake_show(_state, gate=None, apply=None):
        gate("DP-1")
        held.append(cli.panel.running_panel())
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.panel_ui, "show", fake_show)
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_panel("one")
    assert held == [(os.getpid(), "one", "DP-1")]
    assert cli.panel.running_panel() is None


def _spawns(monkeypatch):
    """Popen, not run: a terminal opened from the panel is detached and never
    waited on — see `test_a_manage_terminal_does_not_wait_for_itself`. `run` is
    stubbed alongside it so that a path taking the old route fails loudly here
    rather than spawning a real kitty."""
    seen = []
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda cmd, **k: seen.append((cmd, k)))
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda cmd, **k: pytest.fail(f"blocking spawn: {cmd}"))
    return seen


def test_manage_from_the_panel_opens_a_terminal(monkeypatch):
    """Rename, remove and add are the three things the panel names but cannot
    ask about: each needs free text or a login, and the panel closes the moment
    a button is clicked.

    They go to a terminal running the same `ccs manage` the user could type,
    rather than to a prompt on a stdin that a bar click does not have — an
    input() there blocks forever against a console nobody can see.
    """
    make_account("one")
    seen = _spawns(monkeypatch)
    assert cli.dispatch_panel(cli.panel.Action("rename", "one", None)) == 0
    inner = seen[0][0][-1]
    assert "'manage' 'rename' 'one'" in inner
    assert str(paths.ccs_bin()) in inner


def test_add_from_the_panel_opens_a_terminal(monkeypatch):
    """`ccs add` logs in. That has never been something to run without a
    terminal attached."""
    make_account("one")
    seen = _spawns(monkeypatch)
    cli.dispatch_panel(cli.panel.Action("add", "one", None))
    assert "add" in " ".join(seen[0][0])


def test_a_terminal_opened_from_the_panel_is_not_a_child_session(monkeypatch):
    """The panel may have been started from an agent shell, and everything it
    spawns inherits that. The same scrub every launch already gets."""
    make_account("one")
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    seen = _spawns(monkeypatch)
    cli.dispatch_panel(cli.panel.Action("rename", "one", None))
    assert "CLAUDE_CODE_CHILD_SESSION" not in seen[0][1]["env"]


# ── ccs format ────────────────────────────────────────────────────────────────

def test_format_shows_the_current_format(capsys):
    make_account()
    assert cli.main(["format", "work"]) == 0
    assert fmt.DEFAULT_FORMAT in capsys.readouterr().out


def test_format_sets_it():
    make_account()
    assert cli.main(["format", "work", "%name %5hused"]) == 0
    assert registry.find(registry.load(), "work")["format"] == "%name %5hused"


def test_format_warns_about_an_unknown_token_but_still_sets_it(capsys):
    make_account()
    assert cli.main(["format", "work", "%name %bogus"]) == 0
    assert "%bogus" in capsys.readouterr().out
    assert registry.find(registry.load(), "work")["format"] == "%name %bogus"


def test_format_sets_one_tokens_colour():
    make_account()
    assert cli.main(["format", "work", "--color", "%name", "#89b4fa"]) == 0
    assert registry.find(registry.load(), "work")["format_colors"]["%name"] \
        == "#89b4fa"


def test_setting_a_colour_to_a_dash_deletes_the_key():
    """Absent is a value the dict can hold and typing it is not, so '-' is how
    a token is put back to format.DEFAULT_COLOR."""
    make_account()
    cli.main(["format", "work", "--color", "%name", "#89b4fa"])
    cli.main(["format", "work", "--color", "%name", "-"])
    assert "%name" not in registry.find(registry.load(), "work")["format_colors"]


def test_format_color_rejects_every_retired_colour_name():
    """auto, dim and account each meant "work it out from something else".
    All three are gone and none of them may come back through the CLI."""
    make_account()
    for retired in ("auto", "dim", "account"):
        assert cli.main(["format", "work", "--color", "%name", retired]) == 1


def test_format_color_dash_still_clears_a_token():
    make_account()
    assert cli.main(["format", "work", "--color", "%name", "#89b4fa"]) == 0
    assert cli.main(["format", "work", "--color", "%name", "-"]) == 0
    account = registry.find(registry.load(), "work")
    assert "%name" not in account["format_colors"]


def test_format_rejects_a_bad_colour():
    make_account()
    assert cli.main(["format", "work", "--color", "%name", "red"]) == 1


def test_format_tokens_lists_them(capsys):
    assert cli.main(["format", "--tokens"]) == 0
    out = capsys.readouterr().out
    for token in ("%name", "%email", "%5hreset", "%7dquotaleft", "%5h"):
        assert token in out


def test_format_tokens_names_them_too(capsys):
    """It is the "what can I type" reference, and a bare list of tokens answers
    half the question — the token is the spelling, the name is the meaning."""
    cli.main(["format", "--tokens"])
    out = capsys.readouterr().out
    for line in out.splitlines():
        token = line.split()[0]
        assert fmt.NAMES[token] in line


def test_the_passthrough_still_wins_over_the_new_command(monkeypatch):
    """`ccs -p "format the disk"` is claude's. The two passthrough branches must
    stay ahead of every command name, including this one."""
    seen = []
    monkeypatch.setattr(cli, "cmd_tty", lambda args, gui: seen.append(args) or 0)
    cli.main(["-p", "format"])
    assert seen == [["-p", "format"]]


def test_the_panel_sets_one_tokens_colour():
    make_account()
    assert cli.dispatch_panel(
        panel.Action("format_color", "work", ("%name", "#89b4fa"))) == 0
    assert registry.find(registry.load(), "work")["format_colors"]["%name"] \
        == "#89b4fa"


def test_the_panel_clears_a_colour_by_choosing_auto():
    make_account()
    cli.dispatch_panel(panel.Action("format_color", "work", ("%name", "dim")))
    cli.dispatch_panel(panel.Action("format_color", "work", ("%name", "auto")))
    assert "%name" not in registry.find(registry.load(), "work")["format_colors"]


def test_the_panel_rejects_a_bad_colour():
    make_account()
    assert cli.dispatch_panel(
        panel.Action("format_color", "work", ("%name", "' x='y"))) == 1


def test_the_panel_sets_the_format_itself():
    """It used to spawn a terminal: the panel was gone by the time the prompt
    appeared, and the format was the last widget setting that left the panel to
    be changed."""
    make_account()
    assert cli.dispatch_panel(panel.Action("format", "work", "%name")) == 0
    assert registry.find(registry.load(), "work")["format"] == "%name"


def test_the_format_action_signals_the_bar(monkeypatch):
    """Through _mutate, so _refresh runs — the same path `ccs format` takes.
    The panel gains no write path of its own."""
    make_account()
    seen = []
    monkeypatch.setattr(cli, "_mutate",
                        lambda *a: seen.append(a) or 0)
    cli.dispatch_panel(panel.Action("format", "work", "%name %5hused"))
    assert seen == [("work", "format", "%name %5hused")]


def test_the_format_action_stores_an_unknown_token_as_typed():
    """Warned about beside the entry, never rejected: it renders as its own
    name on the bar, which is visible where a refusal is not."""
    make_account()
    assert cli.dispatch_panel(panel.Action("format", "work", "%bogus")) == 0
    assert registry.find(registry.load(), "work")["format"] == "%bogus"


def test_the_edit_format_action_is_gone():
    """Nothing generates a terminal for the format any more. `ccs format
    <slug> --edit` stays as the terminal door — a TTY and an ssh session
    cannot run GTK — but it is reached by typing it."""
    with pytest.raises(ValueError):
        cli.dispatch_panel(panel.Action("edit_format", "work", None))


def test_format_edit_prompts_for_a_new_format(monkeypatch):
    """The panel's button lands here: a bare `ccs format <slug>` printed and
    exited, so the terminal it opened closed before anything could be typed."""
    make_account()
    monkeypatch.setattr(cli.pickers, "prompt_edit", lambda *_: "%name %5hleft")
    assert cli.main(["format", "work", "--edit"]) == 0
    assert registry.find(registry.load(), "work")["format"] == "%name %5hleft"


def test_format_edit_seeds_the_prompt_with_the_current_format(monkeypatch):
    make_account()
    cli.main(["format", "work", "%name"])
    seen = []
    monkeypatch.setattr(cli.pickers, "prompt_edit",
                        lambda message, initial: seen.append(initial) or None)
    cli.main(["format", "work", "--edit"])
    assert seen == ["%name"]


def test_format_edit_lists_the_tokens_first(monkeypatch, capsys):
    """Free text against a token table nobody can see is a guess."""
    make_account()
    monkeypatch.setattr(cli.pickers, "prompt_edit", lambda *_: None)
    cli.main(["format", "work", "--edit"])
    assert "%5h" in capsys.readouterr().out


def test_format_edit_names_each_token_beside_it(monkeypatch, capsys):
    """The token stays — the prompt under the table wants it typed — but a
    column saying what it is spares reading the render to find out.

    Three columns that line up for *every* token, checked rather than a width
    spelled out here: the widest token stopped being a constant when the Fable
    set arrived — `%fablequotaleft` is two characters past what the table used
    to reserve — and a hardcoded pad turns adding a token into a ragged table
    nobody notices until they read one.
    """
    make_account()
    monkeypatch.setattr(cli.pickers, "prompt_edit", lambda *_: None)
    cli.main(["format", "work", "--edit"])
    rows = [line for line in capsys.readouterr().out.splitlines()
            if line.startswith("  %")]
    assert len(rows) == len(fmt.TOKENS) + 1          # every token, and %%
    def name_column(token, row):
        # Searched past the token, or `%email` finds "email" inside itself.
        return row.index(fmt.NAMES[token], 2 + len(token))

    for token, row in zip(fmt.TOKENS, rows):
        assert row.startswith(f"  {token} ")
        assert name_column(token, row) == name_column("%name", rows[0])


def test_format_edit_cancelled_leaves_the_format_alone(monkeypatch):
    make_account()
    cli.main(["format", "work", "%name"])
    monkeypatch.setattr(cli.pickers, "prompt_edit", lambda *_: None)
    assert cli.main(["format", "work", "--edit"]) == 0
    assert registry.find(registry.load(), "work")["format"] == "%name"


def test_format_edit_warns_about_an_unknown_token(monkeypatch, capsys):
    make_account()
    monkeypatch.setattr(cli.pickers, "prompt_edit", lambda *_: "%name %bogus")
    monkeypatch.setattr(cli.pickers, "prompt", lambda *_: None)
    assert cli.main(["format", "work", "--edit"]) == 0
    assert "%bogus" in capsys.readouterr().out


def test_display_command_is_gone():
    """An unknown command returns non-zero rather than silently doing nothing."""
    assert cli.main(["display", "a", "custom"]) != 0


def test_a_manage_terminal_does_not_wait_for_itself(monkeypatch):
    """Same rule as `launch.run`'s gui branch, and for the same reason: this is
    reached from the panel, whose process Waybar's module thread is waiting on.
    A rename left open in a terminal must not stop that account's widget from
    repainting."""
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda *a, **k: pytest.fail("a manage terminal must not wait"))
    spawned = []
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda argv, **kw: spawned.append((argv, kw)))
    assert cli._in_terminal(["manage", "rename", "work"]) == 0
    (argv, kw), = spawned
    assert argv[0] == "kitty"
    assert kw["start_new_session"] is True


def test_hidden_takes_the_widget_off_the_bar_and_leaves_the_account(monkeypatch):
    """Off the bar is not removed: `ccs rm` trashes the directory, this does
    not touch it. The account still resolves, still runs, still records."""
    make_account("work")
    make_account("home")
    assert cli.main(["hidden", "home"]) == 0
    assert registry.find(registry.load(), "home")["hidden"] is True
    assert paths.account_dir("home").is_dir()
    text = paths.waybar_config().read_text(encoding="utf-8")
    assert "custom/cc-home" not in text
    assert "custom/cc-work" in text
    assert "#custom-cc-home" not in paths.waybar_style().read_text(encoding="utf-8")


def test_hidden_toggles_back(monkeypatch):
    make_account("work")
    assert cli.main(["hidden", "work"]) == 0
    assert cli.main(["hidden", "work"]) == 0
    assert registry.find(registry.load(), "work")["hidden"] is False
    assert "custom/cc-work" in paths.waybar_config().read_text(encoding="utf-8")


def test_hidden_reloads_the_bar_rather_than_signalling_one_module(monkeypatch):
    """The set of modules is what changed, and Waybar reads that at startup —
    SIGRTMIN+n re-runs a module's exec, which cannot remove the module."""
    make_account("work")
    calls = []
    monkeypatch.setattr(cli.waybar, "reload", lambda: calls.append("reload"))
    monkeypatch.setattr(cli.waybar, "signal", lambda n: calls.append(f"signal{n}"))
    assert cli.main(["hidden", "work"]) == 0
    assert calls == ["reload"]


def test_hidden_with_no_slug_lists_what_is_off_the_bar(capsys):
    make_account("work")
    make_account("home")
    cli.main(["hidden", "home"])
    capsys.readouterr()
    assert cli.main(["hidden"]) == 0
    assert capsys.readouterr().out == "home\n"


def test_hidden_for_an_unknown_account_fails_quietly():
    assert cli.main(["hidden", "ghost"]) == 1


def test_list_says_which_account_is_off_the_bar(capsys):
    make_account("work")
    make_account("home")
    cli.main(["hidden", "home"])
    capsys.readouterr()
    cli.main(["list"])
    out = capsys.readouterr().out.splitlines()
    assert "(off the bar)" in next(l for l in out if "home" in l)
    assert "(off the bar)" not in next(l for l in out if "work" in l)
