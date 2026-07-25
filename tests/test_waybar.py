import importlib
import json
import pytest

import ccas.paths as paths
import ccas.waybar as waybar

ORIGINAL = """{
    "height": 26,
    "modules-left": ["custom/launcher"],
    "modules-right": ["custom/stopwatch", "clock"],
    // a comment the user wrote
    "clock": {
        "format": "{:%H:%M}"
    }
}
"""


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    cfg = tmp_path / "config.jsonc"
    cfg.write_text(ORIGINAL, encoding="utf-8")
    monkeypatch.setenv("CCAS_WAYBAR_CONFIG", str(cfg))
    monkeypatch.setenv("CCAS_BASHRC", str(tmp_path / "bashrc"))
    monkeypatch.setenv("CCAS_CCS_BIN", "/home/fixed/.local/bin/ccs")
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    importlib.reload(paths)
    importlib.reload(waybar)
    return cfg


def reg(*slugs):
    return {
        "default": slugs[0] if slugs else None,
        "accounts": [
            {"slug": s, "nickname": s, "email": f"{s}@x.com", "color": i,
             "display": "nickname", "hide_icon": False,
             "warned_invisible": False, "signal": i + 1}
            for i, s in enumerate(slugs)
        ],
    }


def test_module_uses_absolute_paths_everywhere():
    mod = waybar.module_config(reg("work")["accounts"][0])
    assert mod["exec"].startswith("/home/fixed/.local/bin/ccs")
    assert mod["on-click"].startswith("/home/fixed/.local/bin/ccs")


def test_module_carries_signal_and_the_click():
    mod = waybar.module_config(reg("work")["accounts"][0])
    assert mod["signal"] == 1
    assert mod["interval"] == 30
    assert mod["on-click"].endswith("--gui work")


def test_the_module_declares_no_cached_menu():
    """Waybar parses menu-file once when the module is built and only SIGUSR2
    re-parses it, so a cached menu is a menu that has to be invalidated by
    rebuilding the whole bar. The click opens the picker instead, which is
    generated per click and cannot go stale."""
    mod = waybar.module_config(reg("work")["accounts"][0])
    assert "menu" not in mod
    assert "menu-file" not in mod
    assert "menu-actions" not in mod


def test_apply_inserts_block_and_registers_modules(_isolate):
    waybar.apply(reg("personal", "work"))
    text = _isolate.read_text()
    assert "CCAS — managed block, start" in text
    assert '"custom/cc-personal"' in text
    assert "custom/cc-personal" in text.split(waybar.HOST_LIST)[1].split("]")[0]
    assert "// a comment the user wrote" in text, "user content must survive"


def test_apply_is_idempotent(_isolate):
    waybar.apply(reg("work"))
    once = _isolate.read_text()
    waybar.apply(reg("work"))
    assert _isolate.read_text() == once


def test_apply_replaces_rather_than_appends_on_change(_isolate):
    waybar.apply(reg("a", "b"))
    waybar.apply(reg("a"))
    text = _isolate.read_text()
    assert text.count("CCAS — managed block, start") == 1
    assert '"custom/cc-b"' not in text
    assert "custom/cc-b" not in text.split(waybar.HOST_LIST)[1].split("]")[0]


def test_empty_registry_emits_the_placeholder(_isolate):
    waybar.apply(reg())
    text = _isolate.read_text()
    assert '"custom/cc-setup"' in text
    assert "ccs --gui manage add" in text


def test_placeholder_disappears_once_an_account_exists(_isolate):
    waybar.apply(reg())
    waybar.apply(reg("work"))
    assert '"custom/cc-setup"' not in _isolate.read_text()


def test_strip_restores_the_original_file(_isolate):
    waybar.apply(reg("work"))
    waybar.strip(paths.waybar_config())
    text = _isolate.read_text()
    assert "CCAS" not in text
    assert "custom/cc-work" not in text
    assert "// a comment the user wrote" in text
    assert json.loads("".join(
        l for l in text.splitlines(keepends=True) if not l.strip().startswith("//")
    ))["modules-right"] == ["custom/stopwatch", "clock"]


def test_generated_block_is_valid_json_once_comments_are_stripped(_isolate):
    """Waybar must still be able to parse the file we just wrote."""
    waybar.apply(reg("personal", "work"))
    text = _isolate.read_text()
    bare = "".join(
        l for l in text.splitlines(keepends=True) if not l.strip().startswith("//")
    )
    data = json.loads(bare)
    assert data["custom/cc-personal"]["signal"] == 1
    assert data["custom/cc-work"]["signal"] == 2
    assert data[waybar.HOST_LIST] == ["custom/cc-personal", "custom/cc-work"]
    assert data["modules-right"] == ["custom/stopwatch", "clock"], "must not sit at the edge"
    assert data["modules-left"] == ["custom/launcher"], "user's own groups untouched"
    assert not any(k.startswith("custom/cc-sep") for k in data), "no divider module"


