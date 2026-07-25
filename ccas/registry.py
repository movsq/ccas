"""The accounts.json registry."""
import json
import os
import re
import tempfile
import unicodedata

from . import paths


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
    color = next((c for c in range(len(paths.PALETTE)) if c not in used), 0)
    account = {
        "slug": slug,
        "nickname": nickname,
        "email": email,
        "color": color,
        "display": "nickname",
        "hide_icon": False,
        "headless": False,
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
    if field == "display" and value not in paths.DISPLAY_MODES:
        raise ValueError(f"invalid display mode: {value}")
    if field == "color" and not (0 <= int(value) < len(paths.PALETTE)):
        raise ValueError(f"invalid colour index: {value}")
    account[field] = value
