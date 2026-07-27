"""The accounts.json registry."""
import json
import os
import re
import tempfile
import unicodedata

# No cycle: format imports label and paths, neither of which imports registry.
from . import format, paths

# What DEFAULT_FORMAT was before the display modes were retired. A stored format
# still equal to it was never chosen by anyone — it is what add() wrote — so the
# migration may replace it. Anything else the user typed, and it survives.
OLD_DEFAULT_FORMAT = "%icon %name %5h"


def slugify(text: str) -> str:
    text = text.split("@", 1)[0]
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "account"


def load() -> dict:
    path = paths.registry_file()
    if not path.exists():
        return {"default": None, "accounts": []}
    with open(path, encoding="utf-8") as fh:
        reg = json.load(fh)
    reg.setdefault("default", None)
    reg.setdefault("accounts", [])
    for account in reg["accounts"]:
        # Added after the first release; every read path assumes it is present.
        account.setdefault("headless", False)
        account.setdefault("dangerous", False)
        # Written as an index before colours became continuous. Normalised on
        # read so no pass over accounts.json is needed at install — rewriting a
        # whole registry to fix a field is the blind overwrite that "read the
        # live state before you write it" exists to prevent.
        if isinstance(account.get("color"), int):
            index = account["color"]
            account["color"] = paths.PALETTE[index][1] \
                if 0 <= index < len(paths.PALETTE) else paths.PALETTE[0][1]
        # The mode is retired. An account that was not on "custom" had its
        # label built by the mode, so its stored format is whatever add() wrote
        # and is replaced; one the user typed is kept. The key is dropped either
        # way, and only on the read path — nothing rewrites accounts.json here.
        if "display" in account:
            if account.pop("display") != "custom" and \
                    account.get("format", OLD_DEFAULT_FORMAT) == OLD_DEFAULT_FORMAT:
                account["format"] = format.DEFAULT_FORMAT
                account["format_colors"] = dict(format.DEFAULT_FORMAT_COLORS)
    return reg


def save(reg: dict) -> None:
    root = paths.accounts_root()
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".accounts-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(reg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, paths.registry_file())
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def find(reg: dict, slug: str):
    for account in reg["accounts"]:
        if account["slug"] == slug:
            return account
    return None


def index_of(reg: dict, slug: str) -> int:
    for i, account in enumerate(reg["accounts"], start=1):
        if account["slug"] == slug:
            return i
    raise KeyError(slug)


def _renumber(reg: dict) -> None:
    for i, account in enumerate(reg["accounts"], start=1):
        account["signal"] = i


def add(reg: dict, slug: str, email: str, nickname):
    if find(reg, slug):
        raise ValueError(f"account already exists: {slug}")
    used = {a["color"] for a in reg["accounts"]}
    color = next((h for _name, h in paths.PALETTE if h not in used),
                 paths.PALETTE[0][1])
    account = {
        "slug": slug,
        "nickname": nickname,
        "email": email,
        "color": color,
        "hide_icon": False,
        "format": format.DEFAULT_FORMAT,
        "format_colors": {},
        "headless": False,
        "dangerous": False,
        "warned_invisible": False,
        "signal": len(reg["accounts"]) + 1,
    }
    reg["accounts"].append(account)
    _renumber(reg)
    if reg["default"] is None:
        reg["default"] = slug
    return account


def headless_slug(reg: dict):
    """The account `claude -p` runs under, or None if the user has not picked."""
    return next((a["slug"] for a in reg["accounts"] if a.get("headless")), None)


def set_headless(reg: dict, slug) -> None:
    """Point the headless runner at one account, or nowhere when slug is None.

    Exclusive by construction rather than by a top-level key, so that the menu
    row stays a pure function of the account dict it renders — the same shape as
    hide_icon. Passing a slug that is already set is how the menu clears it.
    """
    if slug is not None and find(reg, slug) is None:
        raise KeyError(slug)
    for account in reg["accounts"]:
        account["headless"] = account["slug"] == slug


def remove(reg: dict, slug: str) -> None:
    reg["accounts"] = [a for a in reg["accounts"] if a["slug"] != slug]
    _renumber(reg)
    if reg["default"] == slug:
        reg["default"] = reg["accounts"][0]["slug"] if reg["accounts"] else None


def set_field(reg: dict, slug: str, field: str, value) -> None:
    account = find(reg, slug)
    if account is None:
        raise KeyError(slug)
    if field == "display":
        raise ValueError("display modes are retired; set 'format' instead")
    if field == "color":
        # An int is the shape the pre-hex registry and `ccs color <slug> 3`
        # both use; it is normalised here rather than rejected, so the palette
        # keeps working as a set of shorthands.
        if isinstance(value, int) and not isinstance(value, bool):
            if not 0 <= value < len(paths.PALETTE):
                raise ValueError(f"invalid colour index: {value}")
            value = paths.PALETTE[value][1]
        # HEX, not valid_color: auto/dim/account are token values and mean
        # nothing for the widget's own colour.
        elif not (isinstance(value, str) and format.HEX.match(value)):
            raise ValueError(f"invalid colour: {value!r}")
    # An unknown *token* is deliberately not checked: retiring a token in a later
    # version would turn a stored format into a hard error with nothing on the
    # bar, where rendering it literally is visible and self-explaining.
    if field == "format" and not isinstance(value, str):
        raise ValueError(f"format must be a string: {value!r}")
    if field == "format_colors":
        if not isinstance(value, dict) or \
                not all(format.valid_color(v) for v in value.values()):
            raise ValueError(f"invalid format colours: {value!r}")
    account[field] = value
