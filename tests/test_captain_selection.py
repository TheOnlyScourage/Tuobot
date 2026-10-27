# -*- coding: utf-8 -*-
"""
Regression tests for bot/match/captain_selection.py.

Covers the scoring primitives, smart pair selection, and — since Oct 2026 —
the eligibility filter that replaced the Captain ROLE system: players holding
the "bar" role (captains ban list) or with fewer than CAPTAIN_MIN_GAMES career
games are excluded, with the most-experienced non-barred players filling in
when fewer than two qualify, and barred players used only as a last resort.
"""

from collections import deque

import pytest


def _member(member, role, uid, *role_specs):
	"""role_specs: strings (role names, id 0) or (id, name) tuples."""
	roles = []
	for spec in role_specs:
		if isinstance(spec, tuple):
			roles.append(role(id=spec[0], name=spec[1]))
		else:
			roles.append(role(name=spec))
	return member(uid, roles=roles)


def test_module_constants_are_what_these_tests_assume(captain_selection):
	assert captain_selection.QUIDDITCH_ROLES == ['chaser', 'beater', 'seeker', 'keeper', 'flex']
	assert sorted(captain_selection.FLEX_COMPATIBLE) == ['beater', 'keeper', 'seeker']
	assert captain_selection.CAPTAIN_HISTORY_SIZE == 5


# ── Scoring primitives ────────────────────────────────────────────────────────

def test_get_quidditch_role(captain_selection, member, role):
	# Priority follows QUIDDITCH_ROLES order: chaser outranks beater.
	both = _member(member, role, 1, "Beater", "Chaser")
	assert captain_selection.get_quidditch_role(both) == 'chaser'
	# Case-insensitive.
	loud = _member(member, role, 2, "SEEKER")
	assert captain_selection.get_quidditch_role(loud) == 'seeker'
	flex = _member(member, role, 3, "Flex")
	assert captain_selection.get_quidditch_role(flex) == 'flex'
	# No quidditch role at all -> default 'chaser'.
	none = _member(member, role, 4, "Some Other Role")
	assert captain_selection.get_quidditch_role(none) == 'chaser'


@pytest.mark.parametrize("r1, r2, expected", [
	('chaser', 'chaser', 300),
	('flex', 'flex', 300),      # same role, even if that role is flex
	('flex', 'keeper', 200),
	('seeker', 'flex', 200),    # symmetric
	('flex', 'chaser', 0),      # chaser is NOT in FLEX_COMPATIBLE
	('beater', 'keeper', 0),
])
def test_role_bonus(captain_selection, r1, r2, expected):
	assert captain_selection.role_bonus(r1, r2) == expected


@pytest.mark.parametrize("m1, m2, expected", [
	(1500, 1500, 300),   # identical
	(1500, 2000, 150),   # 500 gap -> half
	(1000, 1250, 225),   # 250 gap
	(1500, 1833, 200),   # 333 gap -> int truncation of 200.1
	(1500, 2500, 0),     # 1000 gap -> floor
	(1500, 3500, 0),     # beyond -> clamped at 0, never negative
])
def test_mmr_bonus(captain_selection, m1, m2, expected):
	assert captain_selection.mmr_bonus(m1, m2) == expected


# ── select_smart_captains ─────────────────────────────────────────────────────

def _smart_squad(member, role):
	"""P1,P2 seekers @1500 (the natural best pair); P3 keeper; P4 chaser."""
	p1 = _member(member, role, 1, "Seeker")
	p2 = _member(member, role, 2, "Seeker")
	p3 = _member(member, role, 3, "Keeper")
	p4 = _member(member, role, 4, "Chaser")
	ratings = {1: 1500, 2: 1500, 3: 1500, 4: 1500}
	return [p1, p2, p3, p4], ratings


def test_smart_picks_highest_scoring_pair(captain_selection, member, role):
	players, ratings = _smart_squad(member, role)
	picked = captain_selection.select_smart_captains(players, ratings)
	assert {p.id for p in picked} == {1, 2}


def test_smart_deprioritises_last_captains(captain_selection, member, role):
	players, ratings = _smart_squad(member, role)
	picked = captain_selection.select_smart_captains(
		players, ratings, last_captains=frozenset({1, 2}))
	# Best pair captained last match -> excluded; next best among the rest.
	assert {p.id for p in picked} == {3, 4}


def test_smart_ignores_last_captains_when_too_few_others(captain_selection, member, role):
	players, ratings = _smart_squad(member, role)
	pair_only = players[:2]
	picked = captain_selection.select_smart_captains(
		pair_only, ratings, last_captains=frozenset({1, 2}))
	# Excluding both would leave <2 candidates -> falls back to everyone.
	assert {p.id for p in picked} == {1, 2}


def test_smart_history_penalty_flips_choice(captain_selection, member, role):
	"""Two 600-point pairs tie; P1 appearing twice in history costs -600 and
	hands the pick to the untouched pair."""
	p1 = _member(member, role, 1, "Seeker")
	p2 = _member(member, role, 2, "Seeker")
	p3 = _member(member, role, 3, "Beater")
	p4 = _member(member, role, 4, "Beater")
	ratings = dict.fromkeys((1, 2, 3, 4), 1500)
	history = deque([frozenset({1}), frozenset({1})], maxlen=5)
	picked = captain_selection.select_smart_captains(
		[p1, p2, p3, p4], ratings, captain_history=history)
	assert {p.id for p in picked} == {3, 4}


