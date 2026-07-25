import importlib
import time
import xml.etree.ElementTree as ET
import pytest

import ccas.paths as paths
import ccas.menu as menu
from ccas.history import Session


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("CCAS_ACCOUNTS_ROOT", str(tmp_path / "accts"))
    importlib.reload(paths)
    importlib.reload(menu)
    (tmp_path / "accts" / "work").mkdir(parents=True)


def account(**kw):
    base = {
        "slug": "work", "nickname": "work", "email": "w@example.com",
        "color": 1, "display": "nickname", "hide_icon": False,
        "warned_invisible": False, "signal": 1,
    }
    base.update(kw)
    return base


def sessions(n=3):
    now = time.time()
    return [
        Session(uuid=f"uuid-{i}", cwd="/home/x/4s", title=f"Title {i}",
                mtime=now - i * 60, path=None)
        for i in range(n)
    ]


def test_xml_is_wellformed_with_a_gtkmenu_root_id():
    root = ET.fromstring(menu.build_xml(account(), sessions()))
    obj = root.find("object")
    assert obj.get("class") == "GtkMenu" and obj.get("id") == "menu"


def test_xml_contains_the_fixed_action_ids():
    xml = menu.build_xml(account(), sessions())
    for wanted in ("new", "last", "search", "hide",
                   "disp-nickname", "disp-index", "disp-cc", "disp-icon",
                   "color-0", "color-7", "mng-add", "mng-rename", "mng-remove"):
        assert f'id="{wanted}"' in xml, wanted


def test_history_items_are_numbered_from_zero():
    xml = menu.build_xml(account(), sessions(3))
    assert 'id="hist-0"' in xml and 'id="hist-2"' in xml
    assert 'id="hist-3"' not in xml


def test_menu_shows_only_a_short_recent_slice():
    """A GtkMenu does not scroll usefully: 219 items filled a 1440px screen."""
    xml = menu.build_xml(account(), sessions(paths.HIST_SLOTS + 50))
    assert f'id="hist-{paths.MENU_HIST_ITEMS - 1}"' in xml
    assert f'id="hist-{paths.MENU_HIST_ITEMS}"' not in xml
    assert paths.MENU_HIST_ITEMS < paths.HIST_SLOTS


def test_overflow_row_offers_the_full_set_and_counts_the_remainder():
    xml = menu.build_xml(account(), sessions(paths.MENU_HIST_ITEMS + 7))
    assert 'id="search-more"' in xml
    assert "7 more" in xml


def test_no_overflow_row_when_everything_fits():
    assert 'id="search-more"' not in menu.build_xml(account(), sessions(3))


def test_tsv_still_holds_the_full_slot_count():
    """The menu is short, but hist-N actions and search index the whole file."""
    rows = menu.build_tsv(sessions(paths.HIST_SLOTS + 50)).strip().split("\n")
    assert len(rows) == paths.HIST_SLOTS


def test_shown_rows_map_to_the_matching_tsv_index():
    sess = sessions(paths.MENU_HIST_ITEMS + 20)
    menu.write(account(), sess)
    xml = (paths.account_dir("work") / "menu.xml").read_text()
    rows = menu.read_tsv("work")
    last = paths.MENU_HIST_ITEMS - 1
    assert rows[last][1] == f"uuid-{last}"
    assert f'id="hist-{last}"' in xml


def test_the_email_heads_the_menu_and_the_nickname_sits_under_it():
    """The email is what identifies the account; the nickname is only a label
    for the bar, so it goes second and only when there is one."""
    xml = menu.build_xml(account(), [])
    assert xml.index(">w@example.com<") < xml.index(">work<")


def test_no_nickname_row_when_unset():
    xml = menu.build_xml(account(nickname=None), [])
    assert ">w@example.com<" in xml
    assert 'id="title-nickname"' not in xml
    assert 'id="title-nickname"' in menu.build_xml(account(), [])


def test_both_title_rows_are_unclickable():
    xml = menu.build_xml(account(), [])
    root = ET.fromstring(xml)
    for item_id in ("title", "title-nickname"):
        obj = root.find(f".//object[@id='{item_id}']")
        sensitive = obj.find("property[@name='sensitive']")
        assert sensitive is not None and sensitive.text == "False", item_id


