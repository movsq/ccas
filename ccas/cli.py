"""Argument dispatch."""
import os
import subprocess
import sys

from . import (accounts, doctor, history, label, launch, menu, paths, pickers,
               registry, waybar)


# `claude <args>` normally resolves an account, because almost everything it can
# do belongs to one: -p, -c, -r, mcp, plugin, auth. These are the exceptions —
# they inspect or repair the installation itself, so asking would be noise. A
# deny-list rather than an allow-list on purpose: an unrecognised argument is far
# more likely to start a session than not, and guessing wrong the other way runs
# it under an account the user did not choose.
NO_ACCOUNT_ARGS = {"--version", "-v", "--help", "-h",
                   "doctor", "install", "update", "upgrade", "gateway"}

# The one row of the account picker that is not an account. ASCII '+' rather
# than a glyph: the vetted-glyph rule is about Waybar's FontAwesome-first stack,
# and this row is drawn by fzf or fuzzel, whose fonts nothing here has measured.
ADD_ROW = "+  Add account…"


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


def _refresh_all(reg) -> None:
    """Rewrite every account's menu and reload once.

    The headless mark is exclusive, so setting it on one account clears it on
    the others — every menu.xml is stale, not just the one that was clicked.
    """
    registry.save(reg)
    sessions = history.scan()
    for account in reg["accounts"]:
        menu.write(account, sessions)
    waybar.reload()


def _toggle_headless(reg, slug: str) -> None:
    """Point the headless runner at this account, or clear it if it already is.

    Choosing the account that is already the runner clears it, which is how the
    user gets the one-time prompt back.
    """
    current = registry.headless_slug(reg)
    registry.set_headless(reg, None if current == slug else slug)
    _refresh_all(reg)


def _runner_slug(reg, args, gui):
    """Which account a `claude <args>` run belongs to, or None to abort.

    Asks once, on the first interactive run that needs an answer, and remembers
    it — so the common case stays silent and scripts never see a prompt at all.
    """
    override = os.environ.get("CCAS_ACCOUNT")
    if override:
        if registry.find(reg, override) is None:
            print(f"ccs: CCAS_ACCOUNT={override} is not an account", file=sys.stderr)
            return None
        return override  # a one-off; deliberately not remembered

    if args and args[0] in NO_ACCOUNT_ARGS:
        return reg["default"]

    chosen = registry.headless_slug(reg)
    if chosen:
        return chosen

    # A pipe, a script, cron: prompting would block on a stdin nobody can type
    # into — the same shape of bug as the original is_gui one.
    if not sys.stdin.isatty():
        return reg["default"]

    names = [label.display_name(a) for a in reg["accounts"]]
    # Asked even when there is only one account: the point is that the user knows
    # which one is answering `claude -p`, and it is a single question, once.
    choice = pickers.choose("account for headless runs", names, gui)
    if choice is None:
        return None
    slug = next(a["slug"] for a in reg["accounts"] if label.display_name(a) == choice)
    registry.set_headless(reg, slug)
    _refresh_all(reg)
    return slug


def _danger_flag(reg, slug: str, args):
    """`[--dangerously-skip-permissions]` when this account opted in, else `[]`.

    Two things the account setting must lose to, both because they were typed
    now and it was clicked days ago: the flag already being present (injecting
    would double it) and an explicit --permission-mode (injecting alongside it
    would override the mode the user just asked for). NO_ACCOUNT_ARGS is the
    third exemption — those inspect the installation and never run a session.
    """
    account = registry.find(reg, slug)
    if not (account and account.get("dangerous")):
        return []
    if args and args[0] in NO_ACCOUNT_ARGS:
        return []
    if launch.DANGEROUS in args or "--permission-mode" in args:
        return []
    return [launch.DANGEROUS]


