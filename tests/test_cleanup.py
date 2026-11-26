# -*- coding: utf-8 -*-
"""Guards for the Nov 2026 Batch 5 cleanup: dead code removal and loose ends.

- the deleted files and helpers stay gone (nothing called them)
- background work goes through core.utils.spawn, never a fire-and-forget
  create_task()/ensure_future(); spawn keeps the task alive and logs failures
- a match that can't be created puts the lobby back in the queue
- /stats replace_player runs in one transaction, on the right channels
- standby notices are private, and rank-role updates survive any Discord error

Like the other guard files, functions are pulled out of the source with ast and
run against small fakes, so the suite needs nothing but pytest."""
import __future__
import ast
import asyncio
import sys
import traceback
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted(p for d in ("bot", "core") for p in (ROOT / d).rglob("*.py"))


def _tree(rel):
	return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _class(tree, name):
	return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _func(tree, name, cls=None):
	scope = _class(tree, cls) if cls else tree
	return next(
		n for n in ast.walk(scope)
		if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
	)


def _names(body):
	return {n.name for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))} | {
		t.id for n in body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)
	}


def _extract(rel, names, ns, cls=None):
	"""Run just the named functions/assignments of a file (or one class in it)."""
	tree = _tree(rel)
	scope = _class(tree, cls).body if cls else tree.body
	picked = [
		n for n in scope
		if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names)
		or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets))
	]
	assert len(picked) == len(names), f"missing in {rel}"
	code = compile(
		ast.Module(body=picked, type_ignores=[]), str(ROOT / rel), "exec",
		flags=__future__.annotations.compiler_flag, dont_inherit=True
	)
	exec(code, ns)
	return ns


# ── Dead code stays gone ──────────────────────────────────────────────────────

def test_dead_files_are_gone():
	for rel in ("bot/utils.py", "bot/context/message", "update_db.py"):
		assert not (ROOT / rel).exists(), f"{rel} was dead code (nothing imported or ran it)"


@pytest.mark.parametrize("rel,cls,gone", [
	("core/utils.py", None, {
		"EmojiFormatter", "random_string", "hl_user", "hl_role", "format_channel", "format_role",
		"format_emoji", "format_message", "escape", "reply", "discord_table",
	}),
	("core/client.py", "DiscordClient", {"command"}),
	("bot/exceptions.py", "Exceptions", {"NoEffect"}),
	("bot/commands/config.py", None, {"set_qc_cfg", "set_queue_cfg"}),
	("bot/match/match.py", "Match", {"rank_str", "report_scores"}),
	("bot/__init__.py", None, {"background_context"}),
])
def test_dead_helpers_are_gone(rel, cls, gone):
	tree = _tree(rel)
	body = _class(tree, cls).body if cls else tree.body
	assert not _names(body) & gone


# ── Background work ───────────────────────────────────────────────────────────

def test_no_fire_and_forget_tasks():
	"""A task nobody keeps a reference to can be garbage-collected mid-flight."""
	offenders = []
	for path in SOURCES:
		for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
			if (
				isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
				and ast.unparse(n.value.func).split(".")[-1] in ("create_task", "ensure_future")
			):
				offenders.append(f"{path.relative_to(ROOT)}:{n.lineno} {ast.unparse(n.value.func)}(...)")
	assert not offenders, f"use core.utils.spawn(...) instead: {offenders}"


class _FakeLog:
	def __init__(self):
		self.errors = []

	def error(self, text):
		self.errors.append(text)


def _spawn_ns(monkeypatch):
	log = _FakeLog()
	monkeypatch.setitem(sys.modules, "core.console", types.SimpleNamespace(log=log))
	ns = _extract("core/utils.py", {"_background_tasks", "spawn", "_background_done"}, {"asyncio": asyncio, "traceback": traceback})
	return ns, log


def test_spawn_keeps_the_task_alive_until_it_finishes(monkeypatch):
	ns, log = _spawn_ns(monkeypatch)

	async def scenario():
		gate = asyncio.Event()

		async def work():
			await gate.wait()
			return "done"

		task = ns["spawn"](work(), name="work")
		assert task in ns["_background_tasks"] and task.get_name() == "work"
		gate.set()
		assert await task == "done"
		await asyncio.sleep(0)  # let the done-callback run
		assert task not in ns["_background_tasks"]

	asyncio.run(scenario())
	assert log.errors == []


def test_spawn_logs_a_failure_instead_of_losing_it(monkeypatch):
	ns, log = _spawn_ns(monkeypatch)

	async def scenario():
		async def boom():
			raise RuntimeError("rank role update exploded")

		task = ns["spawn"](boom(), name="rank roles")
		with pytest.raises(RuntimeError):
			await task
		await asyncio.sleep(0)

	asyncio.run(scenario())
	assert len(log.errors) == 1
	assert "rank roles" in log.errors[0] and "rank role update exploded" in log.errors[0]


