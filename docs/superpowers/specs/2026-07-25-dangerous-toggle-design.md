# Per-account `--dangerously-skip-permissions`

2026-07-25.

A per-account toggle that makes CCAS launch Claude Code with
`--dangerously-skip-permissions`. Session launches and the `ccs -p` passthrough
both honour it.

## Why per-account rather than global

The registry is already per-account, and `menu.build_xml` is deliberately a pure
function of the account dict — that is why `headless` is stored as a per-account
bool instead of a top-level key. A global flag would be the first setting to
break that, and every menu would have to render state it does not own.

Per-account also subsumes global: set it everywhere and you have global. The
reverse is not true, and "bypass in the throwaway account, ask me in the one
holding work credentials" is the reason separate accounts exist at all.

The honest counterargument, recorded so nobody re-litigates it: permission
bypass is arguably a property of how much the *machine* is trusted, not of an
identity. It lost to the two points above.

## The flag name is claude's, verbatim

Measured against the installed binary (2.1.220), not memory:

```
--dangerously-skip-permissions        Bypass all permission checks.
--allow-dangerously-skip-permissions  Enable bypassing as an option, without it
                                      being enabled by default.
--permission-mode <mode>              acceptEdits | auto | bypassPermissions |
                                      manual | dontAsk | plan
```

There is **no short alias** — the complete short-flag set is `-c -d -h -n -p -r
-v -w`. So there is no letter to mirror, and CCAS must not invent one: `main()`
forwards any argv whose first token starts with `-` to the real claude, so a
`ccs -y` would reach a binary that does not know `-y`. The toggle is therefore a
subcommand word, exactly like `ccs headless`.

`--allow-dangerously-skip-permissions` is a different flag — it makes bypass
*available*, not *on*. Do not reach for it by autocomplete.

## Why the passthrough is included

Print mode does not prompt; it **refuses and exits 2**:

```
$ claude -p 'Use the Write tool to create probe.txt…'
Claude requested permissions to write to …/probe.txt, but you haven't granted it yet.
exit 2, no file written

$ claude -p --dangerously-skip-permissions '…same…'
DONE          # probe.txt written
```

So `ccs -p` is the path that most needs the flag, not the least. Verified also
that the flag is accepted ahead of claude's own subcommands
(`claude --dangerously-skip-permissions mcp list` → rc 0), which makes injection
safe for the `ccs -- …` escape.

## Storage

`dangerous: bool` on the account dict. Non-exclusive, so it is shaped like
`hide_icon`, not like `headless`.

- `registry.load()` gains `account.setdefault("dangerous", False)` beside the
  existing `headless` backfill, for registries written before this existed.
- `registry.add()` writes `False`.
- No new registry function. `set_field` already carries plain values; the toggle
  logic lives in `cli`.

## Where the flag is added

Two paths, differing in who owns the argv.

**Session launches.** `launch.resolve()` gains a `dangerous: bool` parameter and
`_claude()` prepends the flag, so all four modes (`new`, `last`, `hist`,
`search`) carry it. `resolve()` stays side-effect free; `run()` loads the
registry and passes the bool in.

**Passthrough.** In `cmd_tty`, after `_runner_slug` resolves the account, insert
the flag between the binary and the user's args:
`[claude, "--dangerously-skip-permissions", *args]`. Two guards:

- Skip if `args` already contains `--dangerously-skip-permissions` or
  `--permission-mode`. Typing the flag yourself must never double it, and an
  explicit `--permission-mode` must win over a setting clicked days ago.
- Skip when `args[0]` is in `NO_ACCOUNT_ARGS` — those inspect the installation
  and never run a session.

## Surfaces

- **Waybar menu** — a row beside `Headless runner`, in that same group: both
  answer "how does this account run", not "what does it look like".
  `MARK_ON`/`MARK_OFF` + `Skip permissions (dangerous)`, action
  `ccs dangerous <slug>` generated through `waybar._ccs()` so it carries `--gui`.
- **Terminal mode menu** — a row shaped like `headless_row`, worded as an
  action: `○ Skip permissions (dangerous)` /
  `● Skipping permissions — pick to clear`.
- **CLI** — `ccs dangerous` prints the slugs that have it on, one per line;
  `ccs dangerous <slug>` toggles.
- **`ccs list`** — a `!` marker on a dangerous account, so the setting is
  visible without opening a menu.

Refresh goes through `_refresh` (one account), **not** `_refresh_all`. Nothing
about this flag changes another account's menu.

## Out of scope

The bar label does not change. Marking a dangerous account on the bar means
touching `label.py`'s pango sizing — the measure-don't-eyeball rule — and both
current accounts run in `icon only` mode, where there is nowhere to put a marker
without vetting a new glyph against the FontAwesome-first stack. Its own task if
wanted.

## Tests

| file | pins |
|---|---|
| `test_registry.py` | default is False; backfill on an older registry |
| `test_launch.py` | each of the four modes carries the flag when on, omits it when off |
| `test_cli.py` | passthrough injects it; both skip guards; `ccs dangerous` prints and toggles |
| `test_menu.py` | the row renders with the right mark |
| `test_waybar.py` | the action is generated and carries `--gui` |
