# -*- coding: utf-8 -*-
"""
Shared pytest plumbing for the Tuobot test suite.

The suite deliberately tests ONLY the two pure modules:

  - bot/stats/mmr_engine.py        (the MMR formula)
  - bot/match/captain_selection.py (captain scoring/selection)

Both were extracted specifically to be testable without Discord or MySQL.
The catch: importing them the normal way (`import bot.stats.mmr_engine`)
executes `bot/__init__.py`, which needs a live Discord client and a MySQL
connection at import time. So instead we:

  1. register a stub `bot` package in sys.modules (never executes the real
     bot/__init__.py), and
  2. load each module directly from its file path with importlib.

`bot/constants.py` has zero imports, so it loads standalone; mmr_engine's
`from bot.constants import ...` then resolves against the stub package.
captain_selection imports only the stdlib.

This keeps CI dependency-free: the test job installs pytest and nothing
else — no nextcord, no aiomysql, no database.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(fullname: str, relpath: str):
	"""Load a module directly from its file, bypassing package __init__."""
	spec = importlib.util.spec_from_file_location(fullname, ROOT / relpath)
	module = importlib.util.module_from_spec(spec)
	sys.modules[fullname] = module
	spec.loader.exec_module(module)
	return module


# Stub the 'bot' package so `from bot.constants import ...` inside the
# modules under test resolves WITHOUT executing bot/__init__.py.
if "bot" not in sys.modules:
	_bot_stub = types.ModuleType("bot")
	_bot_stub.__path__ = [str(ROOT / "bot")]
	sys.modules["bot"] = _bot_stub

if "bot.constants" not in sys.modules:
	_load("bot.constants", "bot/constants.py")


@pytest.fixture(scope="session")
def constants():
	return sys.modules["bot.constants"]


@pytest.fixture(scope="session")
def mmr_engine():
	return _load("bot.stats.mmr_engine", "bot/stats/mmr_engine.py")


@pytest.fixture(scope="session")
def captain_selection():
	return _load("bot.match.captain_selection", "bot/match/captain_selection.py")


# ── Fake Discord objects ──────────────────────────────────────────────────────
# The modules under test only ever read member.id, member.roles, role.id and
# role.name — these two tiny stand-ins are the whole "mock" surface.

class FakeRole:
	def __init__(self, id=0, name=""):
		self.id = id
		self.name = name

	def __repr__(self):
		return f"<FakeRole {self.id} {self.name!r}>"


class FakeMember:
	def __init__(self, id, roles=()):
		self.id = id
		self.roles = list(roles)

	def __repr__(self):
		return f"<FakeMember {self.id}>"


@pytest.fixture(scope="session")
def role():
	"""The FakeRole class — usage: role(id=100, name='Seeker')."""
	return FakeRole


@pytest.fixture(scope="session")
def member():
	"""The FakeMember class — usage: member(1, roles=[role(name='Beater')])."""
	return FakeMember
