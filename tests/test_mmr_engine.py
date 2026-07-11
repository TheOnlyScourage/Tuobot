# -*- coding: utf-8 -*-
"""
Regression tests for bot/stats/mmr_engine.py — the single source of truth for
Q6 rating changes.

Two kinds of tests here:

  - HAND-COMPUTED: small scenarios whose expected deltas were worked out on
    paper from the documented formula (base 50 for equal teams, ±2.5/pick
    slot centered on the team midpoint, +10 flat captain bonus applied before
    the streak multiplier, +5%/streak-game beyond 1 capped at +25%, hard cap
    ±200, magnitude floored at 1).

  - GOLDEN / CHARACTERIZATION: bigger realistic scenarios whose expected
    dicts were captured from the engine as it stood when this suite was
    written (July 2026) — the same formula that was verified equivalent to
    live behaviour in the Version-A reconciliation. Their job is to detect
    ANY future drift in the formula. If one of these fails, either the
    change was intentional (update the golden and announce the balance
    change) or you just broke ratings.

Constants asserted up front so a silent retune of bot/constants.py also
fails loudly here instead of only shifting every expected value.
"""

import pytest


def team(member, ids):
	return [member(i) for i in ids]


def ratings(pairs):
	"""{id: (rating, streak)} -> {id: row_dict} as rating.get_players returns."""
	return {
		uid: dict(rating=rating, streak=streak)
		for uid, (rating, streak) in pairs.items()
	}


def test_constants_are_what_these_tests_assume(constants):
	assert constants.MMR_BASE_EQUAL == 50.0
	assert constants.MMR_BASE_MIN == 10.0
	assert constants.MMR_BASE_MAX == 200.0
	assert constants.MMR_HARD_CAP == 200
	assert constants.MMR_CAPTAIN_BONUS == 10
	assert constants.MMR_STREAK_STEP == 0.05
	assert constants.MMR_STREAK_CAP == 0.25
	assert constants.MMR_PICK_STEP == 2.5
	assert constants.MMR_MAX_TEAM_DIFF == 1000.0


# ── Primitives ────────────────────────────────────────────────────────────────

def test_team_average_defaults(mmr_engine, member):
	assert mmr_engine.team_average({}, []) == 1500.0
	# Unknown ids and None ratings both default to 1500.
	members = [member(1), member(2)]
	assert mmr_engine.team_average({}, members) == 1500.0
	assert mmr_engine.team_average({1: dict(rating=None)}, members) == 1500.0
	# Mixed real values average normally.
	rows = {1: dict(rating=1200), 2: dict(rating=1800)}
	assert mmr_engine.team_average(rows, members) == 1500.0
	assert mmr_engine.team_average({1: dict(rating=2000)}, [member(1)]) == 2000.0


@pytest.mark.parametrize("abs_streak, expected", [
	(0, 1.0),
	(1, 1.0),
	(2, 1.05),
	(3, 1.10),
	(6, 1.25),   # cap reached at 6 games
	(7, 1.25),   # capped beyond
	(50, 1.25),
])
def test_streak_multiplier(mmr_engine, abs_streak, expected):
	assert mmr_engine._streak_multiplier(abs_streak) == pytest.approx(expected)


@pytest.mark.parametrize("cur, is_winner, expected", [
	(0, True, 1),     # win starts a fresh +1
	(3, True, 4),     # win extends a positive streak
	(-2, True, 1),    # win resets a negative streak to +1
	(0, False, -1),   # loss starts a fresh -1
	(-3, False, -4),  # loss extends a negative streak
	(2, False, -1),   # loss resets a positive streak to -1
])
def test_new_streak(mmr_engine, cur, is_winner, expected):
	assert mmr_engine._new_streak(cur, is_winner) == expected


@pytest.mark.parametrize("avg_w, avg_l, expected", [
	(1500, 1500, 50.0),    # equal teams
	(1000, 2000, 200.0),   # max-diff underdog wins
	(1000, 2500, 200.0),   # diff beyond max clamps to max
	(2000, 1000, 10.0),    # max-diff favourite wins
	(2000, 1500, 30.0),    # 500-diff favourite: 50 - 40*0.5
	(1500, 2000, 125.0),   # 500-diff underdog: 50 + 150*0.5
])
def test_team_base_mmr(mmr_engine, avg_w, avg_l, expected):
	assert mmr_engine._team_base_mmr(avg_w, avg_l) == pytest.approx(expected)


# ── Hand-computed full scenarios ──────────────────────────────────────────────

def test_equal_3v3_no_streaks(mmr_engine, member):
	"""All 1500, no streaks. base=50; offsets +2.5/0/-2.5; captain +10.

	Captain: 50 + 2.5 + 10 = 62.5 -> 62 (banker's rounding).
	Mid:     50               -> 50.
	Last:    50 - 2.5 = 47.5  -> 48.
	Losers mirror with negative sign.
	"""
	rows = ratings({i: (1500, 0) for i in (1, 2, 3, 11, 12, 13)})
	changes = mmr_engine.compute_mmr_changes(
		ratings_by_id=rows, winners=team(member, [1, 2, 3]), losers=team(member, [11, 12, 13]))
	assert changes == {1: 62, 2: 50, 3: 48, 11: -62, 12: -50, 13: -48}


def test_captain_bonus_applies_to_losing_captain_too(mmr_engine, member):
	"""2v2 equal: half=0.5, offsets ±1.25. Winner captain 50+1.25+10=61.25 -> 61;
	loser captain mirrors at -61 — the flat +10 applies win OR loss."""
	rows = ratings({i: (1500, 0) for i in (1, 2, 11, 12)})
	changes = mmr_engine.compute_mmr_changes(
		ratings_by_id=rows, winners=team(member, [1, 2]), losers=team(member, [11, 12]))
	assert changes == {1: 61, 2: 49, 11: -61, 12: -49}


