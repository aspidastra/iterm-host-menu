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

Saves the pane IDs to the state file as PANE1_ID / PANE2_ID / PANE3_ID.

Talks to iTerm2 through its Python API. Setup (once):
  1. iTerm2 → Settings → General → Magic → tick "Enable Python API".
  2. python3 -m venv ~/.venvs/iterm2 && ~/.venvs/iterm2/bin/pip install iterm2
     (a venv avoids the "externally managed environment" error from
     Homebrew's Python), then run the scripts with ~/.venvs/iterm2/bin/python.
  3. The first connection may make iTerm2 ask you to allow it. Allow it.

Run it directly, or import it: open an ITermConnection and call ensure_panes().
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path

try:
    import iterm2
except ImportError:
    iterm2 = None

STATE_FILE = Path(os.environ.get("PANES_STATE_FILE", Path.home() / ".iterm_menu_panes"))
PANE_KEYS = ("PANE1_ID", "PANE2_ID", "PANE3_ID")
API_HINT = "See the setup notes at the top of setup_panes.py."


class SetupError(Exception):
    pass


class WrongTabError(SetupError):
    """The saved panes exist, but in a different iTerm2 tab than this one."""


def home_session_id() -> str:
    """ID of the pane this script runs in, from iTerm2's ITERM_SESSION_ID."""
    session = os.environ.get("ITERM_SESSION_ID", "")
    if os.environ.get("TERM_PROGRAM") != "iTerm.app" or not session:
        raise SetupError("must be run inside iTerm2.")
    # Looks like "w0t0p0:6A2B...". The part after ":" is the API's session_id.
    return session.split(":", 1)[-1]


class ITermConnection:
    """One connection to iTerm2's Python API, open until close().

    The API is asyncio-based, so the connection runs on a background thread and
    the caller (e.g. the menu's blocking input()) stays on the main thread, where
    Ctrl-C works normally. run() hands a coroutine to that thread and waits.
    """

    def __init__(self) -> None:
        self.home_id = home_session_id()
        if iterm2 is None:
            raise SetupError(f"{sys.executable} has no iterm2 package. {API_HINT}")
        self.app = None
        self._loop = self._stop = None
        self._error: BaseException | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            # On connection problems the iterm2 package prints its own help, then exits.
            raise SetupError(
                f"could not connect to iTerm2's Python API ({self._error!r}). "
                "Check that it is enabled. " + API_HINT
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
        self.app = await iterm2.async_get_app(connection)
        self._stop = asyncio.Event()
        self._ready.set()
        await self._stop.wait()  # keep the connection open until close()

    def run(self, coro, timeout: float = 30):
        """Run a coroutine on the connection's thread; return its result or raise its error."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def close(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
            self._thread.join(timeout=2)

    def __enter__(self) -> ITermConnection:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


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


def locate(app, session_id: str):
    """(window, tab) holding the pane with this id, or (None, None).

    Buried (hidden) panes count as missing: they aren't in any tab.
    """
    session = app.get_session_by_id(session_id, include_buried=False) if session_id else None
    if session is None:
        return None, None
    return app.get_window_and_tab_for_session(session)


async def _ensure_panes(app, home_id: str, saved: list[str]) -> list[str]:
    home = app.get_session_by_id(home_id, include_buried=False)
    _, home_tab = locate(app, home_id)
    if home is None or home_tab is None:
        raise SetupError("could not find the pane this script is running in.")

    # Sort the 3 saved panes into: here (same tab), elsewhere (other tab), missing.
    ids = ["", "", ""]
    missing: list[int] = []
    elsewhere: list[str] = []
    where = None
    for n, pane_id in enumerate(saved, 1):
        window, tab = locate(app, pane_id)
        if tab is None:
            missing.append(n)
        elif tab.tab_id != home_tab.tab_id:
            elsewhere.append(f"Option {n}")
            where = (window, tab)
        else:
            ids[n - 1] = pane_id

    # Saved panes live in another tab: stop and say where, without splitting anything.
    if elsewhere:
        window, tab = where
        raise WrongTabError(
            f"The {', '.join(elsewhere)} pane(s) for this menu are in "
            f"tab {window.tabs.index(tab) + 1} of window {app.windows.index(window) + 1}, "
            "not in this tab.\n"
            "Switch to that iTerm2 tab and run the menu from there\n"
            "(or close those panes to create a new set in this tab)."
        )

    if len(missing) == 3:
        # Nothing exists yet: build the 2x2 grid.
        top_right = await home.async_split_pane(vertical=True)
        bottom_left = await home.async_split_pane(vertical=False)
        bottom_right = await top_right.async_split_pane(vertical=False)
        new = {1: top_right, 2: bottom_left, 3: bottom_right}
    else:
        # Some panes were closed: recreate just those, below the menu pane.
        new = {n: await home.async_split_pane(vertical=False) for n in missing}

    for n, session in new.items():
        ids[n - 1] = session.session_id

    # Name every pane (reused ones too, so older panes get fixed), and make its
    # title show that name. The user's Default profile shows only the job
    # ("-zsh"), which hides the name; this override applies to these panes only.
    show_name = iterm2.LocalWriteOnlyProfile()
    show_name.set_title_components([iterm2.TitleComponents.SESSION_NAME])
    for n, pane_id in enumerate(ids, 1):
        session = app.get_session_by_id(pane_id, include_buried=False)
        await session.async_set_name(f"Option {n}")
        await session.async_set_profile_properties(show_name)
    return ids


def ensure_panes(conn: ITermConnection) -> list[str]:
    """Create any missing worker panes; return their IDs [Option 1, 2, 3].

    Raises WrongTabError if the saved panes are in a different iTerm2 tab.
    """
    ids = conn.run(_ensure_panes(conn.app, conn.home_id, read_state()))
    write_state(ids)
    return ids


def main() -> int:
    try:
        with ITermConnection() as conn:
            ids = ensure_panes(conn)
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
