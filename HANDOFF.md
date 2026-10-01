# Handoff: iTerm2 multi-pane host menu

This file records what the scripts do, why they're built the way they are,
what has and hasn't been verified, and what's still open. It started as a
handoff from a claude.ai web chat; `CLAUDE.md` imports it.

---

## 1. Goal

From one iTerm2 pane (the **menu pane**), drive three other panes in the same tab:

```
+-----------------+-----------------+
| menu pane       | Option 1        |   Option 1: echo "I want to ssh to <host> -> ssh <host>"
+-----------------+-----------------+   Option 2: ping -c 5 <host's real address>
| Option 2        | Option 3        |   Option 3: nslookup <host's real address>
+-----------------+-----------------+
```

The menu lists the host aliases in `~/.ssh/config` (the number varies). Picking
one sends the three commands above into the three panes.

## 2. Environment

- **macOS + iTerm2.**
- **100% Python, using iTerm2's Python API** (decided 2026-10-01; see 7). No
  osascript/AppleScript and no Bash scripts remain.
- **Python:** user has **3.14.4**. Code stays compatible back to 3.9 via
  `from __future__ import annotations`.
- **iterm2 package:** 2.25 in a venv at `~/.venvs/iterm2`. Run the scripts with
  `~/.venvs/iterm2/bin/python` (the `python3` shebang usually has no `iterm2`).
  iTerm2 → Settings → General → Magic → "Enable Python API" must be on.
- **Shell in the worker panes:** assumed bash or zsh (commands use POSIX
  single-quoting via `shlex.quote`). fish would need different quoting.

## 3. Files

| File | What it is |
|---|---|
| `setup_panes.py` | `ITermConnection` (one API connection on a background thread) and `ensure_panes()`: creates/reuses the 3 worker panes, saves their IDs. Run directly: exit 0 ok, 1 error, 2 wrong tab. |
| `menu.py` | The host menu. Opens one `ITermConnection`, runs `ensure_panes`, then sends each pick's commands over the same connection. |
| `test_menu.py` | Offline unittest suite with a fake `iterm2` package (windows/tabs/panes, splits, names, sent text). Doesn't touch iTerm2. |

State file: `~/.iterm_menu_panes` (override with `PANES_STATE_FILE`):
```
PANE1_ID=<iTerm2 session id>
PANE2_ID=...
PANE3_ID=...
```
Same format as before the switch, so IDs saved by the old AppleScript version
still work (the API's `session_id` equals AppleScript's `id`; confirmed, see 6).
Other overrides: `SSH_CONFIG` (default `~/.ssh/config`), `MENU_TIMING=1`.
`PING_COUNT` is a constant (5) in `menu.py`.

## 4. How it works

### Connection (`ITermConnection` in `setup_panes.py`)
- Refuses to start unless `TERM_PROGRAM=iTerm.app` and `ITERM_SESSION_ID` is
  set. The menu pane's ID = the part of `ITERM_SESSION_ID` after the `:`.
