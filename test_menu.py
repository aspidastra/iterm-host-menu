#!/usr/bin/env python3
"""Offline tests for setup_panes.py and menu.py: python3 -m unittest -v

A fake `iterm2` package (windows → tabs → panes, splits, names, sent text)
replaces the real one, so nothing touches iTerm2 and the real package isn't
needed. Real iTerm2 behaviour (and real speed) is NOT covered by this.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import itertools
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


# --- fake iterm2 package ------------------------------------------------------
class FakeSession:
    def __init__(self, app, session_id):
        self.app, self.session_id = app, session_id
        self.name = None

    async def async_split_pane(self, vertical=False, before=False, profile=None):
        new = self.app.new_session()
        tab = self.app.tab_of(self)
        tab.sessions.insert(tab.sessions.index(self) + 1, new)
        self.app.splits.append((self.session_id, vertical, new.session_id))
        return new

    async def async_set_name(self, name):
        self.name = name

    async def async_send_text(self, text, suppress_broadcast=False):
        self.app.sent.append((self.session_id, text))


class FakeTab:
    def __init__(self, tab_id):
        self.tab_id, self.sessions = tab_id, []


class FakeWindow:
    def __init__(self, window_id):
        self.window_id, self.tabs = window_id, []


class FakeApp:
    def __init__(self):
        self._ids = itertools.count(1)
        self.windows, self.splits, self.sent = [], [], []

    def new_session(self):
        return FakeSession(self, f"S{next(self._ids)}")

    def add_tab(self, window_index=0, session_ids=()):
        while len(self.windows) <= window_index:
            self.windows.append(FakeWindow(f"W{len(self.windows) + 1}"))
        tab = FakeTab(f"T{next(self._ids)}")
        tab.sessions = [FakeSession(self, sid) for sid in session_ids]
        self.windows[window_index].tabs.append(tab)
        return tab

    def tab_of(self, session):
        return self.get_window_and_tab_for_session(session)[1]

    def get_session_by_id(self, session_id, include_buried=True):
        for w in self.windows:
            for t in w.tabs:
                for s in t.sessions:
                    if s.session_id == session_id:
                        return s
        return None

    def get_window_and_tab_for_session(self, session):
        for w in self.windows:
            for t in w.tabs:
                if session in t.sessions:
                    return w, t
        return None, None

    def close(self, session_id):
        _, tab = self.get_window_and_tab_for_session(self.get_session_by_id(session_id))
        tab.sessions = [s for s in tab.sessions if s.session_id != session_id]

    def names(self):
        return {s.session_id: s.name for w in self.windows for t in w.tabs for s in t.sessions}


fake = types.ModuleType("iterm2")
fake.APP = None
fake.CONNECT_FAILS = False


async def _async_get_app(connection, create_if_needed=True):
    return fake.APP


def _run_until_complete(coro, retry=False):
    if fake.CONNECT_FAILS:  # the real package prints help, then sys.exit(1)
        raise SystemExit(1)
    asyncio.run(coro(None))


fake.async_get_app = _async_get_app
fake.run_until_complete = _run_until_complete
sys.modules["iterm2"] = fake

sys.path.insert(0, str(Path(__file__).resolve().parent))
import menu  # noqa: E402
import setup_panes  # noqa: E402

HOME_ID = "HOME"
ITERM_ENV = {"TERM_PROGRAM": "iTerm.app", "ITERM_SESSION_ID": f"w0t0p0:{HOME_ID}"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        fake.APP = FakeApp()
        fake.CONNECT_FAILS = False
        self.app = fake.APP
        self.app.add_tab(0, [HOME_ID])
        for patch in (
            mock.patch.object(setup_panes, "STATE_FILE", self.tmp / "panes"),
            mock.patch.dict(os.environ, ITERM_ENV),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def ensure(self):
        with setup_panes.ITermConnection() as conn:
            return setup_panes.ensure_panes(conn)


class SetupPanesTest(Base):
    def test_first_run_builds_2x2_grid_and_saves_ids(self):
        ids = self.ensure()
        s1, s2, s3 = ids
        self.assertEqual(self.app.splits, [
            (HOME_ID, True, s1),   # vertical divider → Option 1 top right
            (HOME_ID, False, s2),  # below the menu pane → Option 2
            (s1, False, s3),       # below Option 1 → Option 3
        ])
        self.assertEqual([self.app.names()[i] for i in ids], ["Option 1", "Option 2", "Option 3"])
        self.assertEqual(setup_panes.read_state(), ids)

    def test_second_run_reuses_panes(self):
        ids = self.ensure()
        self.app.splits.clear()
        self.assertEqual(self.ensure(), ids)
        self.assertEqual(self.app.splits, [])

    def test_closed_pane_is_recreated_below_menu_pane(self):
        s1, s2, s3 = self.ensure()
        self.app.close(s2)
        self.app.splits.clear()
        new1, new2, new3 = self.ensure()
        self.assertEqual((new1, new3), (s1, s3))
        self.assertEqual(self.app.splits, [(HOME_ID, False, new2)])
        self.assertEqual(self.app.names()[new2], "Option 2")

    def test_two_closed_panes_are_recreated(self):
        s1, s2, s3 = self.ensure()
        self.app.close(s1)
        self.app.close(s3)
        new = self.ensure()
        self.assertEqual(new[1], s2)
        self.assertNotIn(s1, new)
        self.assertEqual([self.app.names()[i] for i in new], ["Option 1", "Option 2", "Option 3"])

    def test_panes_in_another_tab_raise_wrong_tab(self):
        setup_panes.write_state(["A", "B", "C"])
        self.app.add_tab(1, ["X"])
        self.app.add_tab(1, ["A", "B", "C"])  # window 2, tab 2
        with self.assertRaises(setup_panes.WrongTabError) as cm:
            self.ensure()
        self.assertIn("Option 1, Option 2, Option 3", str(cm.exception))
        self.assertIn("tab 2 of window 2", str(cm.exception))
        self.assertEqual(self.app.splits, [])

    def test_refuses_outside_iterm2(self):
        with mock.patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal"}):
            with self.assertRaisesRegex(setup_panes.SetupError, "inside iTerm2"):
                setup_panes.ITermConnection()

    def test_connection_failure_is_a_setup_error(self):
        fake.CONNECT_FAILS = True
        with self.assertRaisesRegex(setup_panes.SetupError, "could not connect"):
            setup_panes.ITermConnection()

    def test_missing_package_is_a_setup_error(self):
        with mock.patch.object(setup_panes, "iterm2", None):
            with self.assertRaisesRegex(setup_panes.SetupError, "no iterm2 package"):
                setup_panes.ITermConnection()

    def test_main_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup_panes.main(), 0)
            setup_panes.write_state(["A", "B", "C"])
            self.app.add_tab(0, ["A", "B", "C"])
            self.assertEqual(setup_panes.main(), 2)
            fake.CONNECT_FAILS = True
            self.assertEqual(setup_panes.main(), 1)


SSH_CONFIG = """\
# comment
Host web1 web2   # two aliases
    HostName 10.0.0.5
