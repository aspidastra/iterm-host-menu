# Handoff: iTerm2 multi-pane host menu

This file hands off work started in a claude.ai web chat so it can continue in
Claude Code on the user's Mac. It records what the scripts do, why they're built
the way they are, what has and hasn't been verified, and what's still open.

> Tip: to have Claude Code load this automatically in this folder, copy it to
> `CLAUDE.md` (or add a line `@HANDOFF.md` to an existing `CLAUDE.md`).

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

- **macOS + iTerm2** (confirmed: the scripts split and control iTerm2 panes on
  the user's Mac).
- **Pane control:** `osascript` (AppleScript) → iTerm2. No extra installs.
- **Python version:** user has **Python 3.14.4** (confirmed). Code is stdlib
  only and also stays compatible back to 3.9 (via
  `from __future__ import annotations`); that line is harmless on 3.14.
- **Shell in the worker panes:** assumed bash or zsh (the commands typed into
  them use POSIX single-quoting). fish would need different quoting.

## 3. Files

| File | What it is |
|---|---|
| `setup_panes.sh` | Creates/reuses the 3 worker panes, saves their IDs. Exit 0 ok, 1 error, 2 wrong tab. |
| `menu.sh` | Bash host menu. Calls `setup_panes.sh` first. |
| `setup_panes.py` | Python port of `setup_panes.sh`. Importable: `ensure_panes()`. |
| `menu.py` | Python port of `menu.sh`. Imports `ensure_panes` from `setup_panes.py`. |
| `menu_api.py` | Same menu as `menu.py`, but sends via iTerm2's Python API (persistent connection). Speed comparison; see 7a. |
| `test_with_fakes.sh` | Runs all three menus end to end with fake `osascript`/`ssh`/`iterm2`. Doesn't touch iTerm2. |
| `compare_timing.sh` | Sets up the `iterm2` venv if needed, times bare `osascript` vs. one iTerm2 Apple Event (5 runs each), then runs `menu.py` and `menu_api.py` with `MENU_TIMING=1`. See 7a. |

The Bash and Python versions are meant to behave identically. **Both
`setup_panes.*` files contain the same AppleScript** — if you change it in one,
change it in the other (it was copied over by script to keep them identical).

State file: `~/.iterm_menu_panes` (override with `PANES_STATE_FILE`). Format,
shared by both versions:
```
PANE1_ID=<iTerm2 session id>
PANE2_ID=...
PANE3_ID=...
```
Other overrides: `SSH_CONFIG` (default `~/.ssh/config`). `PING_COUNT` is a
constant (5) in each menu script.

## 4. How it works

### Pane setup (`setup_panes.*`)
1. Refuses to run unless `TERM_PROGRAM=iTerm.app` and `ITERM_SESSION_ID` is set.
2. The menu pane's ID = the part of `ITERM_SESSION_ID` after the `:`
   (`w0t0p0:<UUID>` → `<UUID>`). This equals AppleScript's `id of session`
   (confirmed by the user on their Mac).
3. AppleScript (`locateSession`) finds the `{window index, tab index}` of the
   menu pane and of each saved pane ID, by looping with **numbered references**
   (`session si of tab ti of window wi`).
4. Requires the menu pane to be `current session of current window` (i.e.
   focused) — needed because splits are sent to that reference.
5. Classifies each saved pane: same tab / other tab / missing.
   - **Any in another tab →** returns `WRONG_TAB\n<pane list>\n<tab description>`
     on stdout without splitting; the shell/Python side prints a "switch to that
     tab" message and exits **2**.
   - **All 3 missing →** builds the 2×2 grid (split menu pane vertically →
     Option 1; split menu pane horizontally → Option 2; split Option 1
     horizontally → Option 3) and names them "Option 1..3".
   - **Some missing →** recreates only those by splitting the menu pane
     horizontally (layout gets less tidy; acceptable).
6. Returns the 3 IDs, which are written to the state file.

### Menu (`menu.*`)
- Parses `~/.ssh/config` for `Host` lines:
  - multiple aliases per line → each listed;
  - wildcard/negated patterns (`*`, `?`, `!`) skipped;
  - `Host=foo` form accepted; `#` comments stripped; keyword case-insensitive;
  - `Include` followed (relative to `~/.ssh`, globs expanded; Python version
    also guards against include loops);
  - duplicates removed, file order kept.
- Menu entries: hosts + "Reload hosts" + "Quit". List is redisplayed after each pick.
- For the picked alias, gets the real address with `ssh -G <alias>` (the
  `hostname` line), falling back to the alias. Ping and nslookup use that
  address, because config-only aliases usually aren't resolvable.
- Sends commands via AppleScript `write text` to the pane found by saved ID.
  If the pane is gone it prints "restart the menu to recreate it".

## 5. Decisions and the reasons behind them (don't undo without cause)

1. **Numbered references, not loop variables.** First version found panes with
   `repeat with s in sessions of t` and then did `tell s to split ...`. The user
   got: `... doesn't understand the "split vertically with default profile"
   message.` iTerm2's scripting docs confirm that command syntax is valid, so the
   *probable* cause is that loop-built references don't accept commands (reading
   `id of s` worked). Fix: use `current session of current window` for the menu
   pane and `session si of tab ti of window wi` for lookups. **The user
   confirmed the grid splits now work.** (The first part of the original error
   was cut off, so the root-cause explanation is still inferred, but the fix is
   proven.)
