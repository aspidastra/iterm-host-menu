# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

An iTerm2 host menu: the menu pane lists the host aliases in `~/.ssh/config`, and picking one types commands into three worker panes ("Option 1..3") laid out as a 2x2 grid in the same tab. It is macOS-only and 100% Python (3.9+; the user runs 3.14). Everything goes through iTerm2's Python API (the `iterm2` package, installed in `~/.venvs/iterm2`). There is no osascript/AppleScript and no Bash; don't reintroduce them without asking.

`HANDOFF.md` holds the full history, the reasons behind each design decision, what has been verified on the real Mac and what hasn't, and the open ideas. Read it before changing behaviour:

@HANDOFF.md

## Commands

```bash
python3 -m unittest -v                         # offline tests (fake iterm2; works without the real package)
python3 -m unittest test_menu.SetupPanesTest.test_closed_pane_is_recreated_below_menu_pane   # one test

~/.venvs/iterm2/bin/python menu.py             # the menu (inside iTerm2)
~/.venvs/iterm2/bin/python setup_panes.py      # create/reuse panes only
MENU_TIMING=1 ~/.venvs/iterm2/bin/python menu.py   # per-step timings
```

`test_menu.py` installs a fake `iterm2` module in `sys.modules` before importing the scripts. It models windows → tabs → panes, splits (parent and divider direction), names and sent text. When you use a new `iterm2` API call, add it to the fake. The tests never touch iTerm2, so real layout, pane titles and whether commands actually run can only be checked by the user on their Mac.

Env overrides: `PANES_STATE_FILE` (default `~/.iterm_menu_panes`), `SSH_CONFIG` (default `~/.ssh/config`), `MENU_TIMING`.

## Architecture

- `setup_panes.py` holds `ITermConnection`, which runs `iterm2.run_until_complete` on a background daemon thread and keeps the connection open. Callers stay on the main thread and use `conn.run(coro)` (`asyncio.run_coroutine_threadsafe`). It also holds `ensure_panes(conn)`, which finds, creates or recreates the panes and writes `PANE1_ID..PANE3_ID` to the state file.
- `menu.py` opens one `ITermConnection`, calls `ensure_panes`, then for each pick resolves the address with `ssh -G <alias>` and sends the 3 commands concurrently over that same connection (`send_to_panes`).
- Exit codes for both scripts: 0 ok, 1 error (`SetupError`), 2 panes are in another tab (`WrongTabError`).

Things to keep:

- Anything blocking (`input()`, `subprocess`) stays on the main thread. Anything that touches `conn.app` runs as a coroutine via `conn.run`, because the app object is updated on the connection's thread.
- `iterm2` calls `sys.exit(1)` when it can't connect. `ITermConnection._run` catches that `SystemExit` on the thread, and `__init__` turns it into a `SetupError`. Keep it that way, or a failure on the thread goes unnoticed. Don't fall back silently to anything slower.
- `async_send_text` doesn't press Return, so commands are sent with a trailing `"\n"`.
- A pane's identity is its session ID: the part of `ITERM_SESSION_ID` after the `:`.
- Look panes up with `include_buried=False`; buried panes count as missing.
- Quote text sent to panes with `shlex.quote`.

## Working with the user

The user wants ambiguities and uncertainty flagged explicitly, and gaps not filled with guesses. When something can only be verified on the real Mac or in iTerm2, say so and update the checklist in `HANDOFF.md` §6.