def test_strip_bashrc_removes_a_legacy_claude_function():
    """CCAS used to shadow `claude` with a shell function. `ccs -p` does the same
    job without owning a binary the user did not offer, so an install now takes
    the old block back out."""
    legacy = (f"# {waybar.RULE}\n# {waybar.START}\nclaude() {{ :; }}\n"
              f"# {waybar.END}\n# {waybar.RULE}\n")
    paths.bashrc().write_text("export EDITOR=vim\n" + legacy, encoding="utf-8")
    waybar.strip_bashrc()
    text = paths.bashrc().read_text()
    assert "claude()" not in text
    assert text == "export EDITOR=vim\n", "the user's own bashrc must survive"


def test_strip_bashrc_leaves_an_unmanaged_bashrc_alone():
    """Nothing of ours in there means nothing to do — no rewrite, and no
    .ccas-orig backup of a file we never touched."""
    paths.bashrc().write_text("export EDITOR=vim\n", encoding="utf-8")
    before = paths.bashrc().stat().st_mtime_ns
    waybar.strip_bashrc()
    assert paths.bashrc().stat().st_mtime_ns == before
    assert not list(paths.bashrc().parent.glob("*.ccas-orig"))


def test_strip_bashrc_does_not_create_a_missing_bashrc():
    assert not paths.bashrc().exists()
    waybar.strip_bashrc()
    assert not paths.bashrc().exists()


def test_reload_and_signal_are_suppressed_by_the_sandbox_flag(monkeypatch):
    monkeypatch.setenv("CCAS_NO_RELOAD", "1")
    called = []
    monkeypatch.setattr(waybar.subprocess, "run", lambda *a, **k: called.append(a))
    waybar.reload()
    waybar.signal(3)
    assert called == []


WITH_CENTER = """{
    "modules-left": ["custom/launcher", "sway/workspaces"],
    "modules-center": ["sway/mode"],
    "modules-right": ["clock"]
}
"""


def test_modules_are_added_to_an_existing_centre_group(_isolate):
    _isolate.write_text(WITH_CENTER, encoding="utf-8")
    waybar.apply(reg("work"))
    data = json.loads("".join(
        l for l in _isolate.read_text().splitlines(keepends=True)
        if not l.strip().startswith("//")
    ))
    assert data["modules-center"] == ["sway/mode", "custom/cc-work"]


def test_centre_group_is_created_when_the_config_lacks_one(_isolate):
    """The fixture config has no modules-center; the modules must still land."""
    waybar.apply(reg("work"))
    data = json.loads("".join(
        l for l in _isolate.read_text().splitlines(keepends=True)
        if not l.strip().startswith("//")
    ))
    assert data["modules-center"] == ["custom/cc-work"]


def test_strip_removes_a_centre_group_it_created(_isolate):
    before = _isolate.read_text()
    waybar.apply(reg("work"))
    waybar.strip(paths.waybar_config())
    assert _isolate.read_text() == before


def test_strip_preserves_a_centre_group_the_user_had(_isolate):
    _isolate.write_text(WITH_CENTER, encoding="utf-8")
    before = _isolate.read_text()
    waybar.apply(reg("work"))
    waybar.strip(paths.waybar_config())
    assert _isolate.read_text() == before


def test_stale_entries_from_an_earlier_layout_are_cleared(_isolate):
    _isolate.write_text(
        '{\n    "modules-left": ["custom/launcher", "custom/cc-sep", "custom/cc-old"],\n'
        '    "modules-right": ["clock", "custom/cc-setup"]\n}\n', encoding="utf-8")
    waybar.apply(reg("work"))
    data = json.loads("".join(
        l for l in _isolate.read_text().splitlines(keepends=True)
        if not l.strip().startswith("//")
    ))
    assert data["modules-left"] == ["custom/launcher"]
    assert data["modules-right"] == ["clock"]
    assert data["modules-center"] == ["custom/cc-work"]


def test_generated_commands_all_force_gui_mode():
    """--gui belongs on every waybar-generated command and nowhere else; the
    terminal entry point (`ccs -p …`) must be free to pick fzf. Pinned because
    the mistake was made once already, back when a bashrc function was the
    terminal path — see test_cli's passthrough gui test for the other half."""
    account = {"slug": "work", "signal": 1, "display": "index",
               "color": 0, "hide_icon": False}
    mod = waybar.module_config(account)
    assert "--gui" in mod["exec"]
    assert "--gui" in mod["on-click"]
    assert "--gui" in waybar.placeholder_config()["on-click"]
