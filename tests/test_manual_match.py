# -*- coding: utf-8 -*-
"""/match create (admin report_manual) — keyword-chain guard.

The call travels three hops:
  commands/matches.py report_manual  →  PickupQueue.fake_ranked_match
                                     →  Match.fake_ranked_match
The July 2026 aborts-replace-draws rename (draw → aborted) missed the middle
hop, so /match create crashed with "unexpected keyword argument 'aborted'"
for two months before anyone used it. This test parses all three hops and
asserts every keyword each caller passes is a parameter the callee names —
a partial paste-over of any one file now fails CI instead of production."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tree(rel):
	return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _func(tree, name):
	return next(
		n for n in ast.walk(tree)
		if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
	)


def _params(fn):
	return {a.arg for a in fn.args.args + fn.args.kwonlyargs}


def _calls_to(fn, attr):
	"""Keyword names of every call `<anything>.<attr>(...)` inside fn."""
	out = []
	for n in ast.walk(fn):
		if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr:
			out.append({kw.arg for kw in n.keywords if kw.arg is not None})
	return out


def test_report_manual_keywords_are_accepted_by_pickup_queue():
	caller = _func(_tree("bot/commands/matches.py"), "report_manual")
	callee = _func(_tree("bot/queues/pickup_queue.py"), "fake_ranked_match")
	calls = _calls_to(caller, "fake_ranked_match")
	assert calls, "report_manual no longer calls fake_ranked_match?"
	for kws in calls:
		assert kws <= _params(callee), f"pickup_queue.fake_ranked_match lacks {kws - _params(callee)}"


def test_pickup_queue_threads_aborted_into_match():
	mid = _func(_tree("bot/queues/pickup_queue.py"), "fake_ranked_match")
	end = _func(_tree("bot/match/match.py"), "fake_ranked_match")
	calls = _calls_to(mid, "fake_ranked_match")
	assert calls, "PickupQueue.fake_ranked_match no longer delegates to Match?"
	for kws in calls:
		assert "aborted" in kws, "aborted must be passed explicitly (not swallowed by **kwargs)"
		assert "draw" not in kws, "legacy `draw` keyword resurfaced"
	assert "aborted" in _params(end)
	assert "draw" not in _params(mid) and "draw" not in _params(end)
