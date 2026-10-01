# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

An iTerm2 host menu: the focused pane (the "menu pane") lists the host aliases in `~/.ssh/config`, and picking one types commands into three worker panes ("Option 1..3") laid out as a 2x2 grid in the same tab. It is macOS-only and stdlib-only: Bash plus Python 3.9+ (the user runs 3.14). `menu_api.py` is the one exception and needs the `iterm2` package.

`HANDOFF.md` holds the full history, the reasons behind each design decision, what has been verified on the real Mac and what hasn't, and the latency work in progress. Read it before changing behaviour:

@HANDOFF.md

## Commands

```bash
./test_with_fakes.sh                  # offline end-to-end run of menu.sh, menu.py and menu_api.py
bash -n menu.sh setup_panes.sh        # syntax check
python3 -m py_compile *.py

./menu.sh            # or: python3 menu.py   (must run inside iTerm2, from the focused pane)
./setup_panes.sh     # or: python3 setup_panes.py   (create/reuse panes only)
MENU_TIMING=1 python3 menu.py         # per-pick timings (menu.sh timing needs bash 5+)
MENU_TIMING=1 ~/.venvs/iterm2/bin/python menu_api.py
```

`test_with_fakes.sh` is not an assertion suite. It puts a fake `osascript` and `ssh` on `PATH` and a fake `iterm2` module on `PYTHONPATH`, uses a throwaway `HOME` with a sample ssh config, and prints the commands each pane would receive plus the exit codes (expected values are printed next to them). You have to read the output to check it. It cannot test single cases; to check one scenario, copy its `run <mode> <cmd>` line. It never touches iTerm2, so real pane behaviour and real speed can only be checked by the user on their Mac.

Env overrides: `PANES_STATE_FILE` (default `~/.iterm_menu_panes`), `SSH_CONFIG` (default `~/.ssh/config`), `MENU_TIMING`.

## Architecture

There are two parallel implementations that are meant to behave the same:

- `setup_panes.sh` / `setup_panes.py` build or reuse the worker panes with one AppleScript run through `osascript`, then write `PANE1_ID..PANE3_ID` to the state file. Both versions read and write the same state file format. Exit codes: 0 ok, 1 error, 2 panes are in another tab. The Python version exposes `ensure_panes()`, which raises `WrongTabError` (exit 2) or `SetupError` (exit 1).
- `menu.sh` / `menu.py` run setup, parse the hosts, resolve the real address with `ssh -G <alias>`, and send all three pane commands in **one** `osascript` call (`send_to_panes`).
- `menu_api.py` reuses `menu.py` (host parsing, `pane_commands`, `choose`, `timing`) and `setup_panes.py` (pane creation). Only the per-pick send goes over iTerm2's Python API. It exists to compare speed with `menu.py`.

Invariants to keep:

- **The AppleScript is duplicated.** `setup_panes.sh` and `setup_panes.py` hold the same setup script, and `menu.sh` and `menu.py` hold the same `sendTo` script. Change one copy, change the other.
- The commands typed into panes are defined in two places: `pane_commands()` in `menu.py`, which `menu_api.py` also uses, and the `send_to_panes` call in `menu.sh`.
- In AppleScript, send commands such as `split` and `write text` only to numbered references (`session si of tab ti of window wi`) or to `current session of current window`. Never send them to `repeat with s in ...` loop variables, because iTerm2 rejected those (HANDOFF §5.1).
- A pane's identity is its session ID: the part of `ITERM_SESSION_ID` after the `:`, which equals AppleScript's `id of session`.
- Quote text sent to panes with POSIX single quotes (`shq` in Bash, `shlex.quote` in Python), not `printf %q`. `shq` always adds quotes and `shlex.quote` only when needed, so the two outputs differ slightly (e.g. `ping -c 5 '10.0.0.5'` vs `ping -c 5 10.0.0.5`).
- In Bash, split config values with `read -a`, not an unquoted `$var`, so that `Host *` isn't glob-expanded.

`menu_api-lard.py` is currently a byte-identical, non-executable copy of `menu_api.py`, and nothing references it.

## Working with the user

The user wants ambiguities and uncertainty flagged explicitly, and gaps not filled with guesses. When something can only be verified on the real Mac or in iTerm2, say so and update the verification checklist in `HANDOFF.md`.
