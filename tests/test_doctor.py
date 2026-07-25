"""`ccs doctor`: assert the invariants that live outside this repo.

Every bug this tool has hit on the real system was drift between four places —
`config.jsonc`, `~/.bashrc`, the account directories and the registry — rather
than a logic bug. These are the checks that were being run by hand.
"""
import importlib
import os

import pytest

import ccas.paths as paths
import ccas.registry as registry
import ccas.accounts as accounts
import ccas.waybar as waybar
import ccas.menu as menu
import ccas.doctor as doctor
import ccas.cli as cli


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    home = tmp_path / "claude"
    (home / "projects").mkdir(parents=True)
    (home / "settings.json").write_text("{}", encoding="utf-8")
    cfg = tmp_path / "config.jsonc"
    cfg.write_text('{\n    "modules-right": ["clock"]\n}\n', encoding="utf-8")
    rc = tmp_path / "bashrc"
    rc.write_text("export EDITOR=vim\n", encoding="utf-8")
    binary = tmp_path / "claude-bin"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(binary, 0o755)
    ccs = tmp_path / "ccs-bin"
    ccs.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(ccs, 0o755)
    monkeypatch.setenv("CCAS_HOME", str(home))
    monkeypatch.setenv("CCAS_CLAUDE_JSON", str(tmp_path / "claude.json"))
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    monkeypatch.setenv("CCAS_TRASH", str(tmp_path / "trash"))
    monkeypatch.setenv("CCAS_WAYBAR_CONFIG", str(cfg))
    monkeypatch.setenv("CCAS_BASHRC", str(rc))
    monkeypatch.setenv("CCAS_CLAUDE_BIN", str(binary))
    monkeypatch.setenv("CCAS_CCS_BIN", str(ccs))
    monkeypatch.setenv("CCAS_NO_RELOAD", "1")
    for module in (paths, registry, accounts, waybar, menu, doctor, cli):
        importlib.reload(module)
    return tmp_path


def healthy():
    """One account, installed the way `ccs config` leaves it."""
    reg = registry.load()
    registry.add(reg, "work", "work@x.com", "work")
    registry.save(reg)
    accounts.create("work")
    accounts.relink("work")
    menu.write(registry.find(registry.load(), "work"), [])
    waybar.apply(registry.load())
    return registry.load()


def failures(reg):
    return [c for c in doctor.run(reg) if not c.ok]


def test_a_healthy_install_reports_no_failures():
    assert failures(healthy()) == []


def test_doctor_never_writes_anything(_isolate):
    reg = healthy()
    watched = [paths.claude_home() / "settings.json", paths.waybar_config(),
               paths.bashrc(), paths.registry_file(),
               paths.account_dir("work") / "menu.xml"]
    before = [p.stat().st_mtime_ns for p in watched]
    doctor.run(reg)
    assert [p.stat().st_mtime_ns for p in watched] == before
    assert not [p for p in paths.claude_home().iterdir() if p.is_symlink()]


def test_a_symlink_inside_claude_home_is_the_loudest_failure():
    """The one invariant the whole design rests on: links point *at* ~/.claude,
    never out of it."""
    reg = healthy()
    (paths.claude_home() / "oops").symlink_to(paths.account_dir("work"))
    bad = failures(reg)
    assert bad and "~/.claude" in bad[0].label
    assert "oops" in bad[0].detail


def test_a_dangling_account_symlink_is_reported():
    reg = healthy()
    (paths.claude_home() / "settings.json").unlink()
    bad = failures(reg)
    assert any("work" in c.label and "link" in c.label for c in bad), bad


def test_a_missing_menu_file_is_reported():
    reg = healthy()
    (paths.account_dir("work") / "menu.xml").unlink()
    assert any("menu" in c.label for c in failures(reg))


def test_a_waybar_config_out_of_step_with_the_registry_is_reported():
    """The symptom is an account with no icon, or a module Waybar cannot
    resolve — which is a config error for the whole bar, not just for us."""
    reg = healthy()
    registry.add(reg, "other", "other@x.com", "other")
    registry.save(reg)
    accounts.create("other")
    accounts.relink("other")
    menu.write(registry.find(registry.load(), "other"), [])
    bad = failures(registry.load())
    assert any("waybar" in c.label.lower() for c in bad), bad
    assert any("ccs config" in c.detail for c in bad), "say how to fix it"


def test_a_default_pointing_at_no_account_is_reported():
    """`default` is a top-level slug, so unlike `headless` it can dangle — and
    it is what every NO_ACCOUNT_ARGS run resolves to."""
    reg = healthy()
    reg["default"] = "gone"
    registry.save(reg)
    assert any("default" in c.label for c in failures(registry.load()))


def test_two_headless_accounts_are_reported():
    """`set_headless` keeps it exclusive; a hand-edited registry does not, and
    then which account answers `-p` is whichever is listed first."""
    reg = healthy()
    registry.add(reg, "other", "other@x.com", "other")
    for account in reg["accounts"]:
        account["headless"] = True
    registry.save(reg)
    accounts.create("other")
    accounts.relink("other")
    menu.write(registry.find(registry.load(), "other"), [])
    waybar.apply(registry.load())
    assert any("headless" in c.label for c in failures(registry.load()))


def test_a_missing_claude_binary_is_reported(monkeypatch, _isolate):
    reg = healthy()
    monkeypatch.setenv("CCAS_CLAUDE_BIN", str(_isolate / "nowhere"))
    importlib.reload(paths)
    assert any("claude binary" in c.label for c in failures(reg))


def test_a_legacy_shell_function_is_reported():
    """New shells are clean after the install that removed it, but a shell open
    since before then still shadows `claude` — invisible drift, exactly what a
    doctor is for."""
    reg = healthy()
    paths.bashrc().write_text(
        f"# {waybar.RULE}\n# {waybar.START}\nclaude() {{ :; }}\n"
        f"# {waybar.END}\n# {waybar.RULE}\n", encoding="utf-8")
    bad = failures(reg)
    assert any("shell function" in c.label for c in bad), bad


def test_the_command_exits_nonzero_only_when_something_failed(capsys):
    healthy()
    assert cli.main(["doctor"]) == 0
    (paths.account_dir("work") / "menu.xml").unlink()
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "menu" in out


def test_doctor_is_ours_and_the_escape_still_reaches_claudes(monkeypatch):
    """`doctor` is a claude subcommand too. `ccs doctor` is now ours; `ccs --
    doctor` is how you reach the other one."""
    healthy()
    runs = []

    class Done:
        returncode = 0

    monkeypatch.setattr(cli.subprocess, "run",
                        lambda argv, **kw: runs.append(argv) or Done())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["--", "doctor"]) == 0
    assert runs[0][1:] == ["doctor"]


def test_account_rows_are_labelled_by_nickname(monkeypatch):
    """The slug names a directory; the nickname is what the user calls the
    account. `label.display_name` falls back to the email, never to nothing —
    an unlabelled row would be worse than a slug (user feedback)."""
    reg = healthy()
    reg["accounts"][0]["nickname"] = "vsed2"
    registry.save(reg)
    labels = [c.label for c in doctor.run(registry.load())]
    assert "vsed2: symlinks resolve" in labels
    assert not any(l.startswith("work:") for l in labels)

    reg = registry.load()
    reg["accounts"][0]["nickname"] = None
    registry.save(reg)
    labels = [c.label for c in doctor.run(registry.load())]
    assert "work@x.com: symlinks resolve" in labels
