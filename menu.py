#!/usr/bin/env python3
"""Host menu: pick a host from ~/.ssh/config, then
  pane "Option 1" echoes that you want to ssh to it
  pane "Option 2" pings it 5 times
  pane "Option 3" runs nslookup on it
Panes are created on first run (via setup_panes.py) and targeted by saved ID.
Set MENU_TIMING=1 to print how long each step takes.
"""
from __future__ import annotations

import glob
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_panes import SetupError, WrongTabError, ensure_panes, run_osascript  # noqa: E402

SSH_CONFIG = Path(os.environ.get("SSH_CONFIG", Path.home() / ".ssh" / "config"))
PING_COUNT = 5

SEND_SCRIPT = r'''
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
'''

# "Keyword value" or "Keyword=value", as ssh_config allows.
KEYWORD_RE = re.compile(r"^\s*([A-Za-z]+)\s*(?:=|\s)\s*(.*)$")


def send_to_panes(pane_ids: list[str], commands: list[tuple[int, str]]) -> None:
    """Type each (n, cmd) into pane "Option n".

    All commands go out in ONE osascript call: starting osascript is the slow
    part, so batching keeps the delay to a single launch instead of one per pane.
    """
    args = [x for n, cmd in commands for x in (pane_ids[n - 1], cmd)]
    try:
        results = run_osascript(SEND_SCRIPT, *args).split()
    except SetupError as e:
        print(f"  ✗ {e}")
        return
    for (n, _), result in zip(commands, results):
        if result == "sent":
            print(f"  → sent to Option {n}")
        else:
            print(f"  ✗ pane for Option {n} is gone — restart the menu to recreate it")


def timing(label: str, start: float) -> None:
    """With MENU_TIMING=1, print how long a step took."""
    if os.environ.get("MENU_TIMING"):
        print(f"  [timing] {label}: {(time.perf_counter() - start) * 1000:.0f} ms")


def pane_commands(host: str, target: str) -> list[tuple[int, str]]:
    """The commands each pane gets for a host (shared with menu_api.py)."""
    return [
        (1, "echo " + shlex.quote(f"I want to ssh to {host}  ->  ssh {host}")),
        (2, f"ping -c {PING_COUNT} {shlex.quote(target)}"),
        (3, f"nslookup {shlex.quote(target)}"),
    ]


def split_values(rest: str) -> list[str]:
    """Split a config value list, honouring quotes like ssh does."""
    try:
        return shlex.split(rest)
    except ValueError:  # unbalanced quote
        return rest.split()


def list_hosts(config: Path, _seen_files: set[Path] | None = None) -> list[str]:
    """Concrete host aliases from "Host" lines, in file order.

    - A line can hold several aliases ("Host web1 web2") — each is listed.
    - Wildcard / negated patterns (*, ?, !) are skipped: you can't ssh to them.
    - "Include" files are followed (relative paths are under ~/.ssh).
    """
    seen_files = _seen_files if _seen_files is not None else set()
    try:
        config = config.resolve()
        if config in seen_files or not config.is_file():
            return []
        seen_files.add(config)
        lines = config.read_text(errors="replace").splitlines()
    except OSError:
        return []

    hosts: list[str] = []
    for line in lines:
        line = line.split("#", 1)[0]
        m = KEYWORD_RE.match(line)
        if not m:
            continue
        key, rest = m.group(1).lower(), m.group(2)
        if key == "host":
            hosts += [p for p in split_values(rest) if not re.search(r"[*?]", p) and not p.startswith("!")]
        elif key == "include":
            for pattern in split_values(rest):
                pattern = os.path.expanduser(pattern)
                if not os.path.isabs(pattern):
                    pattern = str(Path.home() / ".ssh" / pattern)
                for inc in sorted(glob.glob(pattern)):
                    hosts += list_hosts(Path(inc), seen_files)
    return hosts


def load_hosts() -> list[str]:
    return list(dict.fromkeys(list_hosts(SSH_CONFIG)))  # de-duplicate, keep order


def resolve_hostname(alias: str) -> str:
    """The real HostName ssh would use for `alias` (falls back to the alias)."""
    try:
        out = subprocess.run(["ssh", "-G", alias], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return alias
    for line in out.splitlines():
        key, _, value = line.partition(" ")
        if key == "hostname" and value:
            return value.strip()
    return alias


def choose(options: list[str]) -> str | None:
    """Numbered menu like bash's `select`. Returns the chosen option, None on EOF."""
    for i, opt in enumerate(options, 1):
        print(f"{i:>3}) {opt}")
    while True:
        try:
            reply = input("\nWhich host do you want to work with? ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if reply.isdigit() and 1 <= int(reply) <= len(options):
            return options[int(reply) - 1]
        print(f"invalid option {reply}")


def main() -> int:
    try:
        pane_ids = ensure_panes()
    except WrongTabError as e:
        print(e, file=sys.stderr)
        return 2
    except SetupError as e:
        print(f"setup_panes.py: {e}", file=sys.stderr)
        return 1

    hosts = load_hosts()
    if not hosts:
        print(f"No hosts found in {SSH_CONFIG}")
        return 1

    while True:
        print(f"\n=== Hosts in {SSH_CONFIG} ===")
        opt = choose(hosts + ["Reload hosts", "Quit"])
        if opt is None or opt == "Quit":
            return 0
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
        send_to_panes(pane_ids, pane_commands(host, target))
        timing("send to 3 panes (one osascript)", t1)
        timing("total", t0)


if __name__ == "__main__":
    sys.exit(main())
