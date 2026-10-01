#!/usr/bin/env bash
# compare_timing.sh — compare per-pick send latency of menu.py (osascript)
# and menu_api.py (iTerm2 Python API). Run it inside iTerm2, from the menu pane.
#
# One-time manual step for the API version:
#   iTerm2 → Settings → General → Magic → tick "Enable Python API"
#
# Each menu is interactive: pick a host (repeat to collect several samples),
# then Quit to move on.
set -u
cd "$(dirname "$0")"
VENV="$HOME/.venvs/iterm2"

if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV" || exit 1
fi
"$VENV/bin/python" -c 'import iterm2' 2>/dev/null || "$VENV/bin/pip" install iterm2 || exit 1

# Baseline: how much of osascript's cost is starting the process vs. talking to iTerm2.
echo "=== osascript baseline (5 runs each) ==="
python3 - <<'PY'
import subprocess, time
for label, args in [
    ("bare osascript", ["osascript", "-e", "return 1"]),
    ("osascript + one iTerm2 Apple Event", ["osascript", "-e", 'tell application "iTerm2" to count windows']),
]:
    times = []
    for _ in range(5):
        t = time.perf_counter()
        subprocess.run(args, capture_output=True)
        times.append((time.perf_counter() - t) * 1000)
    print(f"  {label}: " + ", ".join(f"{x:.0f}" for x in times) + " ms")
PY

echo; echo "=== menu.py (osascript, batched) ==="
MENU_TIMING=1 python3 menu.py
echo; echo "=== menu_api.py (Python API) ==="
MENU_TIMING=1 "$VENV/bin/python" menu_api.py
