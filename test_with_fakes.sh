#!/usr/bin/env bash
# test_with_fakes.sh — exercise menu.sh / menu.py / menu_api.py without touching iTerm2.
# Puts fake `osascript` and `ssh` first on PATH, a fake `iterm2` Python module on
# PYTHONPATH, and uses a throwaway HOME with a sample ~/.ssh/config.
# Real iTerm2 behaviour (and real speed) is NOT covered by this.
#
# Usage: ./test_with_fakes.sh
set -u
cd "$(dirname "$0")"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/home/.ssh/conf.d"

# --- fake osascript ---------------------------------------------------------
# Setup script (contains "locateSession"): behaviour depends on $FAKE_MODE.
# Send script (batched id/cmd pairs): logs each launch and pair, replies "sent" per pair.
cat > "$T/bin/osascript" <<'EOF'
#!/usr/bin/env bash
script=$(cat)
if grep -q locateSession <<< "$script"; then
  case "${FAKE_MODE:-ok}" in
    wrongtab) printf 'WRONG_TAB\nOption 1, Option 2, Option 3\ntab 2 of window "servers"\n' ;;
    fail) echo "execution error: The menu pane must be the focused pane when this runs. (-2700)" >&2; exit 1 ;;
    *) printf 'AAA-111\nBBB-222\nCCC-333\n' ;;
  esac
else
  shift  # drop the "-"
  echo "(osascript launch)" >> "$FAKE_LOG"
  while [[ $# -ge 2 ]]; do
    echo "pane=$1 cmd=$2" >> "$FAKE_LOG"; echo sent
    shift 2
  done
fi
EOF

# --- fake iterm2 Python package (for menu_api.py) ---------------------------
mkdir -p "$T/py/iterm2"
cat > "$T/py/iterm2/__init__.py" <<'EOF'
import asyncio, os
class _Session:
    def __init__(self, sid): self.session_id = sid
    async def async_send_text(self, text, suppress_broadcast=False):
        with open(os.environ["FAKE_LOG"], "a") as f:
            f.write(f"api pane={self.session_id} text={text!r}\n")
class _App:
    def get_session_by_id(self, sid, include_buried=True):
        return _Session(sid)
async def async_get_app(connection, create_if_needed=True):
    return _App()
def run_until_complete(coro, retry=False):
    asyncio.run(coro(None))
EOF

# --- fake ssh (only `ssh -G <alias>` is used) --------------------------------
cat > "$T/bin/ssh" <<'EOF'
#!/usr/bin/env bash
[[ "$2" == "web1" ]] && echo "hostname 10.0.0.5" || echo "hostname $2"
EOF
chmod +x "$T/bin/"*

# --- sample ssh config -------------------------------------------------------
cat > "$T/home/.ssh/config" <<'EOF'
# comment
Host web1 web2   # two aliases
    HostName 10.0.0.5
host=db
  User admin
Host *.internal !bad
Host *
  ServerAliveInterval 30
Include conf.d/*
EOF
printf 'Host lab-box\n  HostName 192.168.1.9\nHost web1\n' > "$T/home/.ssh/conf.d/extra"

run() {  # run <mode> <command...>
  HOME="$T/home" PATH="$T/bin:$PATH" TERM_PROGRAM=iTerm.app \
  ITERM_SESSION_ID=w0t0p0:HOME-ID FAKE_MODE="$1" FAKE_LOG="$T/log" "${@:2}"
}

for impl in "./menu.sh" "python3 menu.py" "env PYTHONPATH=$T/py python3 menu_api.py"; do
  : > "$T/log"; rm -f "$T/home/.iterm_menu_panes"
  echo "===== $impl: pick web1, then db, then Quit (6)"
  printf '1\n3\n6\n' | run ok $impl >/dev/null; echo "exit=$?"
  echo "--- commands sent to panes:"; cat "$T/log"
  echo "===== $impl: panes in another tab"
  echo 6 | run wrongtab $impl; echo "exit=$? (expect 2)"
  echo "===== $impl: osascript error"
  echo 6 | run fail $impl; echo "exit=$? (expect 1)"
  echo
done
