import importlib
import time
import pytest

import ccas.paths as paths
import ccas.launch as launch
import ccas.menu as menu
from ccas.history import Session


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    monkeypatch.setenv("CCAS_CLAUDE_BIN", "/usr/bin/true")
    importlib.reload(paths)
    importlib.reload(menu)
    importlib.reload(launch)
    (tmp_path / "accts" / "work").mkdir(parents=True)
    (tmp_path / "proj").mkdir()
    return tmp_path


ACCOUNT = {
    "slug": "work", "nickname": "work", "email": "w@example.com",
    "color": 1, "display": "nickname", "hide_icon": False,
    "warned_invisible": False, "signal": 1,
}


def seed(tmp_path, n=2):
    now = time.time()
    sess = [
        Session(uuid=f"uuid-{i}", cwd=str(tmp_path / "proj"), title=f"T{i}",
                mtime=now - i, path=None)
        for i in range(n)
    ]
    menu.write(ACCOUNT, sess)
    return sess


def test_new_with_explicit_dir(tmp_path):
    workdir, argv = launch.resolve("work", "new", str(tmp_path / "proj"), True, None)
    assert workdir == str(tmp_path / "proj")
    assert argv == ["/usr/bin/true"]


def test_hist_resolves_slot_to_uuid_and_its_own_cwd(tmp_path):
    seed(tmp_path)
    workdir, argv = launch.resolve("work", "hist", "1", True, None)
    assert workdir == str(tmp_path / "proj")
    assert argv == ["/usr/bin/true", "--resume", "uuid-1"]


def test_hist_out_of_range_returns_none(tmp_path):
    seed(tmp_path)
    assert launch.resolve("work", "hist", "99", True, None) is None


def test_hist_with_a_nonnumeric_slot_returns_none(tmp_path):
    seed(tmp_path)
    assert launch.resolve("work", "hist", "banana", True, None) is None
    assert launch.resolve("work", "hist", None, True, None) is None


def test_last_uses_the_newest_snapshot_row(tmp_path):
    seed(tmp_path)
    workdir, argv = launch.resolve("work", "last", None, True, None)
    assert argv == ["/usr/bin/true", "--resume", "uuid-0"]


def test_last_with_no_history_returns_none(tmp_path):
    assert launch.resolve("work", "last", None, True, None) is None


def test_tty_mode_scopes_last_to_the_current_directory(tmp_path):
    seed(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert launch.resolve("work", "last", None, False, str(other)) is None
    workdir, argv = launch.resolve("work", "last", None, False, str(tmp_path / "proj"))
    assert argv == ["/usr/bin/true", "--resume", "uuid-0"]


def test_gui_mode_ignores_cwd_scoping(tmp_path):
    """A Waybar click is global; only the terminal front-end is cwd-scoped."""
    seed(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    _workdir, argv = launch.resolve("work", "last", None, True, str(other))
    assert argv == ["/usr/bin/true", "--resume", "uuid-0"]


def test_search_resolves_the_chosen_label(tmp_path, monkeypatch):
    seed(tmp_path)
    rows = menu.read_tsv("work")
    monkeypatch.setattr(launch.pickers, "choose", lambda *a, **k: rows[1][0])
    workdir, argv = launch.resolve("work", "search", None, True, None)
    assert argv == ["/usr/bin/true", "--resume", "uuid-1"]
    assert workdir == str(tmp_path / "proj")


def test_search_cancelled_returns_none(tmp_path, monkeypatch):
    seed(tmp_path)
    monkeypatch.setattr(launch.pickers, "choose", lambda *a, **k: None)
    assert launch.resolve("work", "search", None, True, None) is None


def test_unknown_mode_raises(tmp_path):
    with pytest.raises(ValueError):
        launch.resolve("work", "sideways", None, True, None)


def test_new_in_a_missing_directory_asks_before_creating(tmp_path, monkeypatch):
    asked = {}
    monkeypatch.setattr(launch.pickers, "confirm_create",
                        lambda path, gui: asked.setdefault("path", path) or True)
    target = tmp_path / "brand" / "new"
    workdir, _argv = launch.resolve("work", "new", str(target), True, None)
    assert workdir == str(target)
    assert asked["path"] == str(target)


def test_new_in_a_missing_directory_is_abandoned_when_declined(tmp_path, monkeypatch):
    monkeypatch.setattr(launch.pickers, "confirm_create", lambda path, gui: False)
    assert launch.resolve("work", "new", str(tmp_path / "nope"), True, None) is None


def test_new_in_an_existing_directory_never_asks(tmp_path, monkeypatch):
    monkeypatch.setattr(launch.pickers, "confirm_create",
                        lambda *a: pytest.fail("existing project needs no confirmation"))
    launch.resolve("work", "new", str(tmp_path / "proj"), True, None)


def test_a_typed_tilde_path_is_expanded_before_the_existence_check(tmp_path, monkeypatch):
    """The picker lists abbreviated paths, so what comes back can start with ~."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(launch.pickers, "choose", lambda *a, **k: "~/proj")
    workdir, _argv = launch.resolve("work", "new", None, True, None)
    assert workdir == str(tmp_path / "proj")


def test_run_creates_the_confirmed_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(launch.accounts, "relink", lambda slug: None)
    monkeypatch.setattr(launch.pickers, "confirm_create", lambda path, gui: True)
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0})())
    target = tmp_path / "brand" / "new"
    assert launch.run("work", "new", str(target), True, None) == 0
    assert target.is_dir()


def test_never_invokes_claude_by_bare_name(tmp_path):
    _, argv = launch.resolve("work", "new", str(tmp_path / "proj"), True, None)
    assert argv[0] != "claude"
    assert argv[0].startswith("/")


DANGER = "--dangerously-skip-permissions"


def test_no_mode_carries_the_danger_flag_by_default(tmp_path, monkeypatch):
    """Opt-in, always: an account that never asked for it must launch clean."""
    seed(tmp_path)
    monkeypatch.setattr(launch.pickers, "choose",
                        lambda prompt, options, gui: options[0])
    for mode, arg in (("new", str(tmp_path / "proj")), ("last", None),
                      ("hist", "0"), ("search", None)):
        _workdir, argv = launch.resolve("work", mode, arg, True, None)
        assert DANGER not in argv, mode


def test_every_mode_carries_the_danger_flag_when_set(tmp_path, monkeypatch):
    """All four launch modes build their own argv, so all four must honour it —
    resuming a session skips permissions exactly like starting one."""
    seed(tmp_path)
    monkeypatch.setattr(launch.pickers, "choose",
                        lambda prompt, options, gui: options[0])
    for mode, arg in (("new", str(tmp_path / "proj")), ("last", None),
                      ("hist", "0"), ("search", None)):
        _workdir, argv = launch.resolve("work", mode, arg, True, None,
                                        dangerous=True)
        assert argv[1] == DANGER, mode
        assert argv[0].startswith("/"), "absolute claude path stays first"


def test_the_danger_flag_precedes_resume(tmp_path):
    """--resume takes a value; the flag must not land between it and its uuid."""
    seed(tmp_path)
    _workdir, argv = launch.resolve("work", "last", None, True, None,
                                    dangerous=True)
    assert argv[1:] == [DANGER, "--resume", "uuid-0"]
