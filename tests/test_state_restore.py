# -*- coding: utf-8 -*-
"""Guards for the Nov 2026 Batch 1 fixes: redeploys no longer wipe saved state,
matches restore properly, double reports are refused, game-code listeners
are per match, and priority players leave the queue that popped.

These files import nextcord, so the checks read the source with ast. The
behaviour itself was verified against the real classes when the fixes were
written; these tests stop a partial paste-over from quietly undoing them."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(rel):
	return (ROOT / rel).read_text(encoding="utf-8")


def _tree(rel):
	return ast.parse(_src(rel))


def _func(tree, name, cls=None):
	scope = tree
	if cls:
		scope = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
	return next(
		n for n in ast.walk(scope)
		if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
	)


def _body_without_docstring(fn):
	return [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]


# ── bot/main.py ──────────────────────────────────────────────────────────────

def test_save_state_never_runs_before_the_first_load():
	"""The think loop ticks before on_ready, and its first periodic save used to
	write empty globals over the real saved state (April–Nov 2026)."""
	fn = _func(_tree("bot/main.py"), "save_state")
	body = _body_without_docstring(fn)
	guard_at = next(i for i, s in enumerate(body) if isinstance(s, ast.If))
	guard = body[guard_at]
	assert ast.unparse(guard.test) == "not bot.bot_was_ready"
	assert isinstance(guard.body[-1], ast.Return)
	first_work = next(i for i, s in enumerate(body) if isinstance(s, ast.Assign))
	assert guard_at < first_work, "the guard must come before any state is gathered"


def test_main_never_touches_bot_exc_at_import_time():
	"""bot/__init__.py imports bot/main.py FIRST, before it binds bot.Exc, so a
	module-level `bot.Exc` crashes the boot."""
	for stmt in _tree("bot/main.py").body:
		if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
			continue
		assert "bot.Exc" not in ast.unparse(stmt)


def test_restore_catches_bot_errors_too():
	"""PubobotException derives from BaseException; with only `except
	bot.Exc.ValueError` one odd entry could abort on_ready (bot dead until restart)."""
	src = _src("bot/main.py")
	assert "_RESTORE_ERRORS = (Exception, _BotExc.PubobotException)" in src
	tree = _tree("bot/main.py")
	wrapper = _func(tree, "load_state")
	assert [ast.unparse(h.type) for h in ast.walk(wrapper) if isinstance(h, ast.ExceptHandler)] == ["_RESTORE_ERRORS"]
	restore_trys = [
		t for t in ast.walk(_func(tree, "_load_state"))
		if isinstance(t, ast.Try) and "from_json" in ast.unparse(ast.Module(body=t.body, type_ignores=[]))
	]
	assert len(restore_trys) == 2  # queues and matches
	for t in restore_trys:
		assert [ast.unparse(h.type) for h in t.handlers] == ["_RESTORE_ERRORS"]


# ── bot/match/match.py ───────────────────────────────────────────────────────

def test_snapshot_carries_what_a_restore_needs():
	ser = _func(_tree("bot/match/match.py"), "serialize", cls="Match")
	keys = {kw.arg for call in ast.walk(ser) if isinstance(call, ast.Call) for kw in call.keywords}
	for key in (
		"match_id", "fill_subs", "season_number", "team_names", "team_emojis",
		"captains", "party_code", "check_in_message_id",
	):
		assert key in keys, key


def test_restore_keeps_the_original_match_id():
	"""It used to call next_match() and renumber the match, so `/match report
	<id>` and the saved history no longer matched."""
	fj = _func(_tree("bot/match/match.py"), "from_json", cls="Match")
	assert "match_id = data.get('match_id')" in ast.unparse(fj)
	fallbacks = [
		n for n in ast.walk(fj)
		if isinstance(n, ast.If) and ast.unparse(n.test) == "match_id is None"
	]
	for call in ast.walk(fj):
		if isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "next_match":
			assert any(call in ast.walk(f) for f in fallbacks), "next_match() only for id-less snapshots"


def test_restore_brings_back_force_subs_season_and_names():
	src = ast.unparse(_func(_tree("bot/match/match.py"), "from_json", cls="Match"))
	for needle in ("match.fill_subs = ", "match.season_number = ", "team.name = name", "match.captains = "):
		assert needle in src, needle


def test_reports_refuse_a_match_that_is_already_finishing():
	tree = _tree("bot/match/match.py")
	for name in ("report_loss", "report_win", "record_abort"):
		body = _body_without_docstring(_func(tree, name, cls="Match"))
		assert ast.unparse(body[0]) == "self._ensure_active()", name
	assert "self not in bot.active_matches" in ast.unparse(_func(tree, "_ensure_active", cls="Match"))


def test_finish_match_stops_listening_for_game_codes():
	src = ast.unparse(_func(_tree("bot/match/match.py"), "finish_match", cls="Match"))
	assert src.index("bot.active_matches.remove(self)") < src.index("self.party_code.cancel()")


def test_manual_results_are_stamped_with_the_season():
	src = ast.unparse(_func(_tree("bot/match/match.py"), "fake_ranked_match", cls="Match"))
	assert src.index("match.season_number = ") < src.index("register_match_ranked")


# ── bot/match/party_code.py ──────────────────────────────────────────────────

def test_code_listeners_are_keyed_by_match_not_channel():
	"""Keyed by channel, a second match replaced the first one's listener."""
	src = _src("bot/match/party_code.py")
	assert "_waiting_codes[self.m.id] = self" in src
	assert "_waiting_codes[self.m.qc.id]" not in src
	assert "_waiting_codes.get(message.channel.id)" not in src
	assert "_waiting_codes.pop(self.m.id, None)" in src


def test_party_code_round_trips_for_restore():
	tree = _tree("bot/match/party_code.py")
	ser = _func(tree, "serialize", cls="PartyCode")
	keys = {kw.arg for call in ast.walk(ser) if isinstance(call, ast.Call) for kw in call.keywords}
	restore_src = ast.unparse(_func(tree, "restore", cls="PartyCode"))
	for key in keys:
		assert f"'{key}'" in restore_src, f"restore() ignores {key}"


# ── bot/queues/pickup_queue.py ───────────────────────────────────────────────

def test_priority_protected_players_leave_the_queue_that_popped():
	# _drop_protected() takes them out of every queue that isn't higher-priority,
	# this one included (behaviour: tests/test_priority_handoff.py).
	src = ast.unparse(_func(_tree("bot/queues/pickup_queue.py"), "start", cls="PickupQueue"))
	drop = src.index("self._drop_protected(players, protected_ids, my_priority)")
	assert src.index("exclude=protected_ids") < drop
	assert drop < src.index("bot.Match.new(ctx, self, players, team_size=team_size, **self._match_cfg())")