def test_send_dm_shrugs_off_closed_dms_only():
	class DiscordException(Exception):
		pass

	send_dm = _extract("core/utils.py", {"send_dm"}, {"DiscordException": DiscordException})["send_dm"]

	class Member:
		def __init__(self, exc=None):
			self.exc, self.sent = exc, []

		async def send(self, *args, **kwargs):
			if self.exc:
				raise self.exc
			self.sent.append(args)

	ok = Member()
	asyncio.run(send_dm(ok, "hi"))
	assert ok.sent == [("hi",)]
	asyncio.run(send_dm(Member(DiscordException("Cannot send messages to this user")), "hi"))
	with pytest.raises(RuntimeError):
		asyncio.run(send_dm(Member(RuntimeError("a real bug")), "hi"))


# ── A failed match start ──────────────────────────────────────────────────────

class _PubobotException(BaseException):
	pass


def test_a_failed_match_start_puts_everyone_back_in_the_queue(monkeypatch):
	async def cancel_for(queue, reason):
		return None

	monkeypatch.setitem(sys.modules, "bot.queues.recommend", types.SimpleNamespace(cancel_for=cancel_for))

	async def match_new(*args, **kwargs):
		raise RuntimeError("database went away")

	fake_bot = types.SimpleNamespace(
		Match=types.SimpleNamespace(new=match_new),
		Exc=types.SimpleNamespace(PubobotException=_PubobotException),
		allow_offline=[1], auto_ready={2: 999.0}, queue_channels={}, active_queues=[],
	)
	ns = _extract(
		"bot/queues/pickup_queue.py", {"start"}, cls="PickupQueue",
		ns={"bot": fake_bot, "log": _FakeLog(), "traceback": traceback, "SafeTemplateDict": dict},
	)
	players = [types.SimpleNamespace(id=i) for i in range(1, 13)]

	class Qc:
		def gt(self, text):
			return text

		async def queue_started(self, ctx, members, message=None, exclude=None):
			# what the real one does: out of every queue, offline immunity/auto-ready cleared
			queue.queue = [p for p in queue.queue if p not in members]
			fake_bot.allow_offline.clear()
			fake_bot.auto_ready.clear()

	queue = types.SimpleNamespace(
		name="6v6", qc=Qc(), queue=list(players),
		cfg=types.SimpleNamespace(size=12, team_size=None, start_direct_msg=None, server=None, priority=0),
		_match_cfg=lambda: {}, pop_members=lambda *ms: None, is_added=lambda p: False, length=0,
	)
	ctx = types.SimpleNamespace(channel=types.SimpleNamespace(mention="#pickups"))

	with pytest.raises(_PubobotException, match="back in the queue"):
		asyncio.run(ns["start"](queue, ctx))
	assert queue.queue == players, "the lobby should be back in the queue"
	assert queue in fake_bot.active_queues
	assert fake_bot.allow_offline == [1] and fake_bot.auto_ready == {2: 999.0}


# ── /stats replace_player ─────────────────────────────────────────────────────

def test_replace_player_is_one_transaction_on_the_right_channels():
	fn = _func(_tree("bot/stats/stats.py"), "replace_player")
	assert "rating_channel_id" in [a.arg for a in fn.args.args]
	withs = [w for w in ast.walk(fn) if isinstance(w, ast.AsyncWith)]
	assert withs and ast.unparse(withs[0].items[0].context_expr) == "db.transaction()"
	calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
	assert not [ast.unparse(c.func) for c in calls if ast.unparse(c.func.value) == "db" and c.func.attr != "transaction"]
	writes = {ast.literal_eval(c.args[0]): ast.unparse(c) for c in calls if ast.unparse(c.func.value) == "tx"}
	assert set(writes) == {"qc_players", "qc_rating_history", "qc_player_matches"}
	assert "rating_channel_id" in writes["qc_rating_history"] or "rated" in writes["qc_rating_history"]
	admin = ast.unparse(_func(_tree("bot/commands/admin.py"), "stats_replace_player"))
	assert "rating_channel_id=ctx.qc.rating.channel_id" in admin


# ── Notices and rank roles ────────────────────────────────────────────────────

def test_success_and_error_never_get_an_ephemeral_flag():
	"""ctx.success()/ctx.error() pass their kwargs to ok_embed()/error_embed():
	`ephemeral=True` raised a TypeError, and the fallback posted publicly."""
	hits = []
	for path in SOURCES:
		for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
			if (
				isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("success", "error")
				and any(k.arg == "ephemeral" for k in n.keywords)
			):
				hits.append(f"{path.relative_to(ROOT)}:{n.lineno}")
	assert not hits, hits


@pytest.mark.parametrize("fn", ["add", "add_player"])
def test_standby_notices_are_private(fn):
	code = ast.unparse(_func(_tree("bot/commands/queues.py"), fn))
	assert "On Standby" in code
	assert "await ctx.ignore(embed=ok_embed(" in code
	assert "spawn(send_dm(" in code


def test_rank_role_updates_survive_any_discord_error():
	fn = _func(_tree("bot/queue_channel.py"), "_update_rating_roles", cls="QueueChannel")
	caught = [ast.unparse(h.type) for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)]
	assert caught == ["DiscordException"], caught
