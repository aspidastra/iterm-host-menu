#!/usr/bin/env python3
"""setup_panes.py — make sure the 3 worker panes exist next to this one (iTerm2).

First run: splits the current pane into a 2x2 grid:
    +-----------------+-----------------+
    | this pane (menu)| Option 1        |
    +-----------------+-----------------+
    | Option 2        | Option 3        |
    +-----------------+-----------------+
Later runs: reuses the panes saved in the state file. If any were closed,
only those are recreated (split below this pane).
If the saved panes are in a different iTerm2 tab, nothing is created: it
exits (status 2) and tells you which tab to switch to.

Saves the pane IDs to the state file as PANE1_ID / PANE2_ID / PANE3_ID
(same format as the old setup_panes.sh, so existing saved IDs still work).

Run it directly, or import it and call ensure_panes().
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

STATE_FILE = Path(os.environ.get("PANES_STATE_FILE", Path.home() / ".iterm_menu_panes"))
PANE_KEYS = ("PANE1_ID", "PANE2_ID", "PANE3_ID")

SETUP_SCRIPT = r'''
-- {window index, tab index} of the pane with this id, or missing value.
on locateSession(theId)
    if theId is "" then return missing value
    tell application "iTerm2"
        repeat with wi from 1 to (count of windows)
            repeat with ti from 1 to (count of tabs of window wi)
                repeat with si from 1 to (count of sessions of tab ti of window wi)
                    if (id of session si of tab ti of window wi) is theId then return {wi, ti}
                end repeat
            end repeat
        end repeat
    end tell
    return missing value
end locateSession

-- Human-readable "tab 2 of window "name"" for a {window, tab} location.
on describeTab(loc)
    set wi to item 1 of loc
    set ti to item 2 of loc
    set winLabel to "window " & wi
    try
        tell application "iTerm2" to set winLabel to "window \"" & (name of window wi) & "\""
    end try
    return "tab " & ti & " of " & winLabel
end describeTab

on run argv
    set homeId to item 1 of argv
    set homeLoc to my locateSession(homeId)
    if homeLoc is missing value then error "Could not find the pane this script is running in."

    tell application "iTerm2"
        -- Take the reference straight from iTerm2 (not from a loop variable),
        -- so iTerm2 accepts commands such as "split" sent to it.
        set homeSession to current session of current window
        if (id of homeSession) is not homeId then error "The menu pane must be the focused pane when this runs. Click into it and run again."
    end tell

    -- Sort the 3 saved panes into: here (same tab), elsewhere (other tab), missing.
    set outIds to {"", "", ""}
    set missingIdx to {}
    set elsewhere to {}
    set elsewhereLoc to missing value
    repeat with i from 1 to 3
        set theId to item (i + 1) of argv
        set loc to my locateSession(theId)
        if loc is missing value then
            set end of missingIdx to i
        else if (item 1 of loc is not item 1 of homeLoc) or (item 2 of loc is not item 2 of homeLoc) then
            set end of elsewhere to ("Option " & i)
            set elsewhereLoc to loc
        else
            set item i of outIds to theId
        end if
    end repeat

    -- Saved panes live in another tab: stop and say where, without splitting anything.
    if (count of elsewhere) > 0 then
        set AppleScript's text item delimiters to ", "
        set paneList to elsewhere as text
        set AppleScript's text item delimiters to ""
        return "WRONG_TAB" & linefeed & paneList & linefeed & my describeTab(elsewhereLoc)
    end if

    tell application "iTerm2"
        if (count of missingIdx) is 3 then
            -- Nothing exists yet: build the 2x2 grid.
            tell homeSession to set topRight to (split vertically with default profile)
            tell homeSession to set bottomLeft to (split horizontally with default profile)
            tell topRight to set bottomRight to (split horizontally with default profile)
            set newPanes to {topRight, bottomLeft, bottomRight}
            repeat with i from 1 to 3
                set s to item i of newPanes
                tell s to set name to ("Option " & i)
                set item i of outIds to (id of s)
            end repeat
        else
            -- Some panes were closed: recreate just those, below the menu pane.
            -- Numbered loop: inside this tell, "contents of i" goes to iTerm2
            -- (sessions have a "contents" property) and fails with -1728.
            repeat with k from 1 to (count of missingIdx)
                set n to item k of missingIdx
                tell homeSession to set s to (split horizontally with default profile)
                tell s to set name to ("Option " & n)
                set item n of outIds to (id of s)
            end repeat
        end if
    end tell

    set out to ""
    repeat with theId in outIds
        set out to out & (contents of theId) & linefeed
    end repeat
    return out
end run
'''


class SetupError(Exception):
    pass


class WrongTabError(SetupError):
    """The saved panes exist, but in a different iTerm2 tab than this one."""


def run_osascript(script: str, *args: str) -> str:
    """Run an AppleScript (passed on stdin) with arguments; return its stdout."""
    proc = subprocess.run(
        ["osascript", "-", *args],
        input=script, text=True, capture_output=True,
    )
    if proc.returncode != 0:
        raise SetupError(f"osascript failed: {proc.stderr.strip()}")
    return proc.stdout


def read_state() -> list[str]:
    """Return the 3 saved pane IDs ("" for any that aren't saved)."""
    values = {}
    if STATE_FILE.is_file():
        for line in STATE_FILE.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep:
                values[key.strip()] = value.strip()
    return [values.get(k, "") for k in PANE_KEYS]


def write_state(ids: list[str]) -> None:
    STATE_FILE.write_text("".join(f"{k}={v}\n" for k, v in zip(PANE_KEYS, ids)))


def home_session_id() -> str:
    """ID of the pane this script runs in, from iTerm2's ITERM_SESSION_ID."""
    session = os.environ.get("ITERM_SESSION_ID", "")
    if os.environ.get("TERM_PROGRAM") != "iTerm.app" or not session:
        raise SetupError("must be run inside iTerm2.")
    # Looks like "w0t0p0:6A2B...". The part after ":" is AppleScript's "id of session".
    return session.split(":", 1)[-1]


def ensure_panes() -> list[str]:
    """Create any missing worker panes; return their IDs [Option 1, 2, 3].

    Raises WrongTabError if the saved panes are in a different iTerm2 tab.
    """
    home_id = home_session_id()
    out = run_osascript(SETUP_SCRIPT, home_id, *read_state())
    if out.startswith("WRONG_TAB"):
        lines = out.splitlines() + ["", "", ""]
        panes, where = lines[1], lines[2]
        raise WrongTabError(
            f"The {panes} pane(s) for this menu are in {where}, not in this tab.\n"
            "Switch to that iTerm2 tab and run the menu from there\n"
            "(or close those panes to create a new set in this tab)."
        )
    ids = out.split()
    if len(ids) != 3:
        raise SetupError(f"expected 3 pane IDs, got: {out!r}")
    write_state(ids)
    return ids


def main() -> int:
    try:
        ids = ensure_panes()
    except WrongTabError as e:
        print(e, file=sys.stderr)
        return 2
    except SetupError as e:
        print(f"setup_panes.py: {e}", file=sys.stderr)
        return 1
    print(f"Panes ready (saved to {STATE_FILE}):")
    for n, pane_id in enumerate(ids, 1):
        print(f"  Option {n}: {pane_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
