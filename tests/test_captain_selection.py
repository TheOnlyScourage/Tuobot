# -*- coding: utf-8 -*-
"""
Regression tests for bot/match/captain_selection.py.

Covers the scoring primitives, both selection strategies, and — most
importantly — the eligibility-role vs. bonus-role distinction in
select_captain_role_captains' fallback. That distinction was a REAL bug
caught during the extraction (the two role ids were conflated in the
fallback path); test_fallback_forwards_bonus_role_not_eligibility_role
is the permanent lock against it regressing.

random.sample is only reached when no eligible pair shares a role; that
path is tested with the module's `random` swapped for a recorder so the
candidate pool is asserted exactly, not sampled.
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


def test_captain_role_bonus(captain_selection, member, role):
	holder_a = _member(member, role, 1, (500, "Team Captain"))
	holder_b = _member(member, role, 2, (500, "Team Captain"))
	civilian = _member(member, role, 3, "Beater")
	f = captain_selection.captain_role_bonus
	assert f(holder_a, holder_b, None) == 0      # feature off
	assert f(holder_a, holder_b, 500) == 1000    # both
	assert f(holder_a, civilian, 500) == 300     # one
	assert f(civilian, civilian, 500) == 0       # neither


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


# ── select_captain_role_captains ──────────────────────────────────────────────

ELIG = 100    # eligibility role id (constants.CAPTAIN_ROLE_ID in production)
BONUS = 200   # scoring-bonus role id (cfg captains_role_id in production)


def test_role_mode_only_considers_eligibility_holders(captain_selection, member, role):
	a = _member(member, role, 1, (ELIG, "Captain"), "Seeker")
	b = _member(member, role, 2, (ELIG, "Captain"), "Seeker")
	# Best on paper, but holds no eligibility role -> never picked.
	ringer = _member(member, role, 3, "Seeker")
	picked = captain_selection.select_captain_role_captains(
		[a, b, ringer], dict.fromkeys((1, 2, 3), 1500),
		captain_role_id=ELIG, is_capped=lambda s: False)
	assert {p.id for p in picked} == {1, 2}


def test_role_mode_drops_capped_holders(captain_selection, member, role):
	a = _member(member, role, 1, (ELIG, "Captain"), "Seeker")
	b = _member(member, role, 2, (ELIG, "Captain"), "Seeker")
	c = _member(member, role, 3, (ELIG, "Captain"), "Seeker")
	picked = captain_selection.select_captain_role_captains(
		[a, b, c], dict.fromkeys((1, 2, 3), 1500),
		captain_role_id=ELIG,
		is_capped=lambda streak: streak >= 2,
		captain_streaks={3: 2})
	assert {p.id for p in picked} == {1, 2}


def test_fallback_forwards_bonus_role_not_eligibility_role(captain_selection, member, role):
	"""THE regression lock for the extraction bug.

	Setup: only ONE eligible captain-role holder (A; B and C are on
	cooldown), forcing the smart-selection fallback. Everyone shares a
	quidditch role and MMR, so every pair scores 600 — EXCEPT the pair
	holding the BONUS role (C, D), which the captain-role bonus lifts to
	1600 *iff* the fallback forwards cfg's captains_role_id.

	The historical bug forwarded the ELIGIBILITY id instead; A is the only
	eligibility holder, so under the bug A-pairs score 900 and the pick
	becomes {A, B}. Correct behaviour picks {C, D}.
	"""
	a = _member(member, role, 1, (ELIG, "Captain"), "Beater")
	b = _member(member, role, 2, (ELIG, "Captain"), "Beater")
	c = _member(member, role, 3, (ELIG, "Captain"), (BONUS, "Captain Bonus"), "Beater")
	d = _member(member, role, 4, (BONUS, "Captain Bonus"), "Beater")
	picked = captain_selection.select_captain_role_captains(
		[a, b, c, d], dict.fromkeys((1, 2, 3, 4), 1500),
		captain_role_id=ELIG,
		is_capped=lambda streak: streak >= 2,
		captain_streaks={2: 2, 3: 2},      # only A stays eligible -> fallback
		captains_role_id=BONUS)
	assert {p.id for p in picked} == {3, 4}


def test_role_mode_role_match_outranks_mmr(captain_selection, member, role):
	"""(role_score, mmr_score) tuple ordering: a same-role pair with a worse
	MMR gap must beat a cross-role pair with identical MMR."""
	e = _member(member, role, 1, (ELIG, "Captain"), "Seeker")
	f = _member(member, role, 2, (ELIG, "Captain"), "Seeker")
	g = _member(member, role, 3, (ELIG, "Captain"), "Keeper")
	h = _member(member, role, 4, (ELIG, "Captain"), "Chaser")
	ratings = {1: 1500, 2: 1100, 3: 1500, 4: 1500}   # (e,f) gap 400; (g,h) equal
	picked = captain_selection.select_captain_role_captains(
		[e, f, g, h], ratings, captain_role_id=ELIG, is_capped=lambda s: False)
	assert {p.id for p in picked} == {1, 2}


def test_role_mode_mmr_breaks_role_ties(captain_selection, member, role):
	e = _member(member, role, 1, (ELIG, "Captain"), "Seeker")
	f = _member(member, role, 2, (ELIG, "Captain"), "Seeker")
	g = _member(member, role, 3, (ELIG, "Captain"), "Keeper")
	h = _member(member, role, 4, (ELIG, "Captain"), "Keeper")
	ratings = {1: 1500, 2: 1100, 3: 1500, 4: 1500}   # both pairs 300 role; (g,h) closer MMR
	picked = captain_selection.select_captain_role_captains(
		[e, f, g, h], ratings, captain_role_id=ELIG, is_capped=lambda s: False)
	assert {p.id for p in picked} == {3, 4}


def test_role_mode_random_among_holders_when_no_shared_roles(
	captain_selection, member, role, monkeypatch,
):
	"""No eligible pair shares a role -> random.sample over ELIGIBLE holders
	only. Swap the module's random for a recorder and assert the exact pool:
	capped holders and non-holders must not be in it."""
	e = _member(member, role, 1, (ELIG, "Captain"), "Seeker")
	f = _member(member, role, 2, (ELIG, "Captain"), "Keeper")
	g = _member(member, role, 3, (ELIG, "Captain"), "Chaser")
	capped = _member(member, role, 4, (ELIG, "Captain"), "Seeker")
	civilian = _member(member, role, 5, "Seeker")

	class Recorder:
		def __init__(self):
			self.calls = []

		def sample(self, pool, k):
			self.calls.append((list(pool), k))
			return list(pool)[:k]

	rec = Recorder()
	monkeypatch.setattr(captain_selection, "random", rec)

	picked = captain_selection.select_captain_role_captains(
		[e, f, g, capped, civilian], dict.fromkeys((1, 2, 3, 4, 5), 1500),
		captain_role_id=ELIG,
		is_capped=lambda streak: streak >= 2,
		captain_streaks={4: 2})

	assert len(rec.calls) == 1
	pool, k = rec.calls[0]
	assert k == 2
	assert {p.id for p in pool} == {1, 2, 3}
	assert {p.id for p in picked} <= {1, 2, 3} and len(picked) == 2
