# -*- coding: utf-8 -*-
"""Guards for the Nov 2026 Batch 7 buttons and menus.

- the persistent custom ids (bot/buttons_rules.py) and the pick-menu options
- every persistent view is built so nextcord doesn't store per-message copies,
  is registered at startup, and routes each press through run_slash
- every board carries its buttons (queue updates, check-in, draft, match)
- the new commands behind the pick menu and the We-lost button
- the mod/admin groups are hidden from players' slash menu

bot/buttons.py needs nextcord, so it is read with ast; the pure rules module
and single functions are loaded and run against small fakes. (The real wiring —
registration, payloads, and presses through nextcord's own dispatcher — was
checked on nextcord 2.6 and 3.2 when the buttons were written.)"""
import __future__
import ast
import asyncio
import importlib.util
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted(p for d in ("bot", "core") for p in (ROOT / d).rglob("*.py"))


def _load(name, rel):
	spec = importlib.util.spec_from_file_location(name, ROOT / rel)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


rules = _load("tuobot_buttons_rules", "bot/buttons_rules.py")


def _tree(rel):
	return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _class(tree, name):
	return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _func(tree, name, cls=None):
	scope = _class(tree, cls) if cls else tree
	return next(n for n in ast.walk(scope) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _extract(rel, names, ns):
	tree = _tree(rel)
	picked = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
	assert len(picked) == len(names)
	code = compile(ast.Module(body=picked, type_ignores=[]), str(ROOT / rel), "exec",
		flags=__future__.annotations.compiler_flag, dont_inherit=True)
	exec(code, ns)
	return ns


# ── Ids and the pick menu ─────────────────────────────────────────────────────

def test_custom_ids_are_unique_prefixed_and_short():
	declared = {v for k, v in vars(rules).items() if k.isupper() and isinstance(v, str) and v.startswith("tuobot:")}
	assert declared == set(rules.ALL_IDS)
	assert len(set(rules.ALL_IDS)) == len(rules.ALL_IDS)
	assert all(len(i) <= 100 for i in rules.ALL_IDS)


class _P:
	def __init__(self, id, nick):
		self.id, self.nick = id, nick


def test_pick_options_best_first_capped_and_clipped():
	players = [_P(i, f"player{i:02d}") for i in range(1, 31)]
	players.append(players[0])  # a duplicate is listed once
	opts = rules.pick_options(players, lambda p: p.nick, {p.id: 1000 + p.id for p in players})
	assert len(opts) == 25  # Discord's limit
	assert [o["value"] for o in opts[:3]] == ["30", "29", "28"]  # highest rated first
	assert opts[0]["description"] == "1030 rating"
	assert len({o["value"] for o in opts}) == 25

	unrated = rules.pick_options([_P(2, "bravo"), _P(1, "Alpha")], lambda p: p.nick)
	assert [o["label"] for o in unrated] == ["Alpha", "bravo"] and unrated[0]["description"] is None

	long = rules.pick_options([_P(9, "x" * 150)], lambda p: p.nick)
	assert len(long[0]["label"]) == 100 and long[0]["label"].endswith("…")


# ── The views ─────────────────────────────────────────────────────────────────

BUTTONS = _tree("bot/buttons.py")
PERSISTENT = [n.name for n in BUTTONS.body if isinstance(n, ast.ClassDef) and any(ast.unparse(b) == "_Persistent" for b in n.bases)]


def test_persistent_views_are_not_stored_per_message():
	init = ast.unparse(_func(BUTTONS, "__init__", cls="_Persistent"))
	assert "super().__init__(timeout=None, prevent_update=False)" in init
	assert set(PERSISTENT) == {"QueueButtons", "CheckInButtons", "DraftPickMenu", "ReportButtons"}


def test_every_persistent_item_has_a_fixed_id_and_the_confirm_has_none():
	used = set()
	for name in PERSISTENT:
		for n in ast.walk(_class(BUTTONS, name)):
			if isinstance(n, ast.Call) and ast.unparse(n.func) in ("nextcord.ui.button", "nextcord.ui.StringSelect"):
				custom = next((k.value for k in n.keywords if k.arg == "custom_id"), None)
				assert custom is not None, f"{name} has an item without a custom_id"
				used.add(getattr(rules, ast.unparse(custom).split(".")[-1]))
	assert used == set(rules.ALL_IDS)
	confirm = _class(BUTTONS, "ConfirmLoss")
	assert not [n for n in ast.walk(confirm) if isinstance(n, ast.keyword) and n.arg == "custom_id"]


def test_register_adds_one_of_each_persistent_view():
	reg = ast.unparse(_func(BUTTONS, "register"))
	for name in PERSISTENT:
		assert f"{name}()" in reg
	assert "client.add_view(view)" in reg


def test_every_press_goes_through_run_slash():
	for name in PERSISTENT:
		for fn in ast.walk(_class(BUTTONS, name)):
			if isinstance(fn, ast.AsyncFunctionDef) and fn.name != "on_error":
				assert "await _slash(interaction," in ast.unparse(fn), f"{name}.{fn.name}"
	slash = _func(BUTTONS, "_slash")
	assert any(isinstance(n, ast.ImportFrom) and n.module == "bot.context.slash.commands" for n in slash.body), \
		"import run_slash inside _slash (a module-level import is an import cycle)"
	picked = ast.unparse(_func(BUTTONS, "_picked", cls="DraftPickMenu"))
	assert "interaction.data" in picked and "self.menu.values" not in picked


def test_maybe_never_hands_out_view_none():
	ns = _extract("bot/buttons.py", {"maybe"}, {})
	assert ns["maybe"](None) == {}
	marker = object()
	assert ns["maybe"](marker) == {"view": marker}


def test_buttons_are_listening_before_anything_else():
	on_init = _func(_tree("bot/events.py"), "on_init")
	assert ast.unparse(on_init.body[0]) == "register_buttons(dc)"


# ── Every board carries its buttons ───────────────────────────────────────────

def test_check_in_board_has_ready_buttons():
	start = ast.unparse(_func(_tree("bot/match/check_in.py"), "start", cls="CheckIn"))
	assert "view=CheckInButtons(allow_discard=self.allow_discard)" in start


def test_draft_board_has_the_pick_menu():
	draft = _tree("bot/match/draft.py")
	assert "**maybe(self.pick_menu())" in ast.unparse(_func(draft, "print", cls="Draft"))
	menu = ast.unparse(_func(draft, "pick_menu", cls="Draft"))
	assert "self.picking_captain() is None" in menu and "pick_options(" in menu
	assert "put" not in {n.name for n in _class(draft, "Draft").body if isinstance(n, ast.AsyncFunctionDef)}


def test_match_board_has_report_buttons_only_while_waiting():
	final = ast.unparse(_func(_tree("bot/match/match.py"), "final_message", cls="Match"))
	assert "ReportButtons() if self.state == self.WAITING_REPORT else None" in final
	stray = [
		f"{p.relative_to(ROOT)}:{n.lineno}" for p in SOURCES for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
		if isinstance(n, ast.Call) and "embeds.final_message()" in ast.unparse(n) and ast.unparse(n.func).endswith("notice")
		and p.name != "match.py"
	]
	assert not stray, f"post the board with match.final_message(ctx) so it gets its buttons: {stray}"


def test_every_public_queue_update_has_join_and_leave():
	tree = _tree("bot/commands/queues.py")
	posts = [
		n for n in ast.walk(tree)
		if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("notice", "reply")
		and n.args and ast.unparse(n.args[0]) == "ctx.qc.topic"
	]
	assert len(posts) >= 6
	assert all(any(k.arg == "view" and ast.unparse(k.value) == "QueueButtons()" for k in n.keywords) for n in posts)


def test_no_interaction_response_gets_view_none():
	hits = []
	for path in SOURCES:
		for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
			if (
				isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr != "edit_message"
				and any(k.arg == "view" and isinstance(k.value, ast.Constant) and k.value.value is None for k in n.keywords)
			):
				hits.append(f"{path.relative_to(ROOT)}:{n.lineno} {ast.unparse(n.func)}")
	assert not hits, hits


# ── The commands behind the menu and the We-lost button ───────────────────────

class _Err(BaseException):
	pass


class _Exc:
	NotFoundError = type("NotFoundError", (_Err,), {})
	PermissionError = type("PermissionError", (_Err,), {})
	MatchStateError = type("MatchStateError", (_Err,), {})


class _Team(list):
	def __init__(self, members, name):
		super().__init__(members)
		self.name = name


def _match_world(state="draft"):
	a, b, c, d, e = (types.SimpleNamespace(id=i) for i in range(1, 6))
	picks = []

	class Draft:
		async def pick(self, ctx, author, players):
			picks.append((author, players))

	match = types.SimpleNamespace(
		id=77, qc="QC", players=[a, b, c, d, e], WAITING_REPORT="waiting", state=state, draft=Draft(),
		teams=[_Team([a], "Gryffindor"), _Team([b], "Slytherin"), _Team([c, d], "unpicked")],
	)
	fake_bot = types.SimpleNamespace(active_matches=[match], Exc=_Exc)

	class ConfirmLoss:
		pass

	ns = _extract("bot/commands/matches.py", {"author_match", "pick_by_id", "confirm_loss"}, {
		"bot": fake_bot, "find": lambda pred, it: next((x for x in it if pred(x)), None),
		"wraps": __import__("functools").wraps, "ConfirmLoss": ConfirmLoss,
	})
	return ns, match, picks, (a, b, c, d, e), ConfirmLoss


def _ctx(author):
	sent = []

	async def ignore(**kwargs):
		sent.append(kwargs)

	return types.SimpleNamespace(qc="QC", author=author, ignore=ignore, gt=lambda s: s), sent


def test_pick_menu_picks_by_id():
	ns, match, picks, (a, _b, c, _d, _e), _ = _match_world()
	ctx, _ = _ctx(a)
	ctx.qc = types.SimpleNamespace(gt=lambda s: s)
	match.qc = ctx.qc
	asyncio.run(ns["pick_by_id"](ctx, user_id=c.id))
	assert picks == [(a, [c])]
	with pytest.raises(_Exc.NotFoundError):
		asyncio.run(ns["pick_by_id"](ctx, user_id=999))


def test_we_lost_asks_a_captain_first_and_refuses_everyone_else():
	ns, match, _picks, (a, _b, c, _d, _e), ConfirmLoss = _match_world(state="waiting")
	match.teams[2].clear()
	qc = types.SimpleNamespace(gt=lambda s: s)
	match.qc = qc
	captain, sent = _ctx(a)
	captain.qc = qc
	asyncio.run(ns["confirm_loss"](captain))
	assert len(sent) == 1 and "Gryffindor" in sent[0]["content"] and isinstance(sent[0]["view"], ConfirmLoss)

	player, sent = _ctx(c)
	player.qc = qc
	match.teams[0].append(c)
	with pytest.raises(_Exc.PermissionError):
		asyncio.run(ns["confirm_loss"](player))
	assert sent == []

	match.state = "draft"
	with pytest.raises(_Exc.MatchStateError):
		asyncio.run(ns["confirm_loss"](captain))


# ── Hidden mod menus ──────────────────────────────────────────────────────────

def test_mod_groups_are_hidden_from_players():
	groups = _tree("bot/context/slash/groups.py")
	hidden, visible = set(), set()
	for fn in ast.walk(groups):
		if isinstance(fn, ast.AsyncFunctionDef):
			for dec in fn.decorator_list:
				if isinstance(dec, ast.Call) and ast.unparse(dec.func) == "dc.slash_command":
					name = next(k.value.value for k in dec.keywords if k.arg == "name")
					(hidden if any(k.arg is None and ast.unparse(k.value) == "MOD_MENU" for k in dec.keywords) else visible).add(name)
	assert hidden == {"channel", "queue", "match", "rating", "noadds", "phrases"}
	assert visible == {"stats"}, "players use /stats show"
	assert "Permissions(manage_messages=True)" in (ROOT / "bot/context/slash/groups.py").read_text(encoding="utf-8")
	commands = (ROOT / "bot/context/slash/commands.py").read_text(encoding="utf-8")
	for name in ("swap", "autoreply"):
		line = next(ln for ln in commands.splitlines() if f"slash_command(name='{name}'" in ln)
		assert "**groups.MOD_MENU" in line, name
