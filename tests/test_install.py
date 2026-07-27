import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_install_script_exists_and_is_executable():
    script = ROOT / "install.sh"
    assert script.exists()
    assert os.stat(script).st_mode & stat.S_IXUSR


def test_uninstall_script_exists_and_is_executable():
    script = ROOT / "uninstall.sh"
    assert script.exists()
    assert os.stat(script).st_mode & stat.S_IXUSR


def test_install_is_syntactically_valid():
    for name in ("install.sh", "uninstall.sh"):
        proc = subprocess.run(["bash", "-n", str(ROOT / name)], capture_output=True)
        assert proc.returncode == 0, proc.stderr


def test_uninstall_never_uses_rm_on_the_accounts_directory():
    text = (ROOT / "uninstall.sh").read_text()
    assert "rm -rf" not in text.replace("rm -rf __pycache__", "")
    assert "claude_trash" in text, "purge must move to trash, not delete"


def test_install_is_idempotent_in_a_sandbox(tmp_path):
    env = dict(os.environ)
    env.update({
        "CCAS_HOME": str(tmp_path / "claude"),
        "CCAS_CLAUDE_JSON": str(tmp_path / "claude.json"),
        "CCAS_ACCOUNTS_ROOT": str(tmp_path / "accts"),
        "CCAS_TRASH": str(tmp_path / "trash"),
        "CCAS_WAYBAR_CONFIG": str(tmp_path / "config.jsonc"),
        "CCAS_WAYBAR_STYLE": str(tmp_path / "style.css"),
        "CCAS_BIN_DIR": str(tmp_path / "bin"),
        "CCAS_SHARE_DIR": str(tmp_path / "share"),
        "CCAS_SYSTEMD_DIR": str(tmp_path / "systemd"),
        "CCAS_SKIP_SYSTEMD": "1",
        "CCAS_SKIP_RELOAD": "1",
    })
    (tmp_path / "claude").mkdir()
    (tmp_path / "config.jsonc").write_text('{\n    "modules-right": ["clock"]\n}\n')

    first = subprocess.run(["bash", str(ROOT / "install.sh")], env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    snapshot = (tmp_path / "config.jsonc").read_text()

    second = subprocess.run(["bash", str(ROOT / "install.sh")], env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert (tmp_path / "config.jsonc").read_text() == snapshot
    assert (tmp_path / "bin" / "ccs").exists()


def test_install_emits_the_placeholder_and_leaves_bare_claude_alone(tmp_path):
    env = dict(os.environ)
    env.update({
        "CCAS_HOME": str(tmp_path / "claude"),
        "CCAS_CLAUDE_JSON": str(tmp_path / "claude.json"),
        "CCAS_ACCOUNTS_ROOT": str(tmp_path / "accts"),
        "CCAS_TRASH": str(tmp_path / "trash"),
        "CCAS_WAYBAR_CONFIG": str(tmp_path / "config.jsonc"),
        "CCAS_WAYBAR_STYLE": str(tmp_path / "style.css"),
        "CCAS_BIN_DIR": str(tmp_path / "bin"),
        "CCAS_SHARE_DIR": str(tmp_path / "share"),
        "CCAS_SYSTEMD_DIR": str(tmp_path / "systemd"),
        "CCAS_SKIP_SYSTEMD": "1",
        "CCAS_SKIP_RELOAD": "1",
    })
    (tmp_path / "claude").mkdir()
    (tmp_path / "config.jsonc").write_text('{\n    "modules-right": ["clock"]\n}\n')
    (tmp_path / "style.css").write_text("#clock { padding: 0 12px; }\n")

    proc = subprocess.run(["bash", str(ROOT / "install.sh")], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "custom/cc-setup" in (tmp_path / "config.jsonc").read_text()
    style = (tmp_path / "style.css").read_text()
    assert "#custom-cc-" not in style, "no accounts, so no widgets to style"
    assert style == "#clock { padding: 0 12px; }\n", "the user's own rules survive"


def test_uninstall_restores_both_files_byte_for_byte(tmp_path):
    env = dict(os.environ)
    env.update({
        "CCAS_HOME": str(tmp_path / "claude"),
        "CCAS_CLAUDE_JSON": str(tmp_path / "claude.json"),
        "CCAS_ACCOUNTS_ROOT": str(tmp_path / "accts"),
        "CCAS_TRASH": str(tmp_path / "trash"),
        "CCAS_WAYBAR_CONFIG": str(tmp_path / "config.jsonc"),
        "CCAS_WAYBAR_STYLE": str(tmp_path / "style.css"),
        "CCAS_BIN_DIR": str(tmp_path / "bin"),
        "CCAS_SHARE_DIR": str(tmp_path / "share"),
        "CCAS_SYSTEMD_DIR": str(tmp_path / "systemd"),
        "CCAS_SKIP_SYSTEMD": "1",
        "CCAS_SKIP_RELOAD": "1",
        "CCAS_NO_RELOAD": "1",
    })
    (tmp_path / "claude").mkdir()
    original_cfg = '{\n    "modules-right": ["clock"]\n}\n'
    original_style = "#clock { padding: 0 12px; }\n"
    (tmp_path / "config.jsonc").write_text(original_cfg)
    (tmp_path / "style.css").write_text(original_style)

    subprocess.run(["bash", str(ROOT / "install.sh")], env=env, capture_output=True, text=True, check=True)
    proc = subprocess.run(["bash", str(ROOT / "uninstall.sh")], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    assert (tmp_path / "config.jsonc").read_text() == original_cfg
    assert (tmp_path / "style.css").read_text() == original_style
    assert not (tmp_path / "bin" / "ccs").exists()


def test_uninstall_purge_moves_accounts_to_trash_intact(tmp_path):
    env = dict(os.environ)
    env.update({
        "CCAS_HOME": str(tmp_path / "claude"),
        "CCAS_CLAUDE_JSON": str(tmp_path / "claude.json"),
        "CCAS_ACCOUNTS_ROOT": str(tmp_path / "accts"),
        "CCAS_TRASH": str(tmp_path / "trash"),
        "CCAS_WAYBAR_CONFIG": str(tmp_path / "config.jsonc"),
        "CCAS_WAYBAR_STYLE": str(tmp_path / "style.css"),
        "CCAS_BIN_DIR": str(tmp_path / "bin"),
        "CCAS_SHARE_DIR": str(tmp_path / "share"),
        "CCAS_SYSTEMD_DIR": str(tmp_path / "systemd"),
        "CCAS_SKIP_SYSTEMD": "1",
        "CCAS_SKIP_RELOAD": "1",
        "CCAS_NO_RELOAD": "1",
    })
    (tmp_path / "claude").mkdir()
    (tmp_path / "config.jsonc").write_text('{\n    "modules-right": ["clock"]\n}\n')
    (tmp_path / "accts").mkdir()
    (tmp_path / "accts" / "precious.json").write_text("do not lose me")

    subprocess.run(["bash", str(ROOT / "install.sh")], env=env, capture_output=True, text=True, check=True)
    subprocess.run(["bash", str(ROOT / "uninstall.sh"), "--purge"], env=env,
                   capture_output=True, text=True, check=True)

    assert not (tmp_path / "accts").exists()
    survivors = list((tmp_path / "trash").rglob("precious.json"))
    assert survivors, "account data must be moved to trash, never deleted"
    assert survivors[0].read_text() == "do not lose me"


def _sandbox_env(tmp_path):
    env = dict(os.environ)
    env.update({
        "CCAS_HOME": str(tmp_path / "claude"),
        "CCAS_CLAUDE_JSON": str(tmp_path / "claude.json"),
        "CCAS_ACCOUNTS_ROOT": str(tmp_path / "accts"),
        "CCAS_TRASH": str(tmp_path / "trash"),
        "CCAS_WAYBAR_CONFIG": str(tmp_path / "config.jsonc"),
        "CCAS_WAYBAR_STYLE": str(tmp_path / "style.css"),
        "CCAS_BIN_DIR": str(tmp_path / "bin"),
        "CCAS_SHARE_DIR": str(tmp_path / "share"),
        "CCAS_MENU_CSS": str(tmp_path / "menu.css"),
        # Both, always: without them install.sh writes units into the real
        # ~/.config/systemd/user and `systemctl --user enable --now` starts a
        # timer against the user's real accounts. A test may not do that.
        "CCAS_SYSTEMD_DIR": str(tmp_path / "systemd"),
        "CCAS_SKIP_SYSTEMD": "1",
        "CCAS_SKIP_RELOAD": "1",
    })
    (tmp_path / "claude").mkdir()
    (tmp_path / "config.jsonc").write_text('{\n    "modules-right": ["clock"]\n}\n')
    return env


def test_install_ships_the_panel_stylesheet(tmp_path):
    """The panel opens without it, unstyled. Shipping it once is what makes it
    a thing the user can edit rather than a thing they have to write."""
    env = _sandbox_env(tmp_path)
    subprocess.run(["bash", str(ROOT / "install.sh")], env=env, check=True,
                   capture_output=True, text=True)
    assert (tmp_path / "menu.css").read_text() == \
        (ROOT / "assets" / "menu.css").read_text()


def test_install_never_overwrites_an_edited_stylesheet(tmp_path):
    """The same stance CCAS takes toward style.css: it is the user's the moment
    it exists. A reinstall that reverted their colours would be a deletion."""
    env = _sandbox_env(tmp_path)
    (tmp_path / "menu.css").write_text("/* mine */\n")
    subprocess.run(["bash", str(ROOT / "install.sh")], env=env, check=True,
                   capture_output=True, text=True)
    assert (tmp_path / "menu.css").read_text() == "/* mine */\n"


def test_install_writes_the_poll_units(tmp_path):
    """The timer is how the user controls the cadence — systemctl, not a CCAS
    setting. It must land installed, with the absolute ccs path baked in."""
    env = _sandbox_env(tmp_path)

    proc = subprocess.run(["bash", str(ROOT / "install.sh")], env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    service = (tmp_path / "systemd" / "ccas-poll.service").read_text()
    timer = (tmp_path / "systemd" / "ccas-poll.timer").read_text()
    assert f"ExecStart={tmp_path}/bin/ccs poll" in service
    assert "@CCS@" not in service, "the placeholder must be substituted"
    assert "OnUnitActiveSec=5min" in timer
    assert "WantedBy=timers.target" in timer


def test_reinstall_trashes_the_previous_unit_rather_than_overwriting(tmp_path):
    """Never delete — the global rule, and these are files a user may have
    edited to retime the poll."""
    env = _sandbox_env(tmp_path)
    (tmp_path / "systemd").mkdir()
    (tmp_path / "systemd" / "ccas-poll.timer").write_text("# mine\n")

    subprocess.run(["bash", str(ROOT / "install.sh")], env=env, check=True,
                   capture_output=True)
    trashed = list((tmp_path / "trash").glob("ccas-poll.timer-*"))
    assert trashed and trashed[0].read_text() == "# mine\n"


def test_uninstall_trashes_the_units(tmp_path):
    env = _sandbox_env(tmp_path)
    env["CCAS_NO_RELOAD"] = "1"
    subprocess.run(["bash", str(ROOT / "install.sh")], env=env, check=True,
                   capture_output=True)
    subprocess.run(["bash", str(ROOT / "uninstall.sh")], env=env, check=True,
                   capture_output=True)
    assert not (tmp_path / "systemd" / "ccas-poll.timer").exists()
    assert list((tmp_path / "trash").glob("ccas-poll.timer-*"))


def test_an_unchanged_reinstall_leaves_no_trash(tmp_path):
    """Ten installs in a day left 99 entries in the trash, all of them copies of
    a package that had not changed. Nothing is deleted to fix that — the staged
    copy is only trashed when it actually differs from the one replacing it."""
    env = dict(os.environ)
    env.update({
        "CCAS_HOME": str(tmp_path / "claude"),
        "CCAS_CLAUDE_JSON": str(tmp_path / "claude.json"),
        "CCAS_ACCOUNTS_ROOT": str(tmp_path / "accts"),
        "CCAS_TRASH": str(tmp_path / "trash"),
        "CCAS_WAYBAR_CONFIG": str(tmp_path / "config.jsonc"),
        "CCAS_WAYBAR_STYLE": str(tmp_path / "style.css"),
        "CCAS_BIN_DIR": str(tmp_path / "bin"),
        "CCAS_SHARE_DIR": str(tmp_path / "share"),
        "CCAS_SYSTEMD_DIR": str(tmp_path / "systemd"),
        "CCAS_SKIP_SYSTEMD": "1",
        "CCAS_SKIP_RELOAD": "1",
    })
    (tmp_path / "claude").mkdir()
    (tmp_path / "config.jsonc").write_text('{\n    "modules-right": ["clock"]\n}\n')
    trash = tmp_path / "trash"

    for _ in range(3):
        subprocess.run(["bash", str(ROOT / "install.sh")], env=env,
                       capture_output=True, text=True, check=True)
    assert sorted(p.name for p in trash.iterdir()) == [], \
        "an install that changes nothing must leave nothing behind"

    # A real change still gets its predecessor kept, exactly as before.
    (tmp_path / "share" / "ccas" / "paths.py").write_text("# an older build\n")
    (tmp_path / "systemd" / "ccas-poll.timer").write_text("# retimed by the user\n")
    subprocess.run(["bash", str(ROOT / "install.sh")], env=env,
                   capture_output=True, text=True, check=True)
    kept = sorted(p.name.split("-2026")[0] for p in trash.iterdir())
    assert kept == ["ccas-pkg", "ccas-poll.timer"], kept