def test_display_mode_state_is_rendered_into_labels():
    xml = menu.build_xml(account(display="index"), [])
    assert "● index" in xml
    assert "○ nickname" in xml


def test_hide_icon_state_uses_the_same_marks_as_every_other_row():
    """☑ was invisible on the real bar. Waybar's font stack starts with
    FontAwesome, which covers U+2611 but not U+2610 — so the checked box was
    drawn by FontAwesome and the unchecked one by DejaVu, at different sizes,
    and the 'on' state read as an empty box. Only use marks that survive that
    stack: ●/○ were verified against it with pango-view."""
    assert f"{menu.MARK_OFF} Hide icon" in menu.build_xml(account(hide_icon=False), [])
    assert f"{menu.MARK_ON} Hide icon" in menu.build_xml(account(hide_icon=True), [])
    for xml in (menu.build_xml(account(hide_icon=True), []),
                menu.build_xml(account(hide_icon=False), [])):
        assert "☑" not in xml and "☐" not in xml


def test_labels_are_xml_escaped():
    s = sessions(1)
    s[0].title = "fix <tag> & thing"
    xml = menu.build_xml(account(), s)
    assert "&lt;tag&gt; &amp; thing" in xml
    ET.fromstring(xml)


def test_nested_submenus_are_emitted():
    """The Task 0 spike confirmed Waybar renders nested GtkMenu children."""
    xml = menu.build_xml(account(), sessions(1))
    assert '<child type="submenu">' in xml
    root = ET.fromstring(xml)
    nested = root.findall(".//child[@type='submenu']/object[@class='GtkMenu']")
    assert len(nested) == 4, "history, display, color, manage"


def test_tsv_pairs_labels_with_uuid_and_cwd():
    rows = menu.build_tsv(sessions(2)).strip().split("\n")
    assert len(rows) == 2
    label, uuid, cwd = rows[0].split("\t")
    assert uuid == "uuid-0" and cwd == "/home/x/4s" and "Title 0" in label


def test_write_then_read_tsv_round_trips_and_indices_align():
    acct, sess = account(), sessions(3)
    menu.write(acct, sess)
    rows = menu.read_tsv("work")
    assert rows[1][1] == "uuid-1"
    assert 'id="hist-1"' in (paths.account_dir("work") / "menu.xml").read_text()


def test_write_leaves_no_temp_files():
    menu.write(account(), sessions())
    names = sorted(p.name for p in paths.account_dir("work").iterdir())
    assert names == ["history.tsv", "menu.xml"]


def test_session_set_changed_detects_additions():
    acct = account()
    sess = sessions(2)
    menu.write(acct, sess)
    assert menu.session_set_changed("work", sess) is False
    assert menu.session_set_changed("work", sessions(3)) is True


def test_session_set_changed_detects_a_new_newest_session():
    """Same count, different newest uuid — a resumed-then-new-session case."""
    menu.write(account(), sessions(2))
    fresh = sessions(2)
    fresh[0].uuid = "uuid-brand-new"
    assert menu.session_set_changed("work", fresh) is True


def test_reordering_the_same_sessions_is_not_a_change():
    """Two live Claude sessions take turns being the newest, and comparing only
    the newest uuid made every hand-over a full SIGUSR2 reload — the bar visibly
    resetting itself every few minutes with nothing new to show."""
    menu.write(account(), sessions(3))
    shuffled = sessions(3)
    shuffled.append(shuffled.pop(0))
    assert menu.session_set_changed("work", shuffled) is False


def test_session_set_changed_is_true_when_no_snapshot_exists():
    assert menu.session_set_changed("work", sessions(2)) is True


def test_no_snapshot_and_no_sessions_still_counts_as_changed():
    """An account with no history at all must still get a menu.xml written —
    'no rows on disk' and 'no file on disk' are not the same state."""
    assert menu.session_set_changed("work", []) is True
    menu.write(account(), [])
    assert menu.session_set_changed("work", []) is False
