"""Argument dispatch."""
import json
import os
import subprocess
import sys
import time

from . import (accounts, doctor, history, label, launch, panel, panel_ui, paths,
               pickers, registry, usage, waybar)


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

# Not an exit code: a chip click reopens the panel on another account, and the
# loop in cmd_panel has to tell that apart from a command that finished.
SWITCH = object()


def notify(text: str) -> None:
    subprocess.run(["notify-send", "Claude accounts", text], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _refresh(reg, account) -> None:
    """Persist, warn if newly invisible, and repaint the label.

    A signal, not a reload: SIGRTMIN+n re-runs `exec` and updates the label,
    which since the menu stopped being a cached file is the only thing a
    setting change can alter on screen.
    """
    if label.check_invisible_warning(account):
        notify(label.WARNING_TEXT)
    registry.save(reg)
    waybar.signal(account["signal"])


def _refresh_all(reg) -> None:
    """Persist. `headless` is exclusive, so setting it on one account clears it
    on the others — but none of it reaches the bar: neither headless nor
    dangerous appears in the label, and there is no menu left to rewrite.
    """
    registry.save(reg)


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


def dispatch_panel(action):
    """Turn the panel's one Action into the command it names.

    Every branch goes through the entry point the fuzzel menu already used, so
    the panel gains no write path of its own: set_headless() keeps the runner
    exclusive, set_field() keeps display and colour validated, and accounts and
    launch keep the invariants they already hold. The widget tree decides
    nothing — it says what was clicked and this says what that means.
    """
    if action is None:
        return 1  # Esc, or a click outside the surface
    kind, slug, value = action
    if kind == "switch":
        return SWITCH
    if kind == "new":
        # cwd and scope are the same directory: the panel picked it explicitly,
        # which is what the bar click never had.
        return launch.run(slug, "new", value, True, value)
    if kind == "resume":
        return launch.run(slug, "resume", value, True, None)
    if kind == "headless":
        _toggle_headless(registry.load(), slug)
        return 0
    if kind == "dangerous":
        return _toggle_dangerous(registry.load(), slug)
    if kind == "hide_icon":
        account = registry.find(registry.load(), slug)
        if account is None:
            return 1
        return _mutate(slug, "hide_icon", not account["hide_icon"])
    if kind in ("display", "color"):
        return _mutate(slug, kind, value)
    if kind == "add":
        return cmd_add(True)
    if kind in ("rename", "remove"):
        return cmd_manage(kind, slug, True)
    raise ValueError(f"unknown panel action: {kind}")


def cmd_render(slug: str) -> int:
    """Print the label. Nothing else — this runs every 30 s, per account."""
    reg = registry.load()
    account = registry.find(reg, slug)
    if account is None:
        return 1
    print(label.render(account, registry.index_of(reg, slug), usage.read(slug)))
    return 0


def cmd_statusline(args) -> int:
    """Record the usage numbers Claude Code hands the statusline, then delegate.

    Wired by the user, once, in ~/.claude/settings.json — CCAS cannot write that
    file, because every account's settings.json is a symlink to it:

        "statusLine": {"type": "command",
                       "command": "…/ccs statusline …/statusline.sh"}

    A wrapper, not a replacement: the user already had a statusline and this
    must not cost them it. So the delegate gets the same bytes on stdin, its
    stdout goes out verbatim, and its exit code is ours. No timeout — a hung
    delegate hangs identically without the wrapper.

    Recording is the optional half and swallows everything: a statusline that
    raises is visible in every session, in every prompt, until it is fixed.
    """
    data = sys.stdin.buffer.read()
    try:
        slug = usage.account_slug()
        if slug and usage.record(slug, json.loads(data.decode("utf-8", "replace"))):
            account = registry.find(registry.load(), slug)
            if account:
                # Label only, never the menu, and only on a real change.
                waybar.signal(account["signal"])
    except Exception:  # noqa: BLE001 - see the docstring
        pass

    if not args:
        return 0
    try:
        return subprocess.run([args[0], *args[1:]], input=data, check=False).returncode
    except OSError as exc:
        print(f"ccs statusline: {args[0]}: {exc.strerror}", file=sys.stderr)
        return 127


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


def cmd_usage(slug=None) -> int:
    """Both windows, their age and their source — the bar without a bar.

    Where the label shows only pressure and the picker only what is worth
    saying, this shows everything recorded, so that "nothing on the bar" can be
    told apart from "nothing recorded" without opening a picker.
    """
    reg = registry.load()
    accounts_ = [registry.find(reg, slug)] if slug else reg["accounts"]
    if accounts_ == [None]:
        return 1
    now = time.time()
    for account in accounts_:
        reading = usage.read(account["slug"])
        if reading is None:
            print(f"{account['slug']:<14} {usage.NO_DATA}")
            continue
        age = history.humanise_age(max(0.0, now - reading["fetched_at"]))
        print(f"{account['slug']:<14} "
              f"{usage.column(reading, 'five_hour', now):<28}"
              f"{usage.column(reading, 'seven_day', now):<28}"
              f"{reading['source']}, {age} ago")
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


def _display_menu(reg, slug: str, gui: bool) -> int:
    account = registry.find(reg, slug)
    rows = [f"{label.MARK_ON if account['display'] == m else label.MARK_OFF} {m}"
            for m in paths.DISPLAY_MODES]
    choice = pickers.choose("display as", rows, gui)
    if choice is None:
        return 0
    return _mutate(slug, "display", choice.split(" ", 1)[1])


def _color_menu(reg, slug: str, gui: bool) -> int:
    account = registry.find(reg, slug)
    rows = [f"{label.MARK_ON if account['color'] == i else label.MARK_OFF} {name}"
            for i, (name, _hex) in enumerate(paths.PALETTE)]
    choice = pickers.choose("color", rows, gui)
    if choice is None:
        return 0
    return _mutate(slug, "color", rows.index(choice))


def cmd_panel(slug: str) -> int:
    """The GUI door. Loops so a chip click reopens rather than exits."""
    while True:
        action = panel_ui.show(panel.build_state(slug))
        result = dispatch_panel(action)
        if result is not SWITCH:
            return result
        slug = action.slug


def cmd_mode_menu(slug: str, gui: bool) -> int:
    # The two doors used to differ in their launch verbs, because the bar click
    # had no meaningful cwd and directory-free labels were the only honest ones.
    # The panel's project pane supplies a real directory, so that rule is retired
    # and the split is now the toolkit: GTK from the bar, fzf from a terminal —
    # a TTY and an ssh session cannot run the former.
    if gui:
        return cmd_panel(slug)

    cwd = os.getcwd()
    short = history.abbreviate(cwd)
    reg = registry.load()
    account = registry.find(reg, slug)
    # Same marks as the Waybar menu, but the wording says what picking it does.
    # Sat among four verbs in a flat fzf list, a bare "○ Headless runner" reads
    # as a status line rather than something you can act on.
    headless_row = (f"{label.MARK_ON} Headless runner — pick to clear"
                    if account and account.get("headless")
                    else f"{label.MARK_OFF} Select as headless runner")
    danger_row = (f"{label.MARK_ON} Skipping permissions — pick to clear"
                  if account and account.get("dangerous")
                  else f"{label.MARK_OFF} Skip permissions (dangerous)")
    hide_row = (f"{label.MARK_ON if account and account['hide_icon'] else label.MARK_OFF}"
                " Hide icon")
    # The launch verbs are the one part of this screen that is not the same from
    # both doors. A terminal has a cwd the user chose and scoping to it is the
    # point; a bar click has Waybar's cwd — `~`, wherever the compositor started
    # it — which is nobody's "here". So the bar gets the GtkMenu's three verbs,
    # which never mentioned a directory, and `resolve()` (which already ignores
    # cwd under gui) is told so by being handed None.
    scope = None if gui else cwd
    launch_rows = (["New session", "Resume last session", "Resume from history…"]
                   if gui else
                   [f"New here  ({short})", f"Resume last in  {short}",
                    f"History in  {short}…", "All projects…"])
    # The Waybar menu's grouping: launch verbs, appearance, runner toggles, manage.
    options = [*launch_rows,
               "Display as…", hide_row, "Color…",
               headless_row, danger_row, "Manage…"]
    # The account's identity, which used to be the menu's title row — a picker
    # prompted "mode" does not say which account it belongs to. Under it, where
    # the label has no room to be anything but a warning, usage in words: an
    # open window is the good state and must not read as missing data.
    choice = pickers.choose(label.display_name(account), options, gui,
                            note="\n".join(usage.lines(usage.read(slug))))
    if choice is None:
        return 1
    # Before the launch rows: the last of those is this dispatch's catch-all.
    if choice == "Display as…":
        return _display_menu(reg, slug, gui)
    if choice == "Color…":
        return _color_menu(reg, slug, gui)
    if choice == hide_row:
        return _mutate(slug, "hide_icon", not account["hide_icon"])
    if choice == headless_row:
        _toggle_headless(reg, slug)
        return 0
    if choice == danger_row:
        return _toggle_dangerous(reg, slug)
    if choice == "Manage…":
        return cmd_manage_menu(slug, gui)
    if choice.startswith("New here"):
        return launch.run(slug, "new", cwd, gui, scope)
    if choice.startswith("Resume last"):  # …in <dir>, or the bar's …session
        return launch.run(slug, "last", None, gui, scope)
    if choice.startswith("History in") or choice == "Resume from history…":
        return launch.run(slug, "search", None, gui, scope)
    return launch.run(slug, "new", None, gui, None)  # All projects… / New session


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
    if command == "usage":
        return cmd_usage(rest[0] if rest else None)
    if command == "statusline":
        return cmd_statusline(rest)
    if command == "render":
        return cmd_render(rest[0]) if rest else 1
    if command == "config":
        # Rewrite the managed block and take the old bashrc function out. This
        # is what install.sh runs; it no longer rebuilds anything per account,
        # because nothing per account is generated any more.
        reg = registry.load()
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