host=db
  User admin
Host *.internal !bad
Host *
  ServerAliveInterval 30
Include conf.d/*
"""


class HostParsingTest(unittest.TestCase):
    def test_list_hosts(self):
        with tempfile.TemporaryDirectory() as home:
            ssh = Path(home, ".ssh")
            (ssh / "conf.d").mkdir(parents=True)
            (ssh / "config").write_text(SSH_CONFIG)
            (ssh / "conf.d" / "extra").write_text("Host lab-box\n  HostName 192.168.1.9\nHost web1\n")
            with mock.patch.object(Path, "home", return_value=Path(home)), \
                 mock.patch.object(menu, "SSH_CONFIG", ssh / "config"):
                self.assertEqual(menu.load_hosts(), ["web1", "web2", "db", "lab-box"])

    def test_resolve_hostname(self):
        out = subprocess.CompletedProcess([], 0, stdout="user admin\nhostname 10.0.0.5\nport 22\n")
        with mock.patch.object(menu.subprocess, "run", return_value=out):
            self.assertEqual(menu.resolve_hostname("web1"), "10.0.0.5")
        with mock.patch.object(menu.subprocess, "run", side_effect=OSError):
            self.assertEqual(menu.resolve_hostname("web1"), "web1")

    def test_pane_commands_quote_for_the_shell(self):
        self.assertEqual(menu.pane_commands("db", "10.0.0.5"), [
            (1, "echo 'I want to ssh to db  ->  ssh db'"),
            (2, "ping -c 5 10.0.0.5"),
            (3, "nslookup 10.0.0.5"),
        ])
        self.assertEqual(menu.pane_commands("x", "a b")[2], (3, "nslookup 'a b'"))


class MenuTest(Base):
    def run_menu(self, replies):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=replies), \
             mock.patch.object(menu, "load_hosts", return_value=["web1", "db"]), \
             mock.patch.object(menu, "resolve_hostname", side_effect=lambda h: {"web1": "10.0.0.5"}.get(h, h)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = menu.main()
        return code, out.getvalue()

    def test_picks_send_commands_with_return(self):
        code, _ = self.run_menu(["1", "9", "4"])  # web1, invalid, Quit
        s1, s2, s3 = setup_panes.read_state()
        self.assertEqual(code, 0)
        self.assertEqual(sorted(self.app.sent), sorted([
            (s1, "echo 'I want to ssh to web1  ->  ssh web1'\n"),
            (s2, "ping -c 5 10.0.0.5\n"),
            (s3, "nslookup 10.0.0.5\n"),
        ]))

    def test_pane_closed_mid_session_is_reported(self):
        ids = self.ensure()
        self.app.close(ids[1])
        with setup_panes.ITermConnection() as conn, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            menu.send_to_panes(conn, ids, menu.pane_commands("db", "db"))
        self.assertIn("pane for Option 2 is gone", out.getvalue())
        self.assertEqual(len(self.app.sent), 2)

    def test_eof_quits(self):
        self.assertEqual(self.run_menu(EOFError())[0], 0)

    def test_wrong_tab_exits_2(self):
        setup_panes.write_state(["A", "B", "C"])
        self.app.add_tab(0, ["A", "B", "C"])
        code, out = self.run_menu([])
        self.assertEqual(code, 2)
        self.assertIn("Switch to that iTerm2 tab", out)

    def test_connection_failure_exits_1(self):
        fake.CONNECT_FAILS = True
        self.assertEqual(self.run_menu([])[0], 1)


if __name__ == "__main__":
    unittest.main()
