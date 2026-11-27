# -*- coding: utf-8 -*-
"""No more error 40060 "Interaction has already been acknowledged" (Nov 26).

A Join press answered right as run_slash's 2.5 s defer was on its way: both
sent a first response, Discord refused the second, and the bot posted the
error publicly. SlashContext now sends every first response one at a time.

The real SlashContext methods are pulled out with ast and run against a fake
interaction that follows nextcord's rule (an interaction counts as answered
only once Discord has replied) and Discord's rule (a second first response
is refused with 40060), so the suite needs nothing but pytest."""
import __future__
import ast
import asyncio
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTX_FILE = "bot/context/slash/context.py"


def _tree(rel):
	return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _class(tree, name):
	return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _func(scope, name):
	return next(n for n in ast.walk(scope) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


class _Refused(Exception):
	pass


class FakeInteraction:
	def __init__(self, latency=0.04):
		self.latency, self.acked, self.log = latency, False, []
		self.user = types.SimpleNamespace(send=self._dm)
		self.channel = types.SimpleNamespace(send=self._channel)
		inter = self

		class Response:
			_responded = False

			def is_done(self):
				return self._responded

			async def _first(self, kind, **k):
				await asyncio.sleep(inter.latency / 2)  # on its way to Discord
				refused, inter.acked = inter.acked, True
				await asyncio.sleep(inter.latency / 2)  # Discord's answer on its way back
				if refused:
					inter.log.append(f"{kind} REFUSED 40060")
					raise _Refused("40060")
				inter.log.append(kind + (" private" if k.get("ephemeral") else ""))
				self._responded = True

			async def send_message(self, *a, **k):
				await self._first("reply", **k)

			async def defer(self, *a, **k):
				await self._first("defer", **k)

		class Followup:
			async def send(self, *a, **k):
				inter.log.append("followup " + ("private" if k.get("ephemeral") else "PUBLIC"))

		self.response, self.followup = Response(), Followup()

	async def _dm(self, *a, **k):
		self.log.append("dm")

	async def _channel(self, *a, **k):
		self.log.append("channel")


def _ctx(latency=0.04):
	"""A SlashContext built from the real methods (minus __init__, which needs
	the bot), with the same state __init__ sets up."""
	klass = _class(_tree(CTX_FILE), "SlashContext")
	methods = [n for n in klass.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name != "__init__"]
	ns = {"asyncio": asyncio, "error_embed": lambda *a, **k: ("error", a), "ok_embed": lambda *a, **k: ("ok", a)}
	exec(compile(ast.Module(body=methods, type_ignores=[]), str(ROOT / CTX_FILE), "exec",
	             flags=__future__.annotations.compiler_flag, dont_inherit=True), ns)
	cls = type("Ctx", (), {m.name: ns[m.name] for m in methods})
	ctx = cls()
	ctx.interaction = FakeInteraction(latency)
	ctx._answering = asyncio.Lock()
	ctx._deferred = False
	return ctx


def test_init_sets_up_the_lock_and_flag():
	init = ast.unparse(_func(_class(_tree(CTX_FILE), "SlashContext"), "__init__"))
	assert "self._answering = asyncio.Lock()" in init and "self._deferred = False" in init


def test_a_reply_while_the_defer_is_in_flight_becomes_a_private_followup():
	async def run():
		ctx = _ctx()
		defer = asyncio.create_task(ctx.defer())
		await asyncio.sleep(0.01)                 # the defer is on its way
		await ctx.ignore("Action had no effect.")  # the Nov 26 press, already queued
		assert await defer is True
		return ctx.interaction.log
	log = asyncio.run(run())
	assert not any("REFUSED" in x for x in log), log
	assert log == ["defer", "followup private"]


def test_a_defer_while_the_reply_is_in_flight_stands_down():
	async def run():
		ctx = _ctx()
		reply = asyncio.create_task(ctx.notice("6v6-Ranked (11/12)"))
		await asyncio.sleep(0.01)                 # the reply is on its way
		deferred = await ctx.defer()
		await reply
		return deferred, ctx.interaction.log
	deferred, log = asyncio.run(run())
	assert deferred is False and log == ["reply"]


def test_errors_stay_private_even_after_the_first_reply():
	async def run():
		ctx = _ctx()
		await ctx.notice("topic")
		await ctx.error("Something went wrong")
		return ctx.interaction.log
	assert asyncio.run(run()) == ["reply", "followup private"]


def test_ignore_adds_nothing_after_a_real_reply():
	async def run():
		ctx = _ctx()
		await ctx.reply("phrase")
		await ctx.ignore("Done.")
		await ctx.notice("topic")
		return ctx.interaction.log
	assert asyncio.run(run()) == ["reply", "channel"]


def test_run_slash_defers_through_ctx():
	src = ast.unparse(_func(_tree("bot/context/slash/commands.py"), "run_slash"))
	assert "await ctx.defer()" in src
	assert "interaction.response.defer()" not in src


def test_activity_defers_through_ctx():
	src = ast.unparse(_func(_tree("bot/commands/stats.py"), "activity"))
	assert "await ctx.defer()" in src
	assert "interaction.response.defer()" not in src
