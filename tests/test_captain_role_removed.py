# -*- coding: utf-8 -*-
"""Guards for the Oct 2026 captain overhaul: the Captain ROLE system and the
/capfor + /capme volunteer commands are gone; eligibility (bar role + minimum
games) is wired through Match.new, standby re-picks, and the queue config.
A partial paste-over that resurrects any piece fails here, not in production."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(rel):
	return (ROOT / rel).read_text(encoding="utf-8")


def _func(tree, name):
	return next(
		n for n in ast.walk(tree)
		if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
	)


def test_volunteer_commands_are_gone():
	slash = _src("bot/context/slash/commands.py")
	assert "name='capfor'" not in slash and "name='capme'" not in slash
	handlers = _src("bot/commands/matches.py")
	assert "async def cap_me(" not in handlers and "async def cap_for(" not in handlers
	assert "'cap_me'" not in handlers and "'cap_for'" not in handlers
	draft = _src("bot/match/draft.py")
	assert "async def cap_me(" not in draft and "async def cap_for(" not in draft
	assert "teams_by_author" not in _src("bot/context/slash/autocomplete.py")


def test_captain_role_system_is_gone():
	for rel in ("bot/constants.py", "bot/match/match.py", "bot/match/draft.py", "bot/queues/pickup_queue.py"):
		src = _src(rel)
		assert "CAPTAIN_ROLE_ID" not in src, rel
		assert "captain_streak" not in src, rel
	assert "captains_role" not in _src("bot/queues/pickup_queue.py")
	assert "captain_streak" not in _src("bot/db_init.py")
	assert not (ROOT / "bot" / "stats" / "captain_streak.py").exists()


def test_eligibility_constants():
	src = _src("bot/constants.py")
	assert "CAPTAIN_BAR_ROLE_ID = 1564449871019114597" in src
	assert "CAPTAIN_MIN_GAMES   = 25" in src


def test_match_wiring():
	tree = ast.parse(_src("bot/match/match.py"))
	init = _func(tree, "init_captains")
	assert [a.arg for a in init.args.args] == ["self", "pick_captains"]
	calls = {n.func.id for n in ast.walk(init) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
	assert {"eligible_captains", "select_smart_captains"} <= calls
	names = {n.id for n in ast.walk(init) if isinstance(n, ast.Name)}
	assert {"CAPTAIN_BAR_ROLE_ID", "CAPTAIN_MIN_GAMES"} <= names
	assert _func(tree, "prefetch_career_games") is not None
	new = _func(tree, "new")
	assert "prefetch_career_games" in {
		n.func.attr for n in ast.walk(new) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
	}
	src = _src("bot/match/match.py")
	assert '"captain_role": "smart"' in src and '"by role and rating": "by rating"' in src


def test_standby_repick_refreshes_games_then_repicks():
	src = _src("bot/match/standby.py")
	assert "await match.prefetch_career_games(match.qc.id)" in src
	assert "match.init_captains(match.cfg['pick_captains'])" in src


def test_queue_pick_modes():
	src = _src("bot/queues/pickup_queue.py")
	assert 'options=["smart", "by rating", "fair pairs", "random", "no captains"]' in src


def test_career_games_query_exists():
	tree = ast.parse(_src("bot/stats/stats.py"))
	assert _func(tree, "career_games_by_id") is not None
