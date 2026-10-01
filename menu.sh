#!/usr/bin/env bash
# Host menu: pick a host from ~/.ssh/config, then
#   pane "Option 1" echoes that you want to ssh to it
#   pane "Option 2" pings it 5 times
#   pane "Option 3" runs nslookup on it
# Set MENU_TIMING=1 to print how long each step takes (bash 5+ only).
# Panes are created on first run (via setup_panes.sh) and targeted by saved ID.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="${PANES_STATE_FILE:-$HOME/.iterm_menu_panes}"
SSH_CONFIG="${SSH_CONFIG:-$HOME/.ssh/config}"
PING_COUNT=5

"$SCRIPT_DIR/setup_panes.sh" || exit $?
# shellcheck disable=SC1090
source "$STATE_FILE"
PANE_IDS=("$PANE1_ID" "$PANE2_ID" "$PANE3_ID")

# ---------------------------------------------------------------------------
# send_to_panes <n> <command> [<n> <command> ...]
# Types each <command> into pane "Option <n>". All commands go out in ONE
# osascript call: starting osascript is the slow part, so batching them keeps
# the delay to a single launch instead of one launch per pane.
send_to_panes() {
    local args=() ns=() result line i=0
    while [[ $# -ge 2 ]]; do
        ns+=("$1")
        args+=("${PANE_IDS[$(($1 - 1))]}" "$2")
        shift 2
    done

    result=$(osascript - "${args[@]}" 2>&1 <<'APPLESCRIPT'
-- Type theCommand into the pane with id targetId. Returns "sent" or "not found".
on sendTo(targetId, theCommand)
    tell application "iTerm2"
        repeat with wi from 1 to (count of windows)
            repeat with ti from 1 to (count of tabs of window wi)
                repeat with si from 1 to (count of sessions of tab ti of window wi)
                    if (id of session si of tab ti of window wi) is targetId then
                        tell session si of tab ti of window wi to write text theCommand
                        return "sent"
                    end if
                end repeat
            end repeat
        end repeat
    end tell
    return "not found"
end sendTo

-- argv is: id1 cmd1 id2 cmd2 ... ; prints one result line per pair.
on run argv
    set out to ""
    repeat with k from 1 to (count of argv) by 2
        set out to out & my sendTo(item k of argv, item (k + 1) of argv) & linefeed
    end repeat
    return out
end run
APPLESCRIPT
) || { echo "  ✗ osascript failed: $result"; return 1; }

    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        if [[ "$line" == "sent" ]]; then
            echo "  → sent to Option ${ns[$i]}"
        else
            echo "  ✗ pane for Option ${ns[$i]} is gone — restart the menu to recreate it"
        fi
        i=$((i + 1))
    done <<< "$result"
}

# Timing (MENU_TIMING=1): prints how long each step took. Needs bash 5+
# ($EPOCHREALTIME); macOS's built-in bash 3.2 lacks it, so timing is skipped there.
now_ms() {
    [[ -n "$EPOCHREALTIME" ]] || { echo ""; return; }
    local t="${EPOCHREALTIME/[.,]/}"
    echo "${t%???}"
}
timing() {  # timing <label> <start_ms>
    [[ -n "$MENU_TIMING" && -n "$2" ]] || return 0
    echo "  [timing] $1: $(( $(now_ms) - $2 )) ms"
}

# ---------------------------------------------------------------------------
# list_hosts <config file>
# Prints each concrete host alias from "Host" lines, one per line.
# - A line can hold several aliases ("Host web1 web2") — each is listed.
# - Wildcard / negated patterns (*, ?, !) are skipped: you can't ssh to them.
# - "Include" files are followed (relative paths are under ~/.ssh).
list_hosts() {
    local file="$1"
    [[ -r "$file" ]] || return 0

    local line key words pattern inc
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%#*}"                      # strip comments
        line="${line//=/ }"                     # allow "Host=foo" form
        read -r -a words <<< "$line"            # read -a splits without glob-expanding
        [[ ${#words[@]} -eq 0 ]] && continue
        key="${words[0]}"
        case "$(tr '[:upper:]' '[:lower:]' <<< "$key")" in
            host)
                for pattern in "${words[@]:1}"; do
                    case "$pattern" in
                        *'*'*|*'?'*|'!'*) ;;    # skip patterns
                        *) echo "$pattern" ;;
                    esac
                done
                ;;
            include)
                for pattern in "${words[@]:1}"; do
                    [[ "$pattern" == "~/"* ]] && pattern="$HOME/${pattern#\~/}"
                    [[ "$pattern" != /* ]] && pattern="$HOME/.ssh/$pattern"
                    for inc in $pattern; do     # unquoted: expand globs
                        [[ -f "$inc" ]] && list_hosts "$inc"
                    done
                done
                ;;
        esac
    done < "$file"
}

# shq <text> — wrap text in single quotes so bash/zsh in the pane take it literally
shq() {
    local q="'\\''"
    printf "'%s'" "${1//\'/$q}"
}

# resolve_hostname <alias> — the real HostName ssh would use (falls back to alias)
resolve_hostname() {
    local h
    h=$(ssh -G "$1" 2>/dev/null | awk '$1 == "hostname" { print $2; exit }')
    echo "${h:-$1}"
}

load_hosts() {
    HOSTS=()
    local h
    while IFS= read -r h; do
        HOSTS+=("$h")
    done < <(list_hosts "$SSH_CONFIG" | awk '!seen[$0]++')   # de-duplicate, keep order
}

# ---------------------------------------------------------------------------
load_hosts
if [[ ${#HOSTS[@]} -eq 0 ]]; then
    echo "No hosts found in $SSH_CONFIG"
    exit 1
fi

PS3=$'\nWhich host do you want to work with? '
while true; do
    echo
    echo "=== Hosts in $SSH_CONFIG ==="
    select opt in "${HOSTS[@]}" "Reload hosts" "Quit"; do
        case "$opt" in
            "Quit")
                exit 0
                ;;
            "Reload hosts")
                load_hosts
                echo "Loaded ${#HOSTS[@]} host(s)."
                ;;
            "")
                echo "invalid option $REPLY"
                continue
                ;;
            *)
                host="$opt"
                t0=$(now_ms)
                target="$(resolve_hostname "$host")"
                timing "ssh -G lookup" "$t0"
                echo "Selected: $host ($target)"
                t1=$(now_ms)
                send_to_panes \
                    1 "echo $(shq "I want to ssh to $host  ->  ssh $host")" \
                    2 "ping -c $PING_COUNT $(shq "$target")" \
                    3 "nslookup $(shq "$target")"
                timing "send to 3 panes (one osascript)" "$t1"
                timing "total" "$t0"
                ;;
        esac
        break   # leave select so the host list is shown again
    done
done