def test_smart_tie_resolves_to_earliest_pair(captain_selection, member, role):
	"""Characterization: equal-scoring pairs resolve to the first pair in
	combinations() order (max keeps the first maximum). Deterministic today;
	if this ever changes, it changes which captains real matches get."""
	p1 = _member(member, role, 1, "Seeker")
	p2 = _member(member, role, 2, "Seeker")
	p3 = _member(member, role, 3, "Beater")
	p4 = _member(member, role, 4, "Beater")
	ratings = dict.fromkeys((1, 2, 3, 4), 1500)
	picked = captain_selection.select_smart_captains([p1, p2, p3, p4], ratings)
	assert {p.id for p in picked} == {1, 2}


def test_smart_degenerate_single_player(captain_selection, member, role):
	"""<2 candidates overall -> top-2 by rating slice (here: just the one)."""
	p1 = _member(member, role, 1, "Seeker")
	picked = captain_selection.select_smart_captains([p1], {1: 1500})
	assert [p.id for p in picked] == [1]


# ── eligible_captains (the bar role + minimum games filter) ──────────────────

BAR = 1564449871019114597   # constants.CAPTAIN_BAR_ROLE_ID
MIN = 25                    # constants.CAPTAIN_MIN_GAMES


def _lobby(member, role):
	"""Six players: a=40 games, b=30, c=25 (exactly the bar), d=24, e=5,
	f=100 but BARRED."""
	a = _member(member, role, 1, "Seeker")
	b = _member(member, role, 2, "Seeker")
	c = _member(member, role, 3, "Keeper")
	d = _member(member, role, 4, "Chaser")
	e = _member(member, role, 5, "Beater")
	f = _member(member, role, 6, (BAR, "bar"), "Seeker")
	games = {1: 40, 2: 30, 3: 25, 4: 24, 5: 5, 6: 100}
	return [a, b, c, d, e, f], games


def test_tier0_only_qualified_and_unbarred(captain_selection, member, role):
	players, games = _lobby(member, role)
	pool, tier = captain_selection.eligible_captains(players, games, BAR, MIN)
	assert tier == 0
	assert {p.id for p in pool} == {1, 2, 3}     # 25 games counts; 24 doesn't; barred out


def test_tier1_most_experienced_fill_in(captain_selection, member, role):
	"""Only one qualifier -> the single most-experienced non-barred player
	fills the second slot (24 games beats 5; the barred 100-game player is
	skipped)."""
	players, games = _lobby(member, role)
	games = {**games, 1: 40, 2: 10, 3: 3}         # now only `a` qualifies
	pool, tier = captain_selection.eligible_captains(players, games, BAR, MIN)
	assert tier == 1
	assert [p.id for p in pool] == [1, 4]         # a (qualified) + d (24, most games)


def test_tier1_no_qualifiers_takes_top_two_by_games(captain_selection, member, role):
	players, games = _lobby(member, role)
	games = {1: 20, 2: 19, 3: 2, 4: 24, 5: 5, 6: 100}
	pool, tier = captain_selection.eligible_captains(players, games, BAR, MIN)
	assert tier == 1
	assert [p.id for p in pool] == [4, 1]         # 24 then 20; barred ignored


def test_tier1_ties_keep_queue_order(captain_selection, member, role):
	players, games = _lobby(member, role)
	games = dict.fromkeys((1, 2, 3, 4, 5, 6), 10)
	pool, tier = captain_selection.eligible_captains(players, games, BAR, MIN)
	assert tier == 1 and [p.id for p in pool] == [1, 2]


def test_tier2_everyone_barred_uses_everyone(captain_selection, member, role):
	x = _member(member, role, 1, (BAR, "bar"), "Seeker")
	y = _member(member, role, 2, (BAR, "bar"), "Keeper")
	z = _member(member, role, 3, (BAR, "bar"), "Chaser")
	pool, tier = captain_selection.eligible_captains([x, y, z], {1: 50, 2: 50, 3: 50}, BAR, MIN)
	assert tier == 2 and [p.id for p in pool] == [1, 2, 3]


def test_missing_game_counts_mean_zero(captain_selection, member, role):
	players, _ = _lobby(member, role)
	pool, tier = captain_selection.eligible_captains(players, {}, BAR, MIN)
	assert tier == 1 and len(pool) == 2 and all(p.id != 6 for p in pool)


def test_pool_feeds_smart_selection(captain_selection, member, role):
	"""End to end: the barred Seeker (best Quidditch-role match for `a`) can
	never be picked; smart selection runs on the eligible pool only."""
	players, games = _lobby(member, role)
	pool, _ = captain_selection.eligible_captains(players, games, BAR, MIN)
	ratings = dict.fromkeys((1, 2, 3, 4, 5, 6), 1500)
	picked = captain_selection.select_smart_captains(pool, ratings)
	assert {p.id for p in picked} == {1, 2}       # the two eligible Seekers
	assert 6 not in {p.id for p in picked}


def test_no_captain_role_machinery_left(captain_selection):
	assert not hasattr(captain_selection, "select_captain_role_captains")
	assert not hasattr(captain_selection, "captain_role_bonus")
