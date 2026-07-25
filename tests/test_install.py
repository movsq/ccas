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
        "CCAS_BASHRC": str(tmp_path / "bashrc"),
        "CCAS_BIN_DIR": str(tmp_path / "bin"),
        "CCAS_SHARE_DIR": str(tmp_path / "share"),
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


def test_install_emits_the_placeholder_and_the_bashrc_function(tmp_path):
    env = dict(os.environ)
    env.update({
        "CCAS_HOME": str(tmp_path / "claude"),
        "CCAS_CLAUDE_JSON": str(tmp_path / "claude.json"),
        "CCAS_ACCOUNTS_ROOT": str(tmp_path / "accts"),
        "CCAS_TRASH": str(tmp_path / "trash"),
        "CCAS_WAYBAR_CONFIG": str(tmp_path / "config.jsonc"),
        "CCAS_BASHRC": str(tmp_path / "bashrc"),
        "CCAS_BIN_DIR": str(tmp_path / "bin"),
        "CCAS_SHARE_DIR": str(tmp_path / "share"),
        "CCAS_SKIP_RELOAD": "1",
    })
    (tmp_path / "claude").mkdir()
    (tmp_path / "config.jsonc").write_text('{\n    "modules-right": ["clock"]\n}\n')
    (tmp_path / "bashrc").write_text("export EDITOR=vim\n")

    proc = subprocess.run(["bash", str(ROOT / "install.sh")], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "custom/cc-setup" in (tmp_path / "config.jsonc").read_text()
    bashrc = (tmp_path / "bashrc").read_text()
    assert "claude()" in bashrc
    assert "export EDITOR=vim" in bashrc, "the user's own bashrc must survive"


def test_uninstall_restores_both_files_byte_for_byte(tmp_path):
    env = dict(os.environ)
    env.update({
        "CCAS_HOME": str(tmp_path / "claude"),
        "CCAS_CLAUDE_JSON": str(tmp_path / "claude.json"),
        "CCAS_ACCOUNTS_ROOT": str(tmp_path / "accts"),
        "CCAS_TRASH": str(tmp_path / "trash"),
        "CCAS_WAYBAR_CONFIG": str(tmp_path / "config.jsonc"),
        "CCAS_BASHRC": str(tmp_path / "bashrc"),
        "CCAS_BIN_DIR": str(tmp_path / "bin"),
        "CCAS_SHARE_DIR": str(tmp_path / "share"),
        "CCAS_SKIP_RELOAD": "1",
        "CCAS_NO_RELOAD": "1",
    })
    (tmp_path / "claude").mkdir()
    original_cfg = '{\n    "modules-right": ["clock"]\n}\n'
    original_rc = "export EDITOR=vim\n"
    (tmp_path / "config.jsonc").write_text(original_cfg)
    (tmp_path / "bashrc").write_text(original_rc)

    subprocess.run(["bash", str(ROOT / "install.sh")], env=env, capture_output=True, text=True, check=True)
    proc = subprocess.run(["bash", str(ROOT / "uninstall.sh")], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    assert (tmp_path / "config.jsonc").read_text() == original_cfg
    assert (tmp_path / "bashrc").read_text() == original_rc
    assert not (tmp_path / "bin" / "ccs").exists()


def test_uninstall_purge_moves_accounts_to_trash_intact(tmp_path):
    env = dict(os.environ)
    env.update({
        "CCAS_HOME": str(tmp_path / "claude"),
        "CCAS_CLAUDE_JSON": str(tmp_path / "claude.json"),
        "CCAS_ACCOUNTS_ROOT": str(tmp_path / "accts"),
        "CCAS_TRASH": str(tmp_path / "trash"),
        "CCAS_WAYBAR_CONFIG": str(tmp_path / "config.jsonc"),
        "CCAS_BASHRC": str(tmp_path / "bashrc"),
        "CCAS_BIN_DIR": str(tmp_path / "bin"),
        "CCAS_SHARE_DIR": str(tmp_path / "share"),
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
