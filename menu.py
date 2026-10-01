#!/usr/bin/env python3
"""Host menu: pick a host from ~/.ssh/config, then
  pane "Option 1" echoes that you want to ssh to it
  pane "Option 2" pings it 5 times
  pane "Option 3" runs nslookup on it
Panes are created on first run (via setup_panes.py) and targeted by saved ID.
Set MENU_TIMING=1 to print how long each step takes.

Everything goes through iTerm2's Python API over one connection kept open for
the whole session (setup notes: top of setup_panes.py). Run it with the venv's
Python, e.g. ~/.venvs/iterm2/bin/python menu.py.
"""
from __future__ import annotations

import asyncio
import glob
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_panes import ITermConnection, SetupError, WrongTabError, ensure_panes  # noqa: E402

SSH_CONFIG = Path(os.environ.get("SSH_CONFIG", Path.home() / ".ssh" / "config"))
PING_COUNT = 5

# "Keyword value" or "Keyword=value", as ssh_config allows.
KEYWORD_RE = re.compile(r"^\s*([A-Za-z]+)\s*(?:=|\s)\s*(.*)$")


def send_to_panes(conn: ITermConnection, pane_ids: list[str], commands: list[tuple[int, str]]) -> None:
    """Type each (n, cmd) into pane "Option n", all at once."""
    try:
        conn.run(_send(conn.app, pane_ids, commands), timeout=10)
    except Exception as e:
        print(f"  ✗ sending failed: {e!r}")


async def _send(app, pane_ids: list[str], commands: list[tuple[int, str]]) -> None:
    targets = []
    for n, cmd in commands:
        session = app.get_session_by_id(pane_ids[n - 1], include_buried=False)
        if session is None:
            print(f"  ✗ pane for Option {n} is gone — restart the menu to recreate it")
        else:
            targets.append((n, session, cmd))

    # "\n" = press Return (async_send_text types exactly the text it is given).
    results = await asyncio.gather(
        *(s.async_send_text(cmd + "\n", suppress_broadcast=True) for _, s, cmd in targets),
        return_exceptions=True,
    )
    for (n, _, _), result in zip(targets, results):
        if isinstance(result, Exception):
            print(f"  ✗ Option {n}: {result}")
        else:
            print(f"  → sent to Option {n}")


def timing(label: str, start: float) -> None:
    """With MENU_TIMING=1, print how long a step took."""
    if os.environ.get("MENU_TIMING"):
        print(f"  [timing] {label}: {(time.perf_counter() - start) * 1000:.0f} ms")


def pane_commands(host: str, target: str) -> list[tuple[int, str]]:
    """The commands each pane gets for a host."""
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
    t0 = time.perf_counter()
    try:
        conn = ITermConnection()
    except SetupError as e:
        print(f"menu.py: {e}", file=sys.stderr)
        return 1
    timing("connect to iTerm2 (once)", t0)
    try:
        t1 = time.perf_counter()
        pane_ids = ensure_panes(conn)
        timing("pane setup", t1)
    except WrongTabError as e:
        conn.close()
        print(e, file=sys.stderr)
        return 2
    except Exception as e:
        conn.close()
        print(f"menu.py: pane setup failed: {e}", file=sys.stderr)
        return 1

    try:
        hosts = load_hosts()
        if not hosts:
            print(f"No hosts found in {SSH_CONFIG}")
            return 1
        return menu_loop(conn, pane_ids, hosts)
    finally:
        conn.close()


def menu_loop(conn: ITermConnection, pane_ids: list[str], hosts: list[str]) -> int:
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
        send_to_panes(conn, pane_ids, pane_commands(host, target))
        timing("send to 3 panes", t1)
        timing("total", t0)


if __name__ == "__main__":
    sys.exit(main())
