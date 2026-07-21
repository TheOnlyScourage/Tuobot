# -*- coding: utf-8 -*-
"""The tuonela clause: per-player absolute rating ceilings.

`cap_rating` (bot/stats/mmr_engine.py) is the pure clamp; bot/constants.py
carries WHO is capped (RATING_CAPS) and the verbatim taunt (CAP_MESSAGES).
The AST test pins the stats.py wiring so a future paste-over can't silently
drop it — this repo's classic failure mode is the partial commit.
"""
import ast
from pathlib import Path

TUONELA = 449913356506365972
ROOT = Path(__file__).resolve().parents[1]


# ── The pure clamp ────────────────────────────────────────────────────────────

def test_no_cap_is_passthrough(mmr_engine):
	assert mmr_engine.cap_rating(2350, None) == (2350, False)


def test_below_cap_untouched(mmr_engine):
	assert mmr_engine.cap_rating(2150, 2199) == (2150, False)


def test_landing_exactly_on_cap_is_not_a_crossing(mmr_engine):
	# 2180 + 19 = 2199: reached the ceiling legitimately -> no taunt.
	assert mmr_engine.cap_rating(2199, 2199) == (2199, False)


def test_star_threshold_is_blocked(mmr_engine):
	# 2200 would be Star rank -> parked at 2199, crossing flagged.
	assert mmr_engine.cap_rating(2200, 2199) == (2199, True)


def test_far_overshoot_still_parks_on_cap(mmr_engine):
	assert mmr_engine.cap_rating(2344, 2199) == (2199, True)


# ── The constants ─────────────────────────────────────────────────────────────

def test_tuonela_cap_sits_one_below_star(constants):
	star = max(t for t, _ in constants.RANK_EMOJIS)
	assert star == 2200
	assert constants.RATING_CAPS == {TUONELA: 2199}
	assert constants.RATING_CAPS[TUONELA] == star - 1


def test_taunt_is_verbatim(constants):
	assert constants.CAP_MESSAGES[TUONELA] == (
		"No star rank for you tuoneLa<:loser:1529186680106389691>"
	)


def test_every_cap_has_a_message(constants):
	assert set(constants.RATING_CAPS) == set(constants.CAP_MESSAGES)


# ── The wiring ────────────────────────────────────────────────────────────────

def test_stats_wiring_present():
	"""register_match_ranked must call cap_rating and reference both cap
	constants — guards the wiring against a partial paste-over."""
	src = (ROOT / "bot" / "stats" / "stats.py").read_text(encoding="utf-8")
	tree = ast.parse(src)
	fn = next(
		n for n in ast.walk(tree)
		if isinstance(n, ast.AsyncFunctionDef) and n.name == "register_match_ranked"
	)
	calls = {
		n.func.id for n in ast.walk(fn)
		if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
	}
	names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
	assert "cap_rating" in calls
	assert "RATING_CAPS" in names
	assert "CAP_MESSAGES" in names