- `iterm2.run_until_complete` runs on a **background daemon thread** and keeps
  the connection open until `close()`. The caller stays on the main thread
  (so the menu's blocking `input()` and Ctrl-C behave normally) and runs
  coroutines on the connection with `conn.run(coro)`
  (`asyncio.run_coroutine_threadsafe`).
- If it can't connect, the `iterm2` package prints its own help and calls
  `sys.exit(1)`; that `SystemExit` is caught on the thread and turned into a
  `SetupError` (exit 1).

### Pane setup (`ensure_panes`)
1. Finds the menu pane and each saved pane with `get_session_by_id(...,
   include_buried=False)` and `get_window_and_tab_for_session`.
2. Classifies each saved pane: same tab / other tab / missing (buried panes
   count as missing).
   - **Any in another tab →** `WrongTabError` ("tab N of window M"; indexes in
     `app.windows` / `window.tabs`), nothing split, exit **2**.
   - **All 3 missing →** 2×2 grid: split menu pane with a vertical divider →
     Option 1; split menu pane horizontally → Option 2; split Option 1
     horizontally → Option 3.
   - **Some missing →** recreates only those, split below the menu pane (the
     user is fine with that; no need to restore grid positions).
3. Names all 3 panes "Option n" (`async_set_name`) and overrides, for those
   panes only, the profile's title components to `SESSION_NAME`
   (`async_set_profile_properties`), so the title bar shows the name. Done on
   every run, so reused panes get fixed too.
4. Writes the 3 IDs to the state file.

The menu pane no longer has to be focused: the API splits a pane by ID, while
AppleScript could only split `current session of current window`.

### Menu (`menu.py`)
- Parses `~/.ssh/config` for `Host` lines: multiple aliases per line; wildcard
  and negated patterns (`*`, `?`, `!`) skipped; `Host=foo` form; `#` comments;
  case-insensitive keyword; `Include` followed (relative to `~/.ssh`, globs,
  loop guard); duplicates removed, file order kept.
- Menu entries: hosts + "Reload hosts" + "Quit"; redisplayed after each pick.
- Real address from `ssh -G <alias>` (`hostname` line), falling back to the
  alias.
- Sends the three commands concurrently (`asyncio.gather` of
  `async_send_text(cmd + "\n")`; the API types exactly the text given, so the
  `"\n"` is the Return). A pane that's gone prints "restart the menu to
  recreate it".

## 5. Decisions and the reasons behind them (don't undo without cause)

1. **Python API only.** Measured 37 ms vs 4.8 s per pick against osascript
   (see 7), and the API avoids AppleScript's quirks (decisions H1, H3).
2. **Connection on a background thread, menu on the main thread.** An earlier
   `menu_api.py` ran `input()` in an executor thread inside the event loop,
   where Ctrl-C at the prompt probably left the process waiting for Enter
   (inferred from how executor threads are joined at exit, not observed).
3. **No silent fallback.** If the API is unavailable, exit 1 with the reason,
   rather than quietly doing something slower.
4. **Pane identity = session ID, not window ID.** The user asked for "window
   IDs"; all four panes share one window and tab, so only session IDs
   distinguish them.
5. **`ssh -G` for addresses**: resolves `HostName`, `Include`, `Match` etc. the
   way ssh does, instead of re-implementing it.
6. **Plain single-quoting** (`shlex.quote`) of commands sent to panes; the arrow
   `→` was replaced with `->` to keep them plain ASCII.
7. **Wrong-tab check only at startup**, and only for panes that still exist;
   closed panes are simply recreated in the current tab.
8. **Per-pane title override, not a profile change.** The user's Default
   profile shows only the job in titles (`title_components=[JOB]`), so a set
   name was invisible (panes showed `-zsh`). Diagnosed via the API on the Mac
   (2026-10-01): `autoName` was "Option n" but `name` was "-zsh". Overriding
   title components per session fixed it (read back as "Option n") without
   touching the user's profile.

Historical (AppleScript era, kept for context; that code is gone):
- H1. iTerm2 rejected commands such as `split` sent to `repeat with s in ...`
  loop variables; numbered references (`session si of tab ti of window wi`) or
  `current session of current window` worked.
- H2. Bash: `for p in $rest` glob-expanded `Host *`; fixed with `read -a`.
- H3. Inside `tell application "iTerm2"`, `contents of i` failed with -1728
  because iTerm2's own `contents` property took over the word (reproduced on
  the Mac with a standalone script).

## 6. Verification status

**Offline (`python3 -m unittest -v`, fake `iterm2`), all passing:**
- Pane setup: 2×2 grid splits (parent, divider direction), titles (the fake
  shows "-zsh" until the title override is applied), state file; reuse on
  second run; reused panes get titles; one and two closed panes recreated; wrong tab →
  `WrongTabError` with "tab 2 of window 2" and no splits; refusal outside
  iTerm2; missing package; connection failure; `setup_panes.main` exit codes.
