"""Argument dispatch."""
import os
import subprocess
import sys

from . import accounts, history, label, launch, menu, paths, pickers, registry, waybar


def notify(text: str) -> None:
    subprocess.run(["notify-send", "Claude accounts", text], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _refresh(reg, account) -> None:
    """Persist, warn if newly invisible, and refresh both label and menu.

    The ●/○ marks and the ☐/☑ checkbox are baked into menu.xml, which Waybar
    caches — a signal only repaints the label, so without rewriting the file
    and forcing a full reload the dot stays wherever it was when the bar last
    started. That is a user-initiated click, so the reload flicker is fine;
    the 30 s render tick still guards itself with session_set_changed().
    """
    if label.check_invisible_warning(account):
        notify(label.WARNING_TEXT)
    registry.save(reg)
    menu.write(account, history.scan())
    waybar.reload()  # subsumes the per-module signal, which only repaints labels


def _mutate(slug: str, field: str, value) -> int:
    reg = registry.load()
    if registry.find(reg, slug) is None:
        return 1
    try:
        registry.set_field(reg, slug, field, value)
    except ValueError:
        return 1
    _refresh(reg, registry.find(reg, slug))
    return 0


def cmd_render(slug: str) -> int:
    """Print the label and regenerate the menu as a side effect.

    Waybar caches menu.xml at startup and neither the 30 s exec tick nor the
    per-module RTMIN signal makes it re-read (see docs/superpowers/
    spike-waybar-menu.md). Only a full SIGUSR2 reload does, so reload when the
    session set actually changed — unconditionally would flicker every tick.
    """
    reg = registry.load()
    account = registry.find(reg, slug)
    if account is None:
        return 1
    sessions = history.scan()
    changed = menu.session_set_changed(slug, sessions)
    menu.write(account, sessions)
    print(label.render(account, registry.index_of(reg, slug)))
    if changed:
        waybar.reload()
    return 0


def cmd_list() -> int:
    reg = registry.load()
    for i, a in enumerate(reg["accounts"], start=1):
        star = "*" if reg["default"] == a["slug"] else " "
        color = paths.PALETTE[a["color"]][0]
        print(f"{star} {i}  {a['slug']:<14} {a['email']:<28} {color:<7} {a['display']}")
    return 0


def cmd_add(gui: bool) -> int:
    nickname = pickers.prompt("nickname (optional):", gui)
    reg = registry.load()
    base = registry.slugify(nickname or "account")
    slug, n = base, 2
    while registry.find(reg, slug):
        slug, n = f"{base}-{n}", n + 1

    accounts.create(slug)
    accounts.relink(slug)
    accounts.seed_config(slug)

    env = accounts.env_for(slug)
    command = f"{paths.claude_bin()} auth login"
    if gui:
        subprocess.run(["kitty", "--class", "ccas", "-e", "bash", "-lc",
                        f"{command}; echo; read -n1 -r -p 'press any key…'"],
                       env=env, check=False)
    else:
        subprocess.run(["bash", "-lc", command], env=env, check=False)

    status = accounts.auth_status(slug)
    if not status.get("loggedIn"):
        accounts.to_trash(paths.account_dir(slug))
        notify("Login did not complete — account discarded.")
        return 1

    registry.add(reg, slug, status.get("email", slug), nickname)
    registry.save(reg)
    waybar.apply(reg)
    waybar.reload()
    return 0


def cmd_rm(slug: str) -> int:
    reg = registry.load()
    if registry.find(reg, slug) is None:
        return 1
    directory = paths.account_dir(slug)
    if directory.exists():
        accounts.to_trash(directory)
    registry.remove(reg, slug)
    registry.save(reg)
    waybar.apply(reg)
    waybar.reload()
    return 0


def cmd_manage(action: str, slug, gui: bool) -> int:
    if action == "add":
        return cmd_add(gui)
    if action == "rename":
        name = pickers.prompt_or_clear("new nickname:", "⌫  clear nickname", gui)
        if name is pickers.CANCEL:
            return 0
        reg = registry.load()
        if registry.find(reg, slug) is None:
            return 1
        registry.set_field(reg, slug, "nickname", name)
        _refresh(reg, registry.find(reg, slug))
        return 0
    if action == "remove":
        confirm = pickers.choose(f"remove {slug}?", ["no", "yes"], gui)
        return cmd_rm(slug) if confirm == "yes" else 0
    return 1


def cmd_tty(args, gui=None) -> int:
    reg = registry.load()
    if gui is None:
        gui = pickers.is_gui()
    if args:
        slug = reg["default"]
        if slug is None:
            print("ccs: no accounts configured; run 'ccs add'", file=sys.stderr)
            return 1
        return subprocess.run([str(paths.claude_bin()), *args],
                              env=accounts.env_for(slug), check=False).returncode
    if not reg["accounts"]:
        return cmd_add(gui)
    slug = reg["accounts"][0]["slug"]
    if len(reg["accounts"]) > 1:
        choice = pickers.choose("account", [label.display_name(a) for a in reg["accounts"]], gui)
        if choice is None:
            return 1
        slug = next(a["slug"] for a in reg["accounts"] if label.display_name(a) == choice)
    return cmd_mode_menu(slug, gui)


def cmd_mode_menu(slug: str, gui: bool) -> int:
    cwd = os.getcwd()
    short = history.abbreviate(cwd)
    options = [f"New here  ({short})", f"Resume last in  {short}",
               f"History in  {short}…", "All projects…"]
    choice = pickers.choose("mode", options, gui)
    if choice is None:
        return 1
    if choice.startswith("New here"):
        return launch.run(slug, "new", cwd, gui, cwd)
    if choice.startswith("Resume last"):
        return launch.run(slug, "last", None, gui, cwd)
    if choice.startswith("History in"):
        return launch.run(slug, "search", None, gui, cwd)
    return launch.run(slug, "new", None, gui, None)


def main(argv) -> int:
    # Waybar-generated commands pass --gui explicitly. Sniffing stdin is not
    # reliable there: Waybar inherits the compositor's stdin, which on a TTY
    # session is a real terminal, so a click would take the terminal path and
    # block on input() against a console the user cannot see.
    forced_gui = False
    argv = list(argv)
    if argv and argv[0] == "--gui":
        forced_gui = True
        argv = argv[1:]

    if not argv:
        return cmd_tty([], True if forced_gui else None)
    command, rest = argv[0], argv[1:]
    gui = True if forced_gui else pickers.is_gui()

    if command == "list":
        return cmd_list()
    if command == "add":
        return cmd_add(gui)
    if command == "rm":
        return cmd_rm(rest[0]) if rest else 1
    if command == "nick":
        return _mutate(rest[0], "nickname", rest[1] if len(rest) > 1 else None) if rest else 1
    if command == "color":
        return _mutate(rest[0], "color", int(rest[1])) if len(rest) > 1 else 1
    if command == "display":
        return _mutate(rest[0], "display", rest[1]) if len(rest) > 1 else 1
    if command == "hide":
        if len(rest) < 2:
            return 1
        reg = registry.load()
        account = registry.find(reg, rest[0])
        if account is None:
            return 1
        value = not account["hide_icon"] if rest[1] == "toggle" else rest[1] == "on"
        return _mutate(rest[0], "hide_icon", value)
    if command == "default":
        reg = registry.load()
        if not rest:
            print(reg["default"] or "")
            return 0
        if registry.find(reg, rest[0]) is None:
            return 1
        reg["default"] = rest[0]
        registry.save(reg)
        return 0
    if command == "relink":
        reg = registry.load()
        targets = rest or [a["slug"] for a in reg["accounts"]]
        for slug in targets:
            accounts.relink(slug)
        return 0
    if command == "render":
        return cmd_render(rest[0]) if rest else 1
    if command == "config":
        reg = registry.load()
        waybar.apply(reg)
        waybar.apply_bashrc()
        waybar.reload()
        return 0
    if command == "launch":
        if len(rest) < 2:
            return 1
        arg = rest[2] if len(rest) > 2 else None
        return launch.run(rest[0], rest[1], arg, gui, None)
    if command == "manage":
        if not rest:
            return 1
        return cmd_manage(rest[0], rest[1] if len(rest) > 1 else None, gui)
    if command == "tty":
        return cmd_tty(rest, gui)

    reg = registry.load()
    if registry.find(reg, command):
        if not rest:
            return cmd_mode_menu(command, gui)
        return subprocess.run(rest, env=accounts.env_for(command), check=False).returncode

    print(f"ccs: unknown command: {command}", file=sys.stderr)
    return 2
