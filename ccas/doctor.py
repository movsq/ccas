"""Read-only audit of everything CCAS owns outside this repo.

CCAS writes into four places that can drift apart: `config.jsonc`, `style.css`,
the account directories, and the registry. Every bug found on the real system
was drift between them rather than a logic error, and each was caught by hand
with the checks below. `run()` is those checks, in one pass.

Nothing here may write: not to `~/.claude` (the invariant it exists to police),
and not to an account either, so that running it can never mask the fault it is
looking for. Read, compare, report.
"""
import json
import os
import re
import time
from collections import namedtuple

from . import format, label, paths, poll, registry, usage, waybar

Check = namedtuple("Check", "ok label detail")

# Terminal output, not the bar — the FontAwesome font-stack rule does not apply.
OK = "✓"
BAD = "✗"


def _claude_home_has_no_symlinks() -> Check:
    home = paths.claude_home()
    if not home.is_dir():
        return Check(False, "~/.claude exists", f"{home} is not a directory")
    links = sorted(p.name for p in home.iterdir() if p.is_symlink())
    detail = ("links must point *at* ~/.claude, never out of it: "
              + ", ".join(links)) if links else ""
    return Check(not links, "no symlinks in ~/.claude", detail)


def _binaries() -> list:
    out = []
    for name, path in (("claude binary", paths.claude_bin()),
                       ("ccs binary", paths.ccs_bin())):  # not `label`: module name
        ok = path.exists() and os.access(path, os.X_OK)
        out.append(Check(ok, name, "" if ok else f"{path} is missing or not executable"))
    return out


def _account_checks(account: dict) -> list:
    slug = account["slug"]
    # Rows are named the way the user names the account, not the way the
    # filesystem does. The slug still identifies what is wrong, so it stays in
    # the detail line, which is where the path and the fix already live.
    name = label.display_name(account)
    directory = paths.account_dir(slug)
    if not directory.is_dir():
        return [Check(False, f"{name}: account directory",
                      f"{directory} is missing; run `ccs relink`")]

    home = paths.claude_home()
    dangling, strays = [], []
    for entry in directory.iterdir():
        if not entry.is_symlink():
            continue
        if not entry.exists():
            dangling.append(entry.name)
        elif not str(os.path.realpath(entry)).startswith(str(home.resolve())):
            strays.append(entry.name)
    shared = [p.name for p in home.iterdir() if p.name not in paths.BLOCKLIST] \
        if home.is_dir() else []
    missing = [name for name in shared if not (directory / name).exists()]

    detail = "; ".join(filter(None, [
        f"dangling: {', '.join(sorted(dangling))}" if dangling else "",
        f"not under ~/.claude: {', '.join(sorted(strays))}" if strays else "",
        f"never linked: {', '.join(sorted(missing))}" if missing else "",
    ]))
    checks = [Check(not detail, f"{name}: symlinks resolve",
                    f"{directory}: {detail}; run `ccs relink`" if detail else "")]

    # Reported, never repaired: rewriting the format here would mask the very
    # typo the check exists to name.
    unknown = format.unknown_tokens(account.get("format") or "")
    checks.append(Check(
        not unknown, f"{name}: format tokens are known",
        "" if not unknown else
        f"unknown: {' '.join(unknown)} — they render literally on the bar; "
        f"fix with: ccs format {slug} '…'"))

    return checks


def _waybar_matches(reg: dict) -> list:
    path = paths.waybar_config()
    if not path.exists():
        return [Check(False, "waybar config", f"{path} is missing")]
    text = path.read_text(encoding="utf-8")

    if waybar.START not in text:
        return [Check(False, "waybar managed block",
                      "not installed; run `./install.sh`")]

    # config.jsonc carries // comments, so it is matched, not parsed — the same
    # reason waybar.py never round-trips it through json.
    listed = re.findall(r'"(custom/cc-[^"]+)"\s*:', text)
    expected = waybar.module_names(reg)
    hosted = re.search(rf'"{waybar.HOST_LIST}"\s*:\s*(\[[^\]]*\])', text)
    in_group = [n for n in json.loads(hosted.group(1)) if n.startswith("custom/cc-")] \
        if hosted else []

    checks = []
    drift = sorted(set(expected) ^ set(listed))
    checks.append(Check(not drift, "waybar modules match the registry",
                        f"{', '.join(drift)}; run `ccs config`" if drift else ""))
    placed = sorted(set(expected) ^ set(in_group))
    checks.append(Check(not placed, f"waybar modules are in {waybar.HOST_LIST}",
                        f"{', '.join(placed)}; run `ccs config`" if placed else ""))
    return checks


def _style_matches(reg: dict) -> Check:
    """Does the stylesheet's managed block name exactly this account set?

    The failure it exists to catch is silent: a widget whose id no rule names
    still draws and still clicks, it just stops answering the pointer — which
    reads as a bad install rather than as stale CSS. It happened to an account
    that was removed and added back under a different slug.
    """
    label_ = "waybar style block matches the registry"
    path = paths.waybar_style()
    # The widgets that exist, which is not every account: a hidden one has no
    # module, so a rule naming it would be dead text rather than a fix.
    shown = waybar.visible(reg)
    if not shown:
        return Check(True, label_, "")
    if not path.exists():
        return Check(False, label_, f"{path} is missing; run `ccs config`")
    text = path.read_text(encoding="utf-8")
    block = text.split(waybar.START, 1)[-1].split(waybar.END, 1)[0] \
        if waybar.START in text else ""
    missing = [a["slug"] for a in shown
               if f"#custom-cc-{a['slug']}:hover" not in block]
    return Check(not missing, label_,
                 f"no hover rule for {', '.join(missing)}; run `ccs config`"
                 if missing else "")


