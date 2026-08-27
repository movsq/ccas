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
    monkeypatch.setenv("CCAS_WAYBAR_STYLE", str(tmp_path / "style.css"))
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
             "hide_icon": False,
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
    account = {"slug": "work", "signal": 1,
               "color": "#fab387", "hide_icon": False}
    mod = waybar.module_config(account)
    assert "--gui" in mod["exec"]
    assert "--gui" in mod["on-click"]
    assert "--gui" in waybar.placeholder_config()["on-click"]


# ── the stylesheet's managed block ───────────────────────────────────────────


def test_style_block_names_every_slug_for_spacing_and_hover():
    """GTK CSS has no prefix matching, so #custom-cc-* is not a selector: every
    account has to be spelled out. That is why this is generated rather than
    hand-kept — a slug that was renamed, or an account added, silently lost its
    hover and the widget stopped answering the pointer."""
    waybar.apply_style(reg("work", "home"))
    text = paths.waybar_style().read_text(encoding="utf-8")
    assert "#custom-cc-work,\n#custom-cc-home {" in text
    assert "#custom-cc-work:hover,\n#custom-cc-home:hover {" in text


def test_style_block_goes_last_so_it_outranks_what_the_user_wrote():
    """Equal specificity is decided by order, and the block exists to be the
    answer for the widgets — a stale hand-written rule above it must lose."""
    style = paths.waybar_style()
    style.write_text("#clock { padding: 0 12px; }\n", encoding="utf-8")
    waybar.apply_style(reg("work"))
    text = style.read_text(encoding="utf-8")
    assert text.startswith("#clock { padding: 0 12px; }")
    assert text.rstrip().endswith("*/")


def test_style_block_is_replaced_not_appended_twice():
    waybar.apply_style(reg("work"))
    waybar.apply_style(reg("home"))
    text = paths.waybar_style().read_text(encoding="utf-8")
    assert text.count(waybar.START) == 1
    assert "#custom-cc-work" not in text


def test_style_keeps_one_backup_of_what_the_user_had():
    style = paths.waybar_style()
    style.write_text("#clock { padding: 0 12px; }\n", encoding="utf-8")
    waybar.apply_style(reg("work"))
    waybar.apply_style(reg("work", "home"))
    backup = style.with_suffix(style.suffix + ".ccas-orig")
    assert backup.read_text(encoding="utf-8") == "#clock { padding: 0 12px; }\n"


def test_style_with_no_accounts_leaves_no_block():
    style = paths.waybar_style()
    style.write_text("#clock { padding: 0 12px; }\n", encoding="utf-8")
    waybar.apply_style(reg("work"))
    waybar.apply_style(reg())
    assert style.read_text(encoding="utf-8") == "#clock { padding: 0 12px; }\n"


def test_apply_writes_both_files():
    """One call, both halves: the module list and the rules that make those
    modules look and behave like the others. They drifted apart once already."""
    waybar.apply(reg("work"))
    assert "custom/cc-work" in paths.waybar_config().read_text(encoding="utf-8")
    assert "#custom-cc-work" in paths.waybar_style().read_text(encoding="utf-8")


def test_strip_takes_the_style_block_out_too():
    style = paths.waybar_style()
    style.write_text("#clock { padding: 0 12px; }\n", encoding="utf-8")
    waybar.apply(reg("work"))
    waybar.strip(style, "/*", "*/")
    assert style.read_text(encoding="utf-8") == "#clock { padding: 0 12px; }\n"


# ── hidden accounts ──────────────────────────────────────────────────────────
#
# "Off the bar" is a different thing from "removed", and the registry is the
# only place that can hold the difference: the account still resolves, still
# runs and still shows in the panel — it just has no widget.


def hide(reg_dict: dict, *slugs: str) -> dict:
    for account in reg_dict["accounts"]:
        if account["slug"] in slugs:
            account["hidden"] = True
    return reg_dict


def bare(text: str) -> dict:
    return json.loads("".join(
        l for l in text.splitlines(keepends=True) if not l.strip().startswith("//")
    ))


def test_a_hidden_account_gets_no_module(_isolate):
    waybar.apply(hide(reg("work", "home"), "home"))
    data = bare(_isolate.read_text())
    assert "custom/cc-home" not in data
    assert data[waybar.HOST_LIST] == ["custom/cc-work"]


def test_a_hidden_account_gets_no_style_rule(_isolate):
    """The rules name the widgets that exist. A selector for a module Waybar
    was never told to build is dead text in the user's stylesheet."""
    waybar.apply(hide(reg("work", "home"), "home"))
    text = paths.waybar_style().read_text(encoding="utf-8")
    assert "#custom-cc-home" not in text
    assert "#custom-cc-work:hover" in text


def test_unhiding_puts_the_module_back(_isolate):
    waybar.apply(hide(reg("work", "home"), "home"))
    waybar.apply(reg("work", "home"))
    data = bare(_isolate.read_text())
    assert data[waybar.HOST_LIST] == ["custom/cc-work", "custom/cc-home"]


def test_hiding_every_account_empties_the_bar_rather_than_offering_to_add_one(_isolate):
    """The placeholder says "no accounts configured — click to add one", which
    on a registry holding two accounts is a lie, and its click opens a login
    the user did not ask for. Nothing is the honest answer to "none of them"."""
    waybar.apply(hide(reg("work", "home"), "work", "home"))
    text = _isolate.read_text()
    assert "custom/cc-setup" not in text
    data = bare(text)
    assert not [k for k in data if k.startswith("custom/cc-")]
    assert waybar.HOST_LIST not in data
    assert "#custom-cc-" not in paths.waybar_style().read_text(encoding="utf-8")