def test_streak_multiplier_scales_delta(mmr_engine, member):
	"""1v1 equal ratings. Winner enters on a 5-streak -> new streak 6 -> x1.25:
	(50+10) * 1.25 = 75. Loser at streak 0 -> new -1 -> x1.0 -> -60."""
	rows = ratings({1: (1500, 5), 11: (1500, 0)})
	changes = mmr_engine.compute_mmr_changes(
		ratings_by_id=rows, winners=team(member, [1]), losers=team(member, [11]))
	assert changes == {1: 75, 11: -60}


def test_streaks_by_id_takes_precedence_over_rows(mmr_engine, member):
	"""Rows claim streak 9 (would multiply), streaks_by_id says 0 -> x1.0."""
	rows = ratings({1: (1500, 9), 11: (1500, 9)})
	changes = mmr_engine.compute_mmr_changes(
		ratings_by_id=rows, winners=team(member, [1]), losers=team(member, [11]),
		streaks_by_id={1: 0, 11: 0})
	assert changes == {1: 60, 11: -60}


def test_hard_cap_on_extreme_upset(mmr_engine, member):
	"""1000+ team gap upset -> base 200; captain and mid slots exceed the cap
	and clamp to ±200; the last slot lands just under at 197.5 -> 198."""
	rows = ratings({i: (1000, 0) for i in (1, 2, 3)} | {i: (2400, 0) for i in (11, 12, 13)})
	changes = mmr_engine.compute_mmr_changes(
		ratings_by_id=rows, winners=team(member, [1, 2, 3]), losers=team(member, [11, 12, 13]))
	assert changes == {1: 200, 2: 200, 3: 198, 11: -200, 12: -200, 13: -198}


def test_heavy_favourite_win_pays_little(mmr_engine, member):
	"""Favourite (2400 avg) beats 1000 avg -> base 10. Captain 10+2.5+10=22.5
	-> 22; mid 10; last 7.5 -> 8 (banker's)."""
	rows = ratings({i: (1000, 0) for i in (1, 2, 3)} | {i: (2400, 0) for i in (11, 12, 13)})
	changes = mmr_engine.compute_mmr_changes(
		ratings_by_id=rows, winners=team(member, [11, 12, 13]), losers=team(member, [1, 2, 3]))
	assert changes == {11: 22, 12: 10, 13: 8, 1: -22, 2: -10, 3: -8}


def test_explicit_captain_params_are_latent(mmr_engine, member):
	"""compute_mmr_changes accepts winner_captain/loser_captain but the +10
	bonus is keyed to pick slot 0 regardless — the params are currently
	dead surface (the live adapter always passes teams[x][0] anyway, so
	behaviour matches production). This test LOCKS that: if someone wires
	the params up, this fails and the change gets made deliberately."""
	rows = ratings({i: (1500, 0) for i in (1, 2, 11, 12)})
	changes = mmr_engine.compute_mmr_changes(
		ratings_by_id=rows, winners=team(member, [1, 2]), losers=team(member, [11, 12]),
		winner_captain=member(2), loser_captain=member(12))
	# Identical to the no-explicit-captain 2v2 above: slot 0 keeps the bonus.
	assert changes == {1: 61, 2: 49, 11: -61, 12: -49}


# ── Golden 6v6 characterization ───────────────────────────────────────────────

def test_golden_full_6v6_mixed_ratings_and_streaks(mmr_engine, member):
	"""Realistic Q6 match: 6v6, spread ratings, mixed win/loss streaks on both
	sides. Expected dict captured from the engine at suite creation. Any
	formula drift — base curve, pick offsets, captain bonus, streak
	multiplier, rounding — lands here."""
	rows = ratings({
		1: (1710, 3), 2: (1650, -2), 3: (1580, 0),
		4: (1495, 1), 5: (1430, -4), 6: (1350, 0),
		11: (1820, 5), 12: (1760, 2), 13: (1690, 0),
		14: (1600, -1), 15: (1540, -3), 16: (1470, 2),
	})
	changes = mmr_engine.compute_mmr_changes(
		ratings_by_id=rows,
		winners=team(member, [1, 2, 3, 4, 5, 6]),
		losers=team(member, [11, 12, 13, 14, 15, 16]))
	assert changes == {
		1: 95, 2: 70, 3: 68, 4: 69, 5: 63, 6: 60,
		11: -83, 12: -70, 13: -68, 14: -69, 15: -72, 16: -60,
	}


def test_sign_and_magnitude_invariants(mmr_engine, member):
	"""Across a spread of scenarios: winners strictly positive, losers strictly
	negative, every magnitude within [1, HARD_CAP]."""
	scenarios = [
		({i: (1500, 0) for i in range(1, 13)}, list(range(1, 7)), list(range(7, 13))),
		({i: (1000 + i * 97 % 900, (i % 7) - 3) for i in range(1, 13)},
			list(range(1, 7)), list(range(7, 13))),
		({1: (2400, 6), 2: (900, -6)}, [1], [2]),
	]
	for pairs, w_ids, l_ids in scenarios:
		rows = ratings(pairs)
		changes = mmr_engine.compute_mmr_changes(
			ratings_by_id=rows, winners=team(member, w_ids), losers=team(member, l_ids))
		for uid in w_ids:
			assert 1 <= changes[uid] <= 200, changes
		for uid in l_ids:
			assert -200 <= changes[uid] <= -1, changes
