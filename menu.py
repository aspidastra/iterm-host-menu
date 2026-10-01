#!/usr/bin/env python3
"""Host menu: pick a host from ~/.ssh/config, then
  pane "Option 1" echoes that you want to ssh to it
  pane "Option 2" pings it 5 times
  pane "Option 3" runs nslookup on it
Panes are created on first run (via setup_panes.py) and targeted by saved ID.
Set MENU_TIMING=1 to print how long each step takes.

Commands are sent to the panes through iTerm2's Python API (one connection kept
open for the whole session), which measured ~37 ms per pick against ~4.8 s for
osascript. Set MENU_TRANSPORT to choose:
    (unset)    API if the iterm2 package is installed, otherwise osascript
    api        API only; error if it isn't available
    osascript  AppleScript via osascript (no extra installs, much slower)

API setup (once):
  1. iTerm2 → Settings → General → Magic → tick "Enable Python API".
  2. python3 -m venv ~/.venvs/iterm2 && ~/.venvs/iterm2/bin/pip install iterm2
     (a venv avoids the "externally managed environment" error from
     Homebrew's Python), then run this script with ~/.venvs/iterm2/bin/python.
  3. The first connection may make iTerm2 ask you to allow it. Allow it.
Pane creation (setup_panes.py) always uses osascript; it only runs at startup.
"""
from __future__ import annotations

import asyncio
import glob
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_panes import SetupError, WrongTabError, ensure_panes, run_osascript  # noqa: E402

try:
    import iterm2
except ImportError:
    iterm2 = None

SSH_CONFIG = Path(os.environ.get("SSH_CONFIG", Path.home() / ".ssh" / "config"))
PING_COUNT = 5
API_HINT = "To use the faster Python API, see the setup notes at the top of menu.py."

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


class OsascriptSender:
    """Sends pane commands with osascript (no extra installs, but slow)."""

    label = "osascript"

    def __init__(self, pane_ids: list[str]) -> None:
        self.pane_ids = pane_ids

    def send(self, commands: list[tuple[int, str]]) -> None:
        """Type each (n, cmd) into pane "Option n".

        All commands go out in ONE osascript call: starting osascript is the slow
        part, so batching keeps the delay to a single launch instead of one per pane.
        """
        args = [x for n, cmd in commands for x in (self.pane_ids[n - 1], cmd)]
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

    def close(self) -> None:
        pass


class ApiSender:
    """Sends pane commands through iTerm2's Python API.

    The API is asyncio-based, so its connection runs on a background thread for
    the whole session, and the menu (blocking input()) stays on the main thread,
    where Ctrl-C works normally. send() hands each batch to that thread.
    """

    label = "iTerm2 Python API"

    def __init__(self, pane_ids: list[str]) -> None:
        self.pane_ids = pane_ids
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._missing: list[int] = []
        self._loop = self._app = self._stop = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            # On connection problems the iterm2 package prints its own help, then exits.
            raise SetupError(
                f"could not connect to iTerm2's Python API ({self._error!r}).\n"
                "Check that the API is enabled, or run with MENU_TRANSPORT=osascript."
            )
        if self._missing:
            raise SetupError(
                "the Python API can't see pane(s) "
                + ", ".join(f"Option {n}" for n in self._missing)
                + " by the IDs that setup_panes.py saved.\n"
                "Run with MENU_TRANSPORT=osascript, and report this so the script can be adjusted."
            )

    def _run(self) -> None:
        try:
            iterm2.run_until_complete(self._serve)
        except BaseException as e:  # incl. SystemExit: iterm2 calls sys.exit() when it can't connect
            self._error = e
        finally:
            self._ready.set()  # never leave __init__ waiting

    async def _serve(self, connection) -> None:
        self._loop = asyncio.get_running_loop()
        self._app = await iterm2.async_get_app(connection)
        self._missing = [n for n, pid in enumerate(self.pane_ids, 1)
                         if self._app.get_session_by_id(pid) is None]
        self._stop = asyncio.Event()
        self._ready.set()
        await self._stop.wait()  # keep the connection open until close()

    def send(self, commands: list[tuple[int, str]]) -> None:
        """Type each (n, cmd) into pane "Option n", all at once."""
        try:
            future = asyncio.run_coroutine_threadsafe(self._send(commands), self._loop)
            future.result(timeout=10)
        except Exception as e:
            print(f"  ✗ sending via the Python API failed: {e!r}")

    async def _send(self, commands: list[tuple[int, str]]) -> None:
        targets = []
        for n, cmd in commands:
            session = self._app.get_session_by_id(self.pane_ids[n - 1])
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

    def close(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
            self._thread.join(timeout=2)


def make_sender(pane_ids: list[str]) -> OsascriptSender | ApiSender:
    """Pick the transport from MENU_TRANSPORT (see the top of this file)."""
    choice = os.environ.get("MENU_TRANSPORT", "").strip().lower()
    if choice not in ("", "api", "osascript"):
        raise SetupError(f"MENU_TRANSPORT must be 'api' or 'osascript', not {choice!r}")
    if choice == "osascript":
        return OsascriptSender(pane_ids)
    if iterm2 is None:
        if choice == "api":
            raise SetupError(f"MENU_TRANSPORT=api, but {sys.executable} has no iterm2 package. {API_HINT}")
        print(f"Note: {sys.executable} has no iterm2 package, so commands are sent with osascript "
              f"(much slower). {API_HINT}", file=sys.stderr)
        return OsascriptSender(pane_ids)
    return ApiSender(pane_ids)


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

    t_connect = time.perf_counter()
    try:
        sender = make_sender(pane_ids)
    except SetupError as e:
        print(f"menu.py: {e}", file=sys.stderr)
        return 1
    timing(f"connect ({sender.label}, once)", t_connect)
    print(f"Sending commands via {sender.label}.")
    try:
        return menu_loop(hosts, sender)
    finally:
        sender.close()


def menu_loop(hosts: list[str], sender: OsascriptSender | ApiSender) -> int:
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
        sender.send(pane_commands(host, target))
        timing(f"send to 3 panes ({sender.label})", t1)
        timing("total", t0)


if __name__ == "__main__":
    sys.exit(main())
