# Session handoff prompt

Start a new Claude Code session in `~/ccas` and paste the block below.

---

Implement CCAS, a Claude Code account switcher for Waybar. The design and the
full implementation plan are already written:

- Spec: `docs/superpowers/specs/2026-07-25-ccas-design.md`
- Plan: `docs/superpowers/plans/2026-07-25-ccas.md`

Read both before doing anything. The plan has 12 tasks (0–11), each with
complete test and implementation code, and each ending in a commit. Use the
`superpowers:subagent-driven-development` skill to execute it task by task.

Four things that matter and are easy to get wrong:

1. **Never write to `~/.claude`.** CCAS reads it and symlinks into it. That
   guarantee is the point of the whole design. Task 11 Step 4 verifies it by
   checking that `~/.claude/.credentials.json`'s mtime is unchanged.
2. **Never delete anything** — move it to `~/.claude_trash/`, per my global
   CLAUDE.md.
3. **Task 0 is a spike that needs me.** It reloads Waybar and asks me to click a
   test module, so run it interactively and wait for my answers rather than
   guessing. Its outcome changes how Task 5 refreshes the menu.
4. **Never invoke `claude` by bare name from inside the code.** The install adds
   a `claude()` shell function; a bare call re-enters it and loops forever.
   Always use the absolute binary path.

Start with Task 0.

---

## Context worth carrying over

Findings established during design, already reflected in the spec — no need to
re-derive them:

- `CLAUDE_CONFIG_DIR` fully isolates auth, and **also relocates `~/.claude.json`
  inside the config dir**. Verified with `claude auth status`.
- A config dir that is symlinks to `~/.claude` plus real `.credentials.json` and
  `.claude.json` reports `loggedIn: true`. The symlink farm works.
- Waybar 0.15 supports `menu` / `menu-file` / `menu-actions` (a GtkBuilder
  `GtkMenu`), plus per-module `signal` for flicker-free refresh.
- Waybar has **no hover-exec event**. The menu opens on click. This was checked
  and accepted, not overlooked.
- 219 top-level session transcripts, 402 MB; 218 have `cwd`, 186 have an
  `ai-title`; 12 project directories, all still existing.
- History extraction: regex on a 64 KB head, full re-read only on a miss —
  5.1 ms versus 17.1 ms for line-by-line JSON parsing. No cache, no index.
- Take the **first** `ai-title`, not the last: 176 files have several but only 4
  differ, and in those the first is the better label.
- Decode JSON string bodies with `json.loads(b'"' + raw + b'"')`. A
  `unicode_escape` round-trip mangles "Vojtěch" — this bug was found and fixed
  while writing the plan.
- Installed and available: `fuzzel`, `fzf`, `kitty`, `mako` / `notify-send`,
  `pytest`, `python3.14`. **Not** available: `jq`, `bats`, `shellcheck`.
