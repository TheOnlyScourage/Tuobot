# -*- coding: utf-8 -*-
"""/recommend: pure decision rules + wiring guards.

recommend_rules.py is dependency-free apart from bot.constants, so it loads
by path with the constants module stubbed in (birthday_calendar-style). The
AST guards pin the parts a partial paste-over could sever: the casual flag
in Match, the registration bypass, the streak gate, and start()'s new
signature."""
import ast
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(rel, name):
	spec = importlib.util.spec_from_file_location(name, ROOT / rel)
	mod = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(mod)
	return mod


# constants.py has zero imports — load it for real, then expose it as
# `bot.constants` so recommend_rules' import resolves without the bot package.
_constants = _load("bot/constants.py", "constants_for_recommend")
_bot_pkg = types.ModuleType("bot")
_bot_pkg.__path__ = []
sys.modules.setdefault("bot", _bot_pkg)
sys.modules["bot.constants"] = _constants
rules = _load("bot/queues/recommend_rules.py", "recommend_rules")


# ── formats ──────────────────────────────────────────────────────────────────

def test_format_table():
	assert rules.format_team_size("3v3") == 3
	assert rules.format_team_size("5v5") == 5
	assert rules.format_team_size("6v6") is None
	assert rules.players_needed(3) == 6


def test_six_v_six_queue_offers_every_format():
	assert rules.allowed_formats(12) == ["1v1", "2v2", "3v3", "4v4", "5v5"]


def test_formats_must_be_strictly_smaller_than_the_queue():
	assert rules.allowed_formats(10) == ["1v1", "2v2", "3v3", "4v4"]   # no 5v5 from a 5v5 queue
	assert rules.allowed_formats(4) == ["1v1"]
	assert rules.allowed_formats(2) == []


# ── roster selection ─────────────────────────────────────────────────────────

def test_first_come_roster():
	assert rules.pick_roster([1, 2, 3, 4, 5], {1, 2, 3, 4, 5}, 4) == [1, 2, 3, 4]


def test_accepters_who_left_the_queue_drop_out():
	# 2 left the queue after accepting; 5 fills the gap in press order
	assert rules.pick_roster([1, 2, 3, 4, 5], {1, 3, 4, 5}, 4) == [1, 3, 4, 5]


def test_short_roster_is_none():
	assert rules.pick_roster([1, 2, 3], {1, 2, 3}, 4) is None
	assert rules.pick_roster([1, 2, 3, 4], {1, 2}, 4) is None


def test_cooldown_math():
	assert rules.cooldown_left(100.0, 160.0) == 60
	assert rules.cooldown_left(200.0, 160.0) == 0


# ── wiring guards ────────────────────────────────────────────────────────────

def _src(rel):
	return (ROOT / rel).read_text(encoding="utf-8")


def test_start_accepts_roster_team_size_and_casual():
	tree = ast.parse(_src("bot/queues/pickup_queue.py"))
	fn = next(
		n for n in ast.walk(tree)
		if isinstance(n, ast.AsyncFunctionDef) and n.name == "start"
	)
	assert [a.arg for a in fn.args.args] == ["self", "ctx", "players", "team_size", "casual"]


def test_casual_branch_keeps_players_queued_with_instant_teams():
	src = _src("bot/queues/pickup_queue.py")
	start = src[src.index("async def start(self, ctx, players"):src.index("async def", src.index("async def start(self, ctx, players") + 10)]
	assert 'pick_teams="random teams"' in start and "check_in_timeout=0" in start
	assert 'team_names=["Team A", "Team B"]' in start
	# the casual branch returns BEFORE queue_started (nobody is removed)
	assert start.index("casual=True") < start.index("return") < start.index("queue_started(")
	# and the match itself never runs cross-queue cleanup for casual games
	assert "if self.casual:  # casual (/recommend) players stay in their queues" in _src("bot/match/match.py")


def test_match_has_casual_flag_and_skips_registration():
	src = _src("bot/match/match.py")
	assert "casual=False" in src                                   # default_cfg
	assert "self.casual = bool(self.cfg.get('casual', False))" in src
	assert "if self.casual:\n\t\t\tpass" in src                    # finish_match bypass
	assert "and not match.cfg.get('casual')" in src                # streak gate
	assert "if not self.casual and len(self.teams[0])" in src      # captain history gate


def test_slash_and_handler_wired():
	assert _src("bot/context/slash/commands.py").count("name='recommend'") == 1
	assert "'recommend'" in _src("bot/commands/queues.py")
	assert "async def recommend(" in _src("bot/commands/queues.py")


def test_constants_present():
	assert _constants.RECOMMEND_FORMATS == {"1v1": 1, "2v2": 2, "3v3": 3, "4v4": 4, "5v5": 5}
	assert _constants.RECOMMEND_WINDOW == 300
	assert _constants.RECOMMEND_COOLDOWN > 0
