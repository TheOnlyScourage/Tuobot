# -*- coding: utf-8 -*-
"""Guards for the Nov 2026 Batch 2 match-flow fixes: one exit from check-in,
channel pings, sub_player during check-in, /subauto keeping the draft,
/ready answering, crashed matches announced instead of vanishing.

These files import nextcord, so the checks read the source with ast. The
behaviour was verified against the real classes when the fixes were written;
these stop a partial paste-over from quietly undoing them."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tree(rel):
	return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _func(tree, name, cls=None):
	scope = tree
	if cls:
		scope = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
	return next(
		n for n in ast.walk(scope)
		if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
	)


def _body(fn):
	return [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]


def _first_await_line(nodes):
	lines = [n.lineno for stmt in nodes for n in ast.walk(stmt) if isinstance(n, ast.Await)]
	return min(lines) if lines else float("inf")


# ── check-in: exactly one exit ───────────────────────────────────────────────

CLAIM = "if not self._begin_close():\n    return"


def test_every_check_in_exit_claims_the_close_before_awaiting():
	"""A ready-up landing on the deadline used to run two exits at once: all 12
	players got a missed-check-in strike, then a draft started for a dead match."""
	tree = _tree("bot/match/check_in.py")
	for name in ("finish", "finish_race", "abort_member", "abort_timeout"):
		body = _body(_func(tree, name, cls="CheckIn"))
		assert ast.unparse(body[0]) == CLAIM, name
	refresh = _func(tree, "refresh", cls="CheckIn")
	discard_branch = next(
		n for n in ast.walk(refresh)
		if isinstance(n, ast.If) and "discarded_players" in ast.unparse(n.test)
	)
	assert ast.unparse(discard_branch.body[0]) == CLAIM
	assert discard_branch.body[0].lineno < _first_await_line(discard_branch.body)


def test_nothing_acts_once_check_in_is_closing():
	tree = _tree("bot/match/check_in.py")
	for name in ("think", "refresh"):
		first = _body(_func(tree, name, cls="CheckIn"))[0]
		assert ast.unparse(first) == "if self._closing:\n    return", name
	assert "self._closing" in ast.unparse(_func(tree, "set_ready", cls="CheckIn"))


# ── check-in is buttons only (Nov 26) ────────────────────────────────────────

def test_check_in_is_buttons_only():
	"""The ✅ / ⛔ reactions did what the buttons do. Removed: one way to check
	in, three fewer Discord calls per check-in. /ready and /notready stay."""
	src = (ROOT / "bot/match/check_in.py").read_text(encoding="utf-8")
	klass = next(n for n in _tree("bot/match/check_in.py").body if isinstance(n, ast.ClassDef) and n.name == "CheckIn")
	names = {n.name for n in klass.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
	names |= {t.id for n in klass.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
	assert not {"process_reaction", "READY_EMOJI", "NOT_READY_EMOJI"} & names
	assert "add_reaction" not in src and "waiting_reactions" not in src
	start = ast.unparse(_func(_tree("bot/match/check_in.py"), "start", cls="CheckIn"))
	assert "view=CheckInButtons(allow_discard=self.allow_discard)" in start


def test_nothing_tells_players_to_react_to_check_in():
	board = ast.unparse(_func(_tree("bot/match/embeds.py"), "check_in"))
	standby = (ROOT / "bot/match/standby.py").read_text(encoding="utf-8")
	assert "react" not in board.lower() and "react" not in standby.lower()


def test_party_up_keeps_its_reaction_and_nothing_handles_un_reacts():
	events = _tree("bot/events.py")
	handlers = {n.name for n in events.body if isinstance(n, ast.AsyncFunctionDef)}
	assert "on_reaction_add" in handlers           # the party-up ✅ still needs it
	assert "on_raw_reaction_remove" not in handlers  # only check-in used un-reacts
	party = (ROOT / "bot/match/party_code.py").read_text(encoding="utf-8")
	assert "bot.waiting_reactions[self.party_message_id] = self._process_reaction" in party


def test_race_finish_uses_the_unguarded_half():
	"""finish_race() claims the close first, so finish() would do nothing."""
	src = ast.unparse(_func(_tree("bot/match/standby.py"), "finalize_race_results"))
	assert "await ci._finish(ctx)" in src
	assert "await ci.finish(ctx)" not in src


def test_non_draft_teams_are_rebuilt_after_the_race():
	src = ast.unparse(_func(_tree("bot/match/standby.py"), "finalize_race_results"))
	assert "match.init_teams(match.cfg['pick_teams'])" in src


# ── pings ────────────────────────────────────────────────────────────────────

def test_check_in_board_pings_in_the_message_text():
	"""Mentions inside an embed never notify; the board used to be text-less."""
	src = ast.unparse(_func(_tree("bot/match/check_in.py"), "start", cls="CheckIn"))
	assert "!spawn message" not in src
	assert "content=self._ping_line(not_ready)" in src


def test_draft_board_pings_the_captain_on_the_clock():
	src = ast.unparse(_func(_tree("bot/match/draft.py"), "print", cls="Draft"))
	assert "content=content" in src and "self.picking_captain()" in src


# ── subs ─────────────────────────────────────────────────────────────────────

def test_sub_player_during_check_in_passes_ctx():
	src = ast.unparse(_func(_tree("bot/match/draft.py"), "sub_for", cls="Draft"))
	assert "await self.m.check_in.refresh(ctx)" in src
	assert "self._swap_in(player1, player2)" in src


def test_swap_clears_the_old_players_check_in_state():
	src = ast.unparse(_func(_tree("bot/match/draft.py"), "_swap_in", cls="Draft"))
	for needle in ("ci.ready_players.discard(player1)", "ci.ready_order.remove(player1)", "self.m.captains["):
		assert needle in src, needle


def test_subauto_keeps_the_draft():
	"""It re-ran matchmaking: one unpicked player auto-drafted the whole lobby."""
	src = ast.unparse(_func(_tree("bot/match/draft.py"), "sub_auto", cls="Draft"))
	assert "init_teams" not in src
	assert "self._swap_in(author, candidate)" in src


# ── commands / events ────────────────────────────────────────────────────────

def test_ready_answers_the_interaction():
	src = ast.unparse(_func(_tree("bot/commands/matches.py"), "set_ready"))
	assert src.index("check_in.set_ready") < src.index("await ctx.ignore(")


def test_crashed_matches_are_announced_and_cancelled():
	tree = _tree("bot/events.py")
	think = ast.unparse(_func(tree, "on_think"))
	assert "except (Exception, bot.Exc.PubobotException)" in think
	assert "await _drop_crashed_match(match)" in think
	drop = ast.unparse(_func(tree, "_drop_crashed_match"))
	assert "await match.cancel(ctx)" in drop and "ctx.error(" in drop


def test_no_user_facing_text_names_the_nonexistent_admin_command():
	"""There is no /admin parent command (groups are /channel /queue /match /rating
	/stats /noadds /phrases). Docstrings and comments are fine; strings shown to
	people are not."""
	offenders = []
	for path in (ROOT / "bot").rglob("*.py"):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		docstrings = {
			id(n.body[0].value) for n in ast.walk(tree)
			if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
			and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
		}
		offenders += [
			f"{path.relative_to(ROOT)}:{n.lineno}" for n in ast.walk(tree)
			if isinstance(n, ast.Constant) and isinstance(n.value, str)
			and id(n) not in docstrings and "/admin " in n.value
		]
	assert not offenders, offenders
