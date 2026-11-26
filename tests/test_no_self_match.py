# -*- coding: utf-8 -*-
"""Nobody is ever matched against themselves.

On Nov 11 (Worpel) and Nov 20 (GreenBlackKatt) a 1v1 started with the same
player on both sides, and saving the result failed. bot/queues/roster_rules.py
(pure, loaded by path) keeps every person to one slot. The wiring guards
check that the queue's revert() and start() and Match.new() actually use it."""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace as P

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("roster_rules", ROOT / "bot" / "queues" / "roster_rules.py")
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)

ODA, GBK, WORPEL, X = P(id=1, name="Oda"), P(id=2, name="GreenBlackKatt"), P(id=3, name="Worpel"), P(id=4, name="X")


def names(players):
	return [p.name for p in players]


def test_distinct_keeps_the_first_of_each():
	assert names(rules.distinct([GBK, ODA, GBK, X, ODA])) == ["GreenBlackKatt", "Oda", "X"]


def test_the_same_id_is_the_same_person():
	# two objects for one Discord account (a re-fetched member, say)
	assert names(rules.distinct([GBK, P(id=2, name="GreenBlackKatt again")])) == ["GreenBlackKatt"]


def test_nov_20_first_abort_does_not_refill_with_oda():
	# The Oct 26 code still listed Oda and GreenBlackKatt in the 1v1 after it
	# started, so Oda's abort re-popped the queue with her in it ("Wth").
	ready, standby, queued = rules.distinct_across([ODA], [GBK], [], [ODA, GBK])
	assert (names(ready), standby, queued) == (["GreenBlackKatt"], [], [])


def test_nov_20_second_abort_cannot_self_match():
	ready, standby, queued = rules.distinct_across([ODA], [GBK], [], [GBK])
	assert names(ready + standby + queued) == ["GreenBlackKatt"]  # 1/2, not a 1v1 vs himself


def test_nov_11_timeout_cannot_self_match():
	ready, standby, queued = rules.distinct_across([ODA], [WORPEL], [], [WORPEL, ODA])
	assert names(ready + standby + queued) == ["Worpel"]


def test_standby_keeps_its_order_and_nobody_is_listed_twice():
	ready, standby, queued = rules.distinct_across([ODA], [GBK], [X, GBK, WORPEL], [WORPEL, X, ODA])
	assert names(ready) == ["GreenBlackKatt"]
	assert names(standby) == ["X", "Worpel"]
	assert queued == []


def test_nothing_to_drop_changes_nothing():
	ready, standby, queued = rules.distinct_across([], [GBK], [X], [WORPEL])
	assert (names(ready), names(standby), names(queued)) == (["GreenBlackKatt"], ["X"], ["Worpel"])


# ── wiring: the queue and Match.new really use the guards ─────────────────────

def _tree(rel):
	return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _method(rel, cls, name):
	klass = next(n for n in _tree(rel).body if isinstance(n, ast.ClassDef) and n.name == cls)
	return next(n for n in klass.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _calls(fn, name):
	out = []
	for n in ast.walk(fn):
		if isinstance(n, ast.Call):
			f = n.func
			if (f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)) == name:
				out.append(n)
	return sorted(out, key=lambda n: n.lineno)


def test_pickup_queue_imports_the_helpers():
	imports = [n for n in _tree("bot/queues/pickup_queue.py").body
	           if isinstance(n, ast.ImportFrom) and n.module == "bot.queues.roster_rules"]
	assert imports and {"distinct", "distinct_across"} <= {a.name for a in imports[0].names}


def test_revert_drops_repeats_and_the_not_ready_before_refilling():
	fn = _method("bot/queues/pickup_queue.py", "PickupQueue", "revert")
	guard = _calls(fn, "distinct_across")
	assert guard, "revert() must rebuild the queue through distinct_across()"
	assert isinstance(guard[0].args[0], ast.Name) and guard[0].args[0].id == "not_ready"
	rebuilds = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
	            and any(isinstance(t, ast.Attribute) and t.attr == "queue" for t in n.targets)]
	assert guard[0].lineno < min(rebuilds)


def test_start_refuses_a_doubled_list_before_any_match_is_made():
	fn = _method("bot/queues/pickup_queue.py", "PickupQueue", "start")
	guard, new = _calls(fn, "distinct"), _calls(fn, "new")
	assert guard and new
	assert guard[0].lineno < new[0].lineno


def test_match_new_refuses_the_same_player_twice_before_taking_an_id():
	fn = _method("bot/match/match.py", "Match", "new")
	checks = [n for n in ast.walk(fn) if isinstance(n, ast.If)
	          and ast.unparse(n.test) == "len({p.id for p in players}) != len(players)"]
	assert checks, "Match.new() must refuse a duplicate player"
	assert checks[0].lineno < _calls(fn, "next_match")[0].lineno