def _toggle_dangerous(reg, slug: str) -> int:
    """Flip one account's permission bypass. Not exclusive, unlike headless."""
    account = registry.find(reg, slug)
    if account is None:
        return 1
    registry.set_field(reg, slug, "dangerous", not account.get("dangerous"))
    _refresh(reg, registry.find(reg, slug))
    return 0


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

    The write is bound to the same condition. Waybar is still showing the menu
    it cached at the last reload, and hist-i resolves against line i of
    history.tsv; rewriting the pair without reloading would leave the visible
    row and the session it launches pointing at different things.
    """
    reg = registry.load()
    account = registry.find(reg, slug)
    if account is None:
        return 1
    sessions = history.scan()
    changed = menu.session_set_changed(slug, sessions)
    if changed:
        menu.write(account, sessions)
    print(label.render(account, registry.index_of(reg, slug)))
    if changed:
        waybar.reload()
    return 0


def cmd_list() -> int:
    reg = registry.load()
    for i, a in enumerate(reg["accounts"], start=1):
        star = "*" if reg["default"] == a["slug"] else " "
        # Permission bypass is worth seeing without opening a menu.
        bang = "!" if a.get("dangerous") else " "
        color = paths.PALETTE[a["color"]][0]
        print(f"{star}{bang} {i}  {a['slug']:<14} {a['email']:<28} "
              f"{color:<7} {a['display']}")
    return 0


def cmd_doctor() -> int:
    checks = doctor.run(registry.load())
    print(doctor.report(checks))
    return 1 if any(not c.ok for c in checks) else 0


def _free_slug(reg, base: str, taken=None) -> str:
    """`base`, or base-2, base-3… — free in the registry *and* on disk.

    A directory with no registry entry is not impossible (a half-finished add,
    a restore from trash) and reusing it would put two accounts in one
    CLAUDE_CONFIG_DIR.
    """
    slug, n = base, 2
    while (registry.find(reg, slug) or paths.account_dir(slug).exists()) \
            and slug != taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def cmd_add(gui: bool) -> int:
    nickname = pickers.prompt("nickname (optional):", gui)
    reg = registry.load()
    # A placeholder. The account directory *is* CLAUDE_CONFIG_DIR, so it has to
    # exist before `auth login` runs — and the email that should name it is not
    # known until that login returns. Renamed below, once it is.
    slug = _free_slug(reg, registry.slugify(nickname or "account"))

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

    # The window where a rename is free: login has exited, nothing references
    # the account yet, and the slug becomes permanent the moment it is written
    # to the registry, the Waybar block and every generated command.
    email = status.get("email", "")
    if email:
        final = _free_slug(reg, registry.slugify(email), taken=slug)
        if final != slug:
            accounts.rename(slug, final)
            slug = final

    registry.add(reg, slug, email or slug, nickname)
    registry.save(reg)
    # Before waybar.apply, which is what makes menu-file point here. The render
    # tick would write it within 30 s, but until then the new icon opens a menu
    # Waybar cannot read.
    menu.write(registry.find(reg, slug), history.scan())
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
        if reg["default"] is None:
            print("ccs: no accounts configured; run 'ccs add'", file=sys.stderr)
            return 1
        slug = _runner_slug(reg, args, gui)
        if slug is None:
            return 1
        argv = [str(paths.claude_bin()), *_danger_flag(reg, slug, args), *args]
        return subprocess.run(argv, env=accounts.env_for(slug),
                              check=False).returncode
    if not reg["accounts"]:
        return cmd_add(gui)  # nothing to pick between, and fuzzel dies on an empty list
    # No shortcut for a single account any more: the list is no longer a
    # pointless one row, and skipping it hid Add from the user most likely to
    # want a second account.
    names = [label.display_name(a) for a in reg["accounts"]]
    choice = pickers.choose("account", [*names, ADD_ROW], gui)
    if choice is None:
        return 1
    if choice == ADD_ROW:  # before the lookup — it is not a display name
        return cmd_add(gui)
    slug = next(a["slug"] for a in reg["accounts"] if label.display_name(a) == choice)
    return cmd_mode_menu(slug, gui)


def cmd_mode_menu(slug: str, gui: bool) -> int:
    cwd = os.getcwd()
    short = history.abbreviate(cwd)
    reg = registry.load()
    account = registry.find(reg, slug)
    # Same marks as the Waybar menu, but the wording says what picking it does.
    # Sat among four verbs in a flat fzf list, a bare "○ Headless runner" reads
    # as a status line rather than something you can act on.
    headless_row = (f"{menu.MARK_ON} Headless runner — pick to clear"
                    if account and account.get("headless")
                    else f"{menu.MARK_OFF} Select as headless runner")
    danger_row = (f"{menu.MARK_ON} Skipping permissions — pick to clear"
                  if account and account.get("dangerous")
                  else f"{menu.MARK_OFF} Skip permissions (dangerous)")
    options = [f"New here  ({short})", f"Resume last in  {short}",
               f"History in  {short}…", "All projects…", headless_row,
               danger_row, "Manage…"]
    choice = pickers.choose("mode", options, gui)
    if choice is None:
        return 1
    # Before the launch rows: the last of those is this dispatch's catch-all.
    if choice == headless_row:
        _toggle_headless(reg, slug)
        return 0
    if choice == danger_row:
        return _toggle_dangerous(reg, slug)
    if choice == "Manage…":
        return cmd_manage_menu(slug, gui)
    if choice.startswith("New here"):
        return launch.run(slug, "new", cwd, gui, cwd)
    if choice.startswith("Resume last"):
        return launch.run(slug, "last", None, gui, cwd)
    if choice.startswith("History in"):
        return launch.run(slug, "search", None, gui, cwd)
    return launch.run(slug, "new", None, gui, None)


def cmd_manage_menu(slug: str, gui: bool) -> int:
    """The terminal half of the Waybar menu's Manage submenu.

    Add is not here — it belongs to the account picker, which is the screen
    where the set of accounts is the subject. Rename is named after the
    nickname, which is what the user sees on the bar and what is about to
    change; Remove is named after the slug, because it trashes the account
    directory and the slug is what that directory is called.
    """
    account = registry.find(registry.load(), slug)
    rename = f"Rename {label.display_name(account) if account else slug}…"
    remove = f"Remove {slug}…"
    choice = pickers.choose("manage", [rename, remove], gui)
    if choice is None:
        return 1
    return cmd_manage("rename" if choice == rename else "remove", slug, gui)


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

    # `ccs -p "…"` in place of the old `claude()` shell function: anything that
    # starts with a flag is claude's, not ours. `--` is the escape for claude's
    # own subcommands (mcp, doctor, update), which read as ccs commands.
    if command == "--":
        return cmd_tty(rest, gui)
    if command.startswith("-"):
        return cmd_tty(argv, gui)

    if command == "list":
        return cmd_list()
    # `doctor` is a claude subcommand too; `ccs -- doctor` reaches that one.
    if command == "doctor":
        return cmd_doctor()
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
    if command == "headless":
        reg = registry.load()
        if not rest:
            print(registry.headless_slug(reg) or "")
            return 0
        if registry.find(reg, rest[0]) is None:
            return 1
        _toggle_headless(reg, rest[0])
        return 0
    if command == "dangerous":
        reg = registry.load()
        if not rest:
            for account in reg["accounts"]:
                if account.get("dangerous"):
                    print(account["slug"])
            return 0
        return _toggle_dangerous(reg, rest[0])
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
        # The unconditional rebuild. cmd_render only writes when it is also
        # going to reload, so a code change that alters the XML would otherwise
        # never reach the menus already on disk — this is what install.sh runs.
        reg = registry.load()
        sessions = history.scan()
        for account in reg["accounts"]:
            menu.write(account, sessions)
        waybar.apply(reg)
        waybar.strip_bashrc()
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
