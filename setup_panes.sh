#!/usr/bin/env bash
# setup_panes.sh — make sure the 3 worker panes exist next to this one (iTerm2).
#
# First run: splits the current pane into a 2x2 grid:
#     +-----------------+-----------------+
#     | this pane (menu)| Option 1        |
#     +-----------------+-----------------+
#     | Option 2        | Option 3        |
#     +-----------------+-----------------+
# Later runs: reuses the panes saved in the state file. If any were closed,
# only those are recreated (split below this pane).
# If the saved panes are in a different iTerm2 tab, nothing is created: it
# exits (status 2) and tells you which tab to switch to.
#
# Saves the pane IDs to $STATE_FILE as PANE1_ID / PANE2_ID / PANE3_ID
# and prints them.

STATE_FILE="${PANES_STATE_FILE:-$HOME/.iterm_menu_panes}"

if [[ "$TERM_PROGRAM" != "iTerm.app" || -z "$ITERM_SESSION_ID" ]]; then
    echo "setup_panes.sh: must be run inside iTerm2." >&2
    exit 1
fi

# ITERM_SESSION_ID looks like "w0t0p0:6A2B...". The part after ":" is the
# same ID that AppleScript reports as "id of session".
HOME_ID="${ITERM_SESSION_ID#*:}"

PANE1_ID=""; PANE2_ID=""; PANE3_ID=""
# shellcheck disable=SC1090
[[ -f "$STATE_FILE" ]] && source "$STATE_FILE"

out=$(osascript - "$HOME_ID" "$PANE1_ID" "$PANE2_ID" "$PANE3_ID" 2>&1 <<'APPLESCRIPT'
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
APPLESCRIPT
) || { echo "setup_panes.sh: osascript failed: $out" >&2; exit 1; }

if [[ "$out" == WRONG_TAB* ]]; then
    { read -r _; read -r panes; read -r where; } <<< "$out"
    echo "The $panes pane(s) for this menu are in $where, not in this tab." >&2
    echo "Switch to that iTerm2 tab and run the menu from there" >&2
    echo "(or close those panes to create a new set in this tab)." >&2
    exit 2
fi

# IDs are UUIDs (no spaces), so word-splitting is safe here.
ids=( $out )
if [[ ${#ids[@]} -ne 3 ]]; then
    echo "setup_panes.sh: expected 3 pane IDs, got: $out" >&2
    exit 1
fi

cat > "$STATE_FILE" <<EOF
PANE1_ID=${ids[0]}
PANE2_ID=${ids[1]}
PANE3_ID=${ids[2]}
EOF

echo "Panes ready (saved to $STATE_FILE):"
echo "  Option 1: ${ids[0]}"
echo "  Option 2: ${ids[1]}"
echo "  Option 3: ${ids[2]}"