def _registry_checks(reg: dict) -> list:
    slugs = [a["slug"] for a in reg["accounts"]]
    default = reg.get("default")
    ok = default is None or default in slugs
    checks = [Check(ok, "default account exists",
                    "" if ok else f"default is {default!r}, which is not an account")]

    marked = [a["slug"] for a in reg["accounts"] if a.get("headless")]
    checks.append(Check(len(marked) < 2, "at most one headless runner",
                        f"{', '.join(marked)} are all marked; "
                        f"run `ccs headless <slug>`" if len(marked) > 1 else ""))
    return checks


def _statusline_hook() -> Check:
    """Is `ccs statusline` wired into ~/.claude/settings.json?

    Read-only, like everything here — and it has to be: settings.json is a
    symlink from every account directory into ~/.claude, so writing it is the
    one thing CCAS may never do. The user adds the line; this says whether they
    have, and spells out the line to add.

    It fails rather than reports. The feature ships never-wired, and a check
    that passes on never-wired cannot say the one thing the user needs to hear.
    """
    label_ = "usage: statusline hook wired"
    path = paths.claude_home() / "settings.json"
    wanted = f"{paths.ccs_bin()} statusline"
    if not path.exists():
        return Check(False, label_, f"{path} is missing")
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return Check(False, label_, f"{path} does not parse: {exc}")

    command = (settings.get("statusLine") or {}).get("command") or ""
    if str(paths.ccs_bin()) in command and "statusline" in command:
        return Check(True, label_, "")
    if command:
        # Their statusline stays: it becomes the delegate, which is the whole
        # reason `ccs statusline` chains instead of replacing.
        fix = f'set statusLine.command to "{wanted} {command}"'
    else:
        fix = ('add "statusLine": {"type": "command", "command": '
               f'"{wanted} <your statusline, if any>"}}')
    return Check(False, label_,
                 f"{path}: {fix} — without it no account records its usage")


def _panel_import_error():
    """The exact reason the panel cannot open, or None if it can.

    Imported here rather than at module scope for the same reason panel_ui does
    it inside show(): `ccs doctor` must run on a machine that has none of this,
    since telling the user what is missing is the whole point of the check.
    """
    try:
        import ctypes
        ctypes.CDLL("libgtk4-layer-shell.so.0", mode=ctypes.RTLD_GLOBAL)
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk, Gtk4LayerShell  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — any failure is the same report
        return exc
    return None


def _panel_dependencies() -> Check:
    """Report only. Naming the package is as far as doctor may go: installing
    one would be a write, and a repair inside doctor masks the fault it is
    looking for."""
    error = _panel_import_error()
    return Check(error is None, "the panel's dependencies are installed",
                 "" if error is None else
                 f"{error}\n    the bar's click opens nothing without them; "
                 "install with: sudo pacman -S python-gobject gtk4 "
                 "gtk4-layer-shell")


def _poll_timer() -> Check:
    """Is the five-minute poll actually running?

    Without it an idle account's numbers freeze at its last session, which is
    indistinguishable from the feature not existing — so a dead timer has to be
    reported here rather than discovered by mistrusting the bar.
    """
    state = poll.timer_state()
    return Check(state == "active", "usage: poll timer",
                 "" if state == "active" else
                 f"systemctl says {state}; start it with: "
                 "systemctl --user enable --now ccas-poll.timer")


def _usage_freshness(account: dict) -> Check:
    """How old this account's reading is, and how long its token can still be
    polled with. Always passes: both facts are expected states, not faults. An
    expired token is the next poll's problem to hand to claude for renewal,
    not a fault here — and with `CCAS_NO_TOKEN_REFRESH=1` set it is not even
    that, just the account going quiet until it is next used."""
    name = label.display_name(account)
    now = time.time()
    reading = usage.load(account["slug"])
    age = (f"recorded {poll.age(now - reading['fetched_at'])} ago"
           if reading else "nothing recorded yet")
    credentials = poll.access_token(account["slug"])
    if credentials is None:
        token = "no readable token"
    elif credentials[1] <= now:
        token = f"token expired {poll.age(now - credentials[1])} ago — polling is quiet"
    else:
        token = f"token good for {poll.age(credentials[1] - now)}"
    # Named by the nickname and identified by the slug in the detail, the same
    # way `_account_checks` does it.
    return Check(True, f"{name}: usage freshness",
                 f"{account['slug']}: {age}; {token}")


def run(reg: dict) -> list:
    checks = [_claude_home_has_no_symlinks(), *_binaries(),
              _style_matches(reg), _statusline_hook(), _poll_timer(),
              _panel_dependencies(),
              *_registry_checks(reg),
              *_waybar_matches(reg)]
    for account in reg["accounts"]:
        checks.extend(_account_checks(account))
        checks.append(_usage_freshness(account))
    return checks


def report(checks) -> str:
    lines = []
    for check in checks:
        mark = OK if check.ok else BAD
        lines.append(f"{mark} {check.label}" + (f"\n    {check.detail}"
                                                if check.detail else ""))
    bad = sum(1 for c in checks if not c.ok)
    lines.append("")
    lines.append("all good" if not bad else f"{bad} problem(s) found")
    return "\n".join(lines)
