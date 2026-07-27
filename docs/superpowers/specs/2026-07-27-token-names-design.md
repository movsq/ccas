# Token names in the UI

The panel's colour chips are labelled with the raw format token — `%5hused`,
`%7dquotaleft`. That is the string you type into a format string, not the name
of a thing you are colouring: the chip row reads as syntax where it should read
as a list of the parts of the label.

## The table

`format.NAMES`, a dict beside `format.TOKENS` and with exactly its keys. Pure
data — `format.py` stays I/O-free because `ccs statusline` reaches it.

| token | name | token | name |
|---|---|---|---|
| `%name` | nickname | `%5h` | usage clock |
| `%email` | email | `%7d` | usage clock |
| `%index` | index | | |
| `%5hused` | 5h used | `%7dused` | 7d used |
| `%5hquotaleft` | 5h left | `%7dquotaleft` | 7d left |
| `%5hreset` | 5h reset | `%7dreset` | 7d reset |
| `%5htimeleft` | 5h remaining | `%7dtimeleft` | 7d remaining |

`%5h` and `%7d` share a name because they share `_smart`: `usage.bar()` already
decides which window the label mentions, so they are two spellings of one
behaviour, which is what the comment above `TOKENS` already says.

A test asserts `set(NAMES) == set(TOKENS)`, so a token cannot ship nameless.

## Where the names show

- **The panel's chip row.** `panel.color_targets` sets `"label"` to the name
  instead of the token. The decision stays in `panel.py`; `panel_ui.py` renders
  the list it is handed and does not change. `"token"` is untouched, so what is
  written is untouched.
- **`ccs format <slug> --edit`.** A name column ahead of the rendered sample.
  The token stays, because that is what the prompt below the table wants typed.
- **`ccs format --tokens`.** Token then name. It is the "what can I type"
  reference and a bare list answers half the question.

The icon chip keeps its `"icon"` label — it is not a token and has no entry.

## What does not change

Nothing is stored. A name is display only, so there is no registry field, no
new value to tolerate on read, and nothing to migrate.
