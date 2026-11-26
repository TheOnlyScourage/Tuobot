# -*- coding: utf-8 -*-
"""Queue priority never puts one player in two matches (Nov 26).

- When a lower-priority queue pops, priority-protected players stay ONLY in
  the higher-priority queues. Every other queue drops them at once, so a 2v2
  can't fill during the 1v1 check-in and pull them into a second match.
- When a higher-priority queue pops (the 6v6) with players who are still in a
  lower-priority check-in (a 1v1), that check-in lets them go with no strike,
  and everyone else goes back to the queue.

The real methods are pulled out of the source with ast and run against small
fakes, like the other guard files, so the suite needs nothing but pytest."""
import __future__
import ast
import asyncio
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tree(rel):
	return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _methods(rel, cls, names, ns):
	"""Compile just these methods of `cls` into namespace `ns`."""
	klass = next(n for n in _tree(rel).body if isinstance(n, ast.ClassDef) and n.name == cls)
	picked = [n for n in klass.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
	assert len(picked) == len(names), f"missing in {rel}"
	code = compile(
		ast.Module(body=picked, type_ignores=[]), str(ROOT / rel), "exec",
		flags=__future__.annotations.compiler_flag, dont_inherit=True
	)
	exec(code, ns)
	return ns


class _PubobotException(BaseException):
	pass


def _member(uid, name):
	return types.SimpleNamespace(id=uid, display_name=name, mention=f"<@{name}>")


ODA, GBK, SIX = _member(1, "Oda"), _member(2, "GreenBlackKatt"), _member(3, "Six1")


class _Log:
	def __init__(self):
		self.errors = []

	def error(self, msg):
		self.errors.append(msg)


# ── lower-priority pops: protected players are dropped everywhere else ────────

def _queue_class(ns):
	_methods("bot/queues/pickup_queue.py", "PickupQueue",
	         {"pop_members", "is_added", "_drop_protected"}, ns)

	class Queue:
		pop_members = ns["pop_members"]
		is_added = ns["is_added"]
		_drop_protected = ns["_drop_protected"]

		def __init__(self, name, priority, players, qc):
			self.name = name
			self.cfg = types.SimpleNamespace(priority=priority)
			self.queue = list(players)
			self.standby = []
			self.qc = qc
			qc.queues.append(self)

		@property
		def length(self):
			return len(self.queue)

	return Queue


def test_protected_players_stay_only_in_higher_priority_queues():
	cancelled = []
	fake_bot = types.SimpleNamespace(
		queue_channels={}, active_queues=[],
		expire=types.SimpleNamespace(cancel=lambda qc, m: cancelled.append((qc.name, m.display_name))),
	)
	Queue = _queue_class({"bot": fake_bot})
	ch6 = types.SimpleNamespace(name="#6v6", queues=[])
	ch_small = types.SimpleNamespace(name="#small", queues=[])
	fake_bot.queue_channels = {1: ch6, 2: ch_small}
	six = Queue("6v6", 100, [ODA], ch6)
	two = Queue("2v2", 0, [ODA, SIX], ch_small)
	one = Queue("1v1", 0, [ODA, GBK], ch_small)
	fake_bot.active_queues = [six, two, one]

	one._drop_protected([ODA, GBK], {ODA.id}, 0)  # the 1v1 popped; Oda is protected by the 6v6

	assert six.queue == [ODA]                # the 6v6 keeps her
	assert ODA not in two.queue and two.queue == [SIX]  # the 2v2 can't pull her into a 2nd match
	assert ODA not in one.queue              # the queue that popped drops her
	assert GBK in one.queue                  # unprotected players aren't touched here
	assert cancelled == [("#small", "Oda")]  # her timer stops where she's no longer queued


def test_an_empty_queue_leaves_the_active_list():
	fake_bot = types.SimpleNamespace(queue_channels={}, active_queues=[],
	                                 expire=types.SimpleNamespace(cancel=lambda qc, m: None))
	Queue = _queue_class({"bot": fake_bot})
	ch = types.SimpleNamespace(name="#small", queues=[])
	fake_bot.queue_channels = {1: ch}
	two = Queue("2v2", 0, [ODA], ch)
	one = Queue("1v1", 0, [ODA], ch)
	fake_bot.active_queues = [two, one]
	one._drop_protected([ODA], {ODA.id}, 0)
	assert fake_bot.active_queues == []


# ── higher-priority pops: lower check-ins let its players go ─────────────────

def _fake_match(mid, queue_name, priority, state, players):
	released = []

	async def release(ctx, members, to_queue):
		released.append((members, to_queue))

	m = types.SimpleNamespace(
		id=mid, state=state, CHECK_IN=1, players=players, qc=queue_name,
		queue=types.SimpleNamespace(name=queue_name, cfg=types.SimpleNamespace(priority=priority)),
		check_in=types.SimpleNamespace(release=release),
	)
	return m, released


def test_a_higher_priority_pop_releases_its_players_from_lower_check_ins():
	in_1v1_checkin, rel_a = _fake_match(10, "1v1", 0, 1, [ODA, GBK])
	in_1v1_game, rel_b = _fake_match(11, "1v1", 0, 3, [SIX])               # past check-in: left alone
	in_equal_checkin, rel_c = _fake_match(12, "6v6-b", 100, 1, [_member(4, "X")])
	fake_bot = types.SimpleNamespace(
		active_matches=[in_1v1_checkin, in_1v1_game, in_equal_checkin],
		SystemContext=lambda qc: f"ctx:{qc}",
		Exc=types.SimpleNamespace(PubobotException=_PubobotException),
	)
	ns = _methods("bot/queues/pickup_queue.py", "PickupQueue", {"_release_from_lower_check_ins"},
	              {"bot": fake_bot, "log": _Log(), "traceback": __import__("traceback")})
	six = types.SimpleNamespace(name="6v6")
	asyncio.run(ns["_release_from_lower_check_ins"](six, [ODA, SIX, _member(4, "X")], 100))
	assert rel_a == [([ODA], "6v6")]   # Oda leaves the 1v1 check-in; GreenBlackKatt isn't named
	assert rel_b == []                 # a 1v1 already being played is not touched
	assert rel_c == []                 # equal priority never releases anyone


def test_a_failed_release_never_breaks_the_6v6_start():
	async def boom(ctx, members, to_queue):
		raise RuntimeError("discord hiccup")

	m, _ = _fake_match(10, "1v1", 0, 1, [ODA])
	m.check_in.release = boom
	log = _Log()
	fake_bot = types.SimpleNamespace(active_matches=[m], SystemContext=lambda qc: qc,
	                                 Exc=types.SimpleNamespace(PubobotException=_PubobotException))
	ns = _methods("bot/queues/pickup_queue.py", "PickupQueue", {"_release_from_lower_check_ins"},
	              {"bot": fake_bot, "log": log, "traceback": __import__("traceback")})
	asyncio.run(ns["_release_from_lower_check_ins"](types.SimpleNamespace(name="6v6"), [ODA], 100))
	assert log.errors and "couldn't release" in log.errors[0]


def test_release_frees_the_check_in_without_a_strike():
	reverted, notices, deleted = [], [], []

	async def revert(ctx, not_ready, ready):
		reverted.append((not_ready, ready))

	async def no_strikes(*a, **k):
		raise AssertionError("released players must not get a strike")

	class Msg:
		id = 99

		async def delete(self):
			deleted.append(True)

	match = types.SimpleNamespace(
		players=[ODA, GBK], ranked=True, gt=lambda s: s,
		queue=types.SimpleNamespace(name="1v1", revert=revert),
	)
	fake_bot = types.SimpleNamespace(active_matches=[match], waiting_reactions={99: "handler"})
	ns = _methods("bot/match/check_in.py", "CheckIn", {"release", "_begin_close", "_safe_notice"}, {
		"bot": fake_bot, "DiscordException": Exception, "log": _Log(), "record_violation": no_strikes,
		"join_and": lambda xs: " & ".join(xs),
	})

	class Ctx:
		async def notice(self, text):
			notices.append(text)

	ci = types.SimpleNamespace(m=match, message=Msg(), _closing=False)
	for name in ("_begin_close", "_safe_notice"):
		setattr(ci, name, types.MethodType(ns[name], ci))

	asyncio.run(ns["release"](ci, Ctx(), [ODA], "6v6"))
	assert reverted == [([ODA], [GBK])]               # GreenBlackKatt back to the 1v1 queue
	assert "moved to the **6v6** match" in notices[0]
	assert fake_bot.active_matches == [] and fake_bot.waiting_reactions == {} and deleted
	asyncio.run(ns["release"](ci, Ctx(), [ODA], "6v6"))  # a second call is a no-op
	assert len(reverted) == 1


def test_start_releases_only_after_its_own_match_exists():
	klass = next(n for n in _tree("bot/queues/pickup_queue.py").body
	             if isinstance(n, ast.ClassDef) and n.name == "PickupQueue")
	start = next(n for n in klass.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "start")
	src = ast.unparse(start)
	assert src.index("bot.Match.new(ctx, self, players, team_size=team_size, **self._match_cfg())") \
		< src.index("await self._release_from_lower_check_ins(players, my_priority)")