2. **Pane identity = session ID, not window ID.** The user asked for "window IDs";
   in iTerm2 all four panes share one window and one tab, so only session IDs
   distinguish them.
3. **`ssh -G` for addresses** — handles `HostName`, `Include`, `Match` etc. the
   way ssh itself does, instead of re-implementing resolution.
4. **`read -a` for splitting config values in Bash.** An earlier `for p in $rest`
   glob-expanded a bare `Host *` into filenames from the current directory
   (caught in testing, fixed).
5. **Plain single-quoting of commands sent to panes** (`shq` in Bash,
   `shlex.quote` in Python). `printf %q` produced `$'...'` escapes for non-ASCII,
   which is ugly and not portable; the arrow `→` was replaced with `->`.
6. **Wrong-tab check only at startup**, and it only triggers for panes that still
   exist; closed panes are simply recreated in the current tab.

## 6. Verification status

**Verified (in a Linux sandbox, with fakes):**
- Bash syntax (`bash -n`) and Python compile for all scripts.
- Host parsing against a sample config (comments, multi-alias, `Host=db`,
  wildcards, `Include`) → `web1, web2, db, lab-box`.
- End-to-end flow of both versions via `test_with_fakes.sh`: IDs saved and
  reused, exact pane commands, invalid menu numbers rejected, wrong-tab exit 2,
  osascript error exit 1 with iTerm2's message shown, refusal outside iTerm2.

**Confirmed by the user on their Mac (2026-09-30):**
- [x] Python is 3.14.4.
- [x] Grid splits work (the AppleScript runs against real iTerm2, and the fix in
      decision 1 works).
- [x] `ITERM_SESSION_ID` suffix == AppleScript `id of session`.
- [x] `name of window` works (used in the wrong-tab message).

**Still to confirm:**
- [ ] Panes show the titles "Option 1..3". This is likely fine, because the
      naming runs in the same script right after the splits, but no one has
      said so explicitly.
- [ ] Wrong-tab detection with real panes in two tabs: run the menu once in
      tab A, then start it in tab B and expect the "switch to tab" message
      with exit code 2.
- [ ] A full menu pick with real hosts: the echo, ping and nslookup land in
      the right panes, and `ssh -G` returns the expected addresses.
- [ ] Recreating a single closed pane (close Option 2, rerun the menu).

## 7. How to run

```bash
chmod +x *.sh *.py
./menu.sh            # or: python3 menu.py
./setup_panes.sh     # or: python3 setup_panes.py  (panes only; prints IDs)
MENU_TIMING=1 python3 menu.py   # print per-step timings
./test_with_fakes.sh # offline test of all three menus
```
Start the menu from the pane that should become the top-left "menu pane", with
that pane focused. To start over, close the Option panes (or delete
`~/.iterm_menu_panes`).

## 7a. Latency work (in progress)

**Observation (user, 2026-10-01):** about 0.5 s between choosing a host in the
menu and the commands appearing in the panes.

**Likely causes:** these were inferred, not measured on the Mac.
- Each pick used to launch `osascript` three times, once per pane. Every launch
  starts a process, compiles the AppleScript from source, and sends Apple
  Events to iTerm2.
- `ssh -G` runs before anything is sent. It is usually fast, but options such
  as `CanonicalizeHostname` or `Match exec` in the config could slow it down.

