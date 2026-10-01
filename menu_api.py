#!/usr/bin/env python3
"""menu_api.py — same host menu as menu.py, but talks to iTerm2 through its
Python API (a persistent websocket connection) instead of launching osascript.

Use it to compare speed with menu.py / menu.sh:
    MENU_TIMING=1 python3 menu.py
    MENU_TIMING=1 python3 menu_api.py

Setup (once):
  1. iTerm2 → Settings → General → Magic → tick "Enable Python API".
  2. python3 -m venv ~/.venvs/iterm2 && ~/.venvs/iterm2/bin/pip install iterm2
     (a venv avoids the "externally managed environment" error from
     Homebrew's Python), then run this script with ~/.venvs/iterm2/bin/python.
  3. The first connection may make iTerm2 ask you to allow it. Allow it.

To keep the comparison fair, pane creation is the same as menu.py (setup_panes.py,
via osascript, once at startup). Only the per-pick sending is done through the API.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from menu import (  # noqa: E402
    SSH_CONFIG, choose, load_hosts, pane_commands, resolve_hostname, timing,
)
from setup_panes import SetupError, WrongTabError, ensure_panes  # noqa: E402

try:
    import iterm2
except ImportError:
    sys.exit("menu_api.py needs the iterm2 package: pip install iterm2 (see the setup notes at the top of this file)")


async def send_to_panes(app, pane_ids: list[str], commands: list[tuple[int, str]]) -> None:
    """Type each (n, cmd) into pane "Option n", all at once, over the open connection."""
    targets = []
    for n, cmd in commands:
        session = app.get_session_by_id(pane_ids[n - 1])
        if session is None:
            print(f"  ✗ pane for Option {n} is gone — restart the menu to recreate it")
        else:
            targets.append((n, session, cmd))

    # "\n" = press Return (write text in AppleScript adds it for us; the API doesn't).
    results = await asyncio.gather(
        *(s.async_send_text(cmd + "\n", suppress_broadcast=True) for _, s, cmd in targets),
        return_exceptions=True,
    )
    for (n, _, _), result in zip(targets, results):
        if isinstance(result, Exception):
            print(f"  ✗ Option {n}: {result}")
        else:
            print(f"  → sent to Option {n}")


async def main(connection) -> None:
    app = await iterm2.async_get_app(connection)

    missing = [n for n, pid in enumerate(PANE_IDS, 1) if app.get_session_by_id(pid) is None]
    if missing:
        print(
            "The Python API can't see pane(s) "
            + ", ".join(f"Option {n}" for n in missing)
            + " by the IDs that setup_panes.py saved.\n"
            "That would mean the API's session IDs differ from AppleScript's; "
            "report this so the script can be adjusted.",
            file=sys.stderr,
        )
        return

    hosts = load_hosts()
    if not hosts:
        print(f"No hosts found in {SSH_CONFIG}")
        return

    loop = asyncio.get_running_loop()
    while True:
        print(f"\n=== Hosts in {SSH_CONFIG} (iTerm2 Python API) ===")
        # Run the blocking menu prompt in a thread so the connection stays serviced.
        opt = await loop.run_in_executor(None, choose, hosts + ["Reload hosts", "Quit"])
        if opt is None or opt == "Quit":
            return
        if opt == "Reload hosts":
            hosts = load_hosts() or hosts
            print(f"Loaded {len(hosts)} host(s).")
            continue

        host = opt
        t0 = time.perf_counter()
        target = resolve_hostname(host)
        timing("ssh -G lookup", t0)
        print(f"Selected: {host} ({target})")
        t1 = time.perf_counter()
        await send_to_panes(app, PANE_IDS, pane_commands(host, target))
        timing("send to 3 panes (Python API)", t1)
        timing("total", t0)


if __name__ == "__main__":
    try:
        PANE_IDS = ensure_panes()
    except WrongTabError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    except SetupError as e:
        print(f"setup_panes.py: {e}", file=sys.stderr)
        sys.exit(1)

    t_connect = time.perf_counter()
    if os.environ.get("MENU_TIMING"):
        # Measure the one-off connection cost separately from per-pick sends.
        async def _timed(connection):
            timing("connect to iTerm2 API (once)", t_connect)
            await main(connection)
        iterm2.run_until_complete(_timed)
    else:
        iterm2.run_until_complete(main)
