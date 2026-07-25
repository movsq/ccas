"""Read-only audit of everything CCAS owns outside this repo.

CCAS writes into four places that can drift apart: `config.jsonc`, `~/.bashrc`,
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
from collections import namedtuple

from . import label, paths, registry, waybar

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


def _no_legacy_shell_function() -> Check:
    """A shell opened before the install that removed it still shadows `claude`.

    Invisible from inside that shell, which is the whole reason to check.
    """
    path = paths.bashrc()
    present = path.exists() and waybar.START in path.read_text(encoding="utf-8")
    return Check(not present, "no CCAS shell function in ~/.bashrc",
                 "a legacy `claude()` block is still there; run `./install.sh` "
                 "to strip it, then open a new shell" if present else "")


def run(reg: dict) -> list:
    checks = [_claude_home_has_no_symlinks(), *_binaries(),
              _no_legacy_shell_function(), *_registry_checks(reg),
              *_waybar_matches(reg)]
    for account in reg["accounts"]:
        checks.extend(_account_checks(account))
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