**Changes made:**
1. All three pane commands now go out in **one** `osascript` call
   (`send_to_panes` in `menu.sh` and `menu.py`, AppleScript `sendTo` handler
   called once per id/command pair).
2. `MENU_TIMING=1` prints how long the `ssh -G` lookup, the send and the total
   took. In `menu.sh` this needs bash 5+ (`$EPOCHREALTIME`), so it is silently
   skipped under macOS's built-in bash 3.2. `menu.py` always supports it.
3. New `menu_api.py`: the same menu, but it sends through **iTerm2's Python
   API** over one persistent connection, with the three sends run concurrently
   (`asyncio.gather`). Pane creation still uses `setup_panes.py` (osascript,
   once at startup), so the timing comparison only covers the per-pick send.
   It imports host parsing, `pane_commands` and `timing` from `menu.py`.
   - Needs: iTerm2 → Settings → General → Magic → "Enable Python API"; a venv
     with `pip install iterm2` (PyPI latest checked: 2.25, Sept 2026); iTerm2
     may ask to allow the first connection.
   - At startup it checks that the API can find each saved pane ID, which
     assumes API `session_id` == AppleScript `id`. **Unverified;** if it fails
     the script says so.
   - It sends `cmd + "\n"` because `async_send_text` doesn't add Return the way
     AppleScript `write text` does. **Unverified** on the user's shell.

**Results (user's Mac, 2026-10-01, one pick each):**

| | send to 3 panes | total incl. `ssh -G` |
|---|---|---|
| `menu_api.py` (Python API) | **37 ms** | 185 ms |
| `menu.py` (one batched osascript) | **4806 ms** | 5128 ms |

- The Python API is far faster. It also confirms that API `session_id` matches
  AppleScript `id`, since the API found the saved panes.
- `ssh -G` takes about 150 to 320 ms, which is most of what's left in the API
  total.
- The osascript time of 4.8 s is much worse than the 0.5 s the user first
  noticed. With only one sample, the cause is **unknown**. Possible causes
  include a cold first call, a slow Apple Event round trip for each pane
  lookup, or something else. If osascript is kept, time it several times
  before drawing conclusions.
  `compare_timing.sh` now also times a bare `osascript` and a single
  iTerm2 Apple Event to help separate those causes (not yet run).

**Next:** likely make the Python API the main transport (see section 8). That
is not started; waiting for the user's decision.

## 8. Open ideas / possible next steps (not requested yet)

- Make Option 1 actually connect: change the Option 1 entry in `pane_commands`
  (`menu.py`) or the `send_to_panes` call (`menu.sh`) to `ssh <host>`.
- Re-check the tab on every menu pick (currently startup only).
- When the wrong-tab case is detected, offer to switch to that tab automatically
  (AppleScript `select` on the tab/window) instead of just exiting.
- If the API proves faster, move pane setup to the API too (`async_split_pane`,
  `async_set_name`) and drop osascript from the Python path.
- Consider keeping only one implementation to avoid the duplicated AppleScript.

## 9. Conversation history (condensed)

1. Started from a basic Bash `select` menu (Option 1/2/3/Quit). Asked to send
   each option's `echo` to a pane named after the option via osascript →
   name-matched panes (iTerm2 assumed).
2. Asked to auto-create ("fan out") the 3 panes if missing and save IDs for later
   runs → `setup_panes.sh` + state file; menu targets panes by ID.
3. User hit the "doesn't understand split vertically" error → switched to
   iTerm2-issued / numbered references (decision 1).
4. Menu rebuilt around `~/.ssh/config` hosts: pane 1 echoes the ssh intent,
   pane 2 pings 5×. Then pane 3 runs nslookup.
5. Converted both scripts to Python (user wrote "setup_menu.sh"; taken to mean
   `setup_panes.sh`). Bash versions kept.
6. Added: exit and say which tab to switch to when run from a tab other than the
   one holding the Option panes.
7. Handoff written. User then confirmed Python 3.14.4, working grid splits,
   `ITERM_SESSION_ID` matching, and `name of window`.
8. User saw a delay of about 0.5 s per pick. Sends were batched into one
   osascript call, `MENU_TIMING` was added, and `menu_api.py` (iTerm2 Python
   API) was written to compare speed (section 7a).

User preference noted throughout: flag ambiguities and uncertainty explicitly,
prioritise accuracy, don't fill gaps with guesses.
