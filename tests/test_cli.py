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

    def fake_choose(prompt, options, gui):
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
                        lambda p, o, g: asked.append(o) or "work")
    cli.main(["tty", "-p", "hi"])
    assert asked, "the point is knowing which account answered"


def test_declining_the_headless_prompt_runs_nothing(monkeypatch):
    make_account("work")
    runs = _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g: None)
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


def test_setting_the_headless_runner_rewrites_every_menu(monkeypatch):
    """The mark lives in each account's menu.xml, and turning it on for one
    account turns it off for the others — so all of them need rewriting."""
    make_account("work")
    make_account("other")
    reloads = []
    monkeypatch.setattr(cli.waybar, "reload", lambda: reloads.append(1))
    cli.main(["headless", "work"])
    assert f"{cli.menu.MARK_ON} Headless runner" in (
        paths.account_dir("work") / "menu.xml").read_text()
    assert f"{cli.menu.MARK_OFF} Headless runner" in (
        paths.account_dir("other") / "menu.xml").read_text()
    assert reloads, "the marks are baked into the cached menu"


def test_mode_menu_offers_the_headless_runner_and_shows_its_state(monkeypatch):
    """The third way in. The Waybar row and `ccs headless` both existed, but the
    terminal path — pick an account, then pick what to do with it — had no way to
    say "this is the one that answers claude -p"."""
    make_account("work")
    monkeypatch.setattr(cli.launch, "run", lambda *a, **k: pytest.fail("no session"))
    seen = []

    def fake_choose(prompt, options, gui):
        seen.append(options)
        return next(o for o in options if "headless runner" in o.lower())

    monkeypatch.setattr(cli.pickers, "choose", fake_choose)

    assert cli.cmd_mode_menu("work", False) == 0
    assert f"{cli.menu.MARK_OFF} Select as headless runner" in seen[0]
    assert registry.headless_slug(registry.load()) == "work"

    assert cli.cmd_mode_menu("work", False) == 0
    # The mark carries the state, but on its own it does not read as something
    # you can pick — in a flat fzf list of verbs it looks like a status line.
    # The wording has to say what picking it does, in both directions.
    assert f"{cli.menu.MARK_ON} Headless runner — pick to clear" in seen[1]
    assert registry.headless_slug(registry.load()) is None, "picking it again clears"


def test_mode_menu_still_launches_the_other_four_rows(monkeypatch):
    """The headless row is appended, not spliced in — 'All projects…' is the
    catch-all in this dispatch and must not swallow the new row."""
    make_account("work")
    calls = []
    monkeypatch.setattr(cli.launch, "run",
                        lambda *a, **k: calls.append(a) or 0)
    for i in range(4):
        monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g, i=i: o[i])
        assert cli.cmd_mode_menu("work", False) == 0
    assert [c[1] for c in calls] == ["new", "last", "search", "new"]
    assert len(calls) == 4


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


def test_passthrough_does_not_force_gui_mode(monkeypatch):
    """The other half of test_waybar's --gui test: `ccs -p` is typed at a real
    terminal, so its prompts must use fzf. --gui is waybar's alone."""
    make_account("a")
    make_account("b")
    _stub_claude(monkeypatch)
    monkeypatch.setattr(cli.pickers, "is_gui", lambda: False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    seen = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda prompt, options, gui: seen.append(gui) or options[0])
    assert cli.main(["-p", "hi"]) == 0
    assert seen == [False]


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
    _stub_login(monkeypatch, "vsedlacek1337@gmail.com")
    assert cli.main(["add"]) == 0
    reg = registry.load()
    assert [a["slug"] for a in reg["accounts"]] == ["vsedlacek1337"]
    assert paths.account_dir("vsedlacek1337").is_dir()
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


def test_add_writes_the_menu_before_waybar_points_at_it(monkeypatch):
    """Found by `ccs doctor` on a sandbox add: the new module's menu-file did
    not exist yet. The 30 s render tick heals it (session_set_changed returns
    True when no snapshot exists), but until then a click on the new icon opens
    a menu Waybar cannot read."""
    _stub_login(monkeypatch, "someone@x.com")
    assert cli.main(["add"]) == 0
    directory = paths.account_dir("someone")
    assert (directory / "menu.xml").exists()
    assert (directory / "history.tsv").exists()


def test_account_picker_offers_add_even_with_one_account(monkeypatch):
    """The picker used to be skipped when there was only one account — a
    one-row list asks nothing. Once it carries the Add row that stops being
    true, and skipping it puts Add out of reach for exactly the user most
    likely to want a second account."""
    make_account("work")
    seen = []
    monkeypatch.setattr(cli.pickers, "choose",
                        lambda p, o, g: seen.append(o) or o[0])
    monkeypatch.setattr(cli, "cmd_mode_menu", lambda slug, gui: 0)

    assert cli.cmd_tty([], gui=False) == 0
    assert seen, "the picker was skipped"
    assert seen[0] == ["work", cli.ADD_ROW]


def test_choosing_the_add_row_adds_an_account(monkeypatch):
    """Not a slug, so the account lookup would raise StopIteration on it."""
    make_account("work")
    calls = []
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g: cli.ADD_ROW)
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
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g: o[1])
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
                        lambda p, o, g: pytest.fail("nothing to pick between"))
    monkeypatch.setattr(cli, "cmd_add", lambda gui: calls.append(gui) or 0)

    assert cli.cmd_tty([], gui=False) == 0
    assert calls == [False]


def test_cancelling_the_account_picker_adds_nothing(monkeypatch):
    make_account("work")
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g: None)
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

    def fake_choose(prompt, options, gui):
        seen.append(options)
        return "Manage…" if prompt == "mode" else None

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
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g: row)
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
                        lambda p, o, g: seen.append(o) or None)

    assert cli.cmd_manage_menu("me@x.com", False) == 1
    assert seen[0] == ["Rename work laptop…", "Remove me@x.com…"]


def test_cancelling_the_manage_menu_manages_nothing(monkeypatch):
    make_account("work")
    monkeypatch.setattr(cli.pickers, "choose", lambda p, o, g: None)
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
                        lambda prompt, options, gui: seen.append(options) or None)
    cli.cmd_mode_menu("a", gui=False)
    assert any("Skip permissions (dangerous)" in o for o in seen[0])


def test_picking_the_dangerous_row_toggles_it(monkeypatch):
    make_account("a")
    monkeypatch.setattr(
        cli.pickers, "choose",
        lambda prompt, options, gui: next(o for o in options if "permissions" in o))
    assert cli.cmd_mode_menu("a", gui=False) == 0
    assert registry.find(registry.load(), "a")["dangerous"] is True