- Menu: host parsing on a sample config → `web1, web2, db, lab-box`;
  `ssh -G` parsing and fallback; quoting; a pick sends the 3 commands with
  `"\n"`; invalid menu number ignored; EOF quits; pane closed mid-session
  reported; wrong tab → exit 2; connection failure → exit 1.
- `menu.py` and `setup_panes.py` import with the real `iterm2` 2.25.

**Confirmed by the user on their Mac:**
- [x] Python 3.14.4; `ITERM_SESSION_ID` suffix == AppleScript `id of session`
      (2026-09-30).
- [x] API `session_id` == AppleScript `id`: the API found panes that the
      AppleScript version had saved (2026-10-01).
- [x] API send is fast: 37 ms for 3 panes (2026-10-01, old `menu_api.py`).

**Confirmed with the all-Python version (user, 2026-10-01):**
- [x] Fresh 2×2 grid has the intended layout.
- [x] Recreating a closed pane works (new pane appears below the menu pane).
- [x] Wrong-tab detection with real panes in two tabs (exit 2).
- [x] A full pick: commands land in the right panes and run.
- [x] Quit and Ctrl-C exit cleanly.
- [ ] Panes show the titles "Option 1..3". **Failed at first** (titles showed
      `-zsh`; see decision 8). After the per-pane override, the API reads the
      titles back as "Option n" on the live panes; the user still has to
      confirm the title bars show them with the updated `setup_panes.py`.

## 7. How to run

```bash
~/.venvs/iterm2/bin/python menu.py           # the menu
~/.venvs/iterm2/bin/python setup_panes.py    # panes only; prints IDs
MENU_TIMING=1 ~/.venvs/iterm2/bin/python menu.py   # per-step timings
python3 -m unittest -v                       # offline tests (any python3)
```
Start the menu from the pane that should become the top-left "menu pane". To
start over, close the Option panes (or delete `~/.iterm_menu_panes`).

**Latency history.** The user first saw ~0.5 s per pick with osascript (one
launch per pane). Batching into one launch and timing it gave, on 2026-10-01,
one pick each: **osascript 4806 ms** vs **Python API 37 ms** to send to 3
panes; `ssh -G` took ~150–320 ms. Why osascript was that slow was never
determined (one sample). That led to the API, first as the send transport,
then for everything.

## 8. Open ideas / possible next steps (not requested yet)

- Make Option 1 actually connect: change the Option 1 entry in
  `pane_commands` to `ssh <host>`.
- Re-check the tab on every menu pick (currently startup only).
- On wrong tab, offer to switch to it (`async_activate` on the tab/window)
  instead of exiting.
- `ssh -G` is now most of the per-pick time; it could be cached per host or run
  concurrently with the echo to Option 1.

## 9. Conversation history (condensed)

1. Started from a basic Bash `select` menu sending `echo`s to name-matched
   panes via osascript.
2. Auto-created ("fanned out") the 3 panes and saved their IDs (state file).
3. "doesn't understand split vertically" error → numbered references (H1).
4. Menu rebuilt around `~/.ssh/config` hosts: echo / ping / nslookup.
5. Converted to Python; Bash versions kept for a while.
6. Added the wrong-tab exit.
7. User confirmed Python 3.14.4, grid splits, `ITERM_SESSION_ID` matching.
8. ~0.5 s delay → batched osascript, `MENU_TIMING`, `menu_api.py` for comparison.
9. Timings: API 37 ms vs osascript 4.8 s. API became `menu.py`'s main transport
   (PR #1); `menu_api.py` folded in.
10. Recreating a closed pane failed in AppleScript (H3); fixed, then the user
    chose to go **100% Python**: pane setup moved to the API, Bash scripts,
    osascript and `test_with_fakes.sh`/`compare_timing.sh` removed, replaced by
    `test_menu.py`.
11. On the Mac everything worked except pane titles (showed `-zsh`, because
    the Default profile's title shows only the job). Fixed with a per-pane
    title-components override (decision 8).

User preference noted throughout: flag ambiguities and uncertainty explicitly,
prioritise accuracy, don't fill gaps with guesses.
