# -*- coding: utf-8 -*-
"""
Captain selection logic for draft matches.

Extracted from match.py so the scoring/selection is pure and testable in
isolation. These functions take explicit parameters (players, ratings, roles,
history, streaks) rather than reading match state, so they can be unit-tested
without constructing a Match.

match.py's `init_captains` is a thin dispatcher: for the 'smart' and
'captain_role' pick modes it calls into here; the simpler modes (by role and
rating, fair pairs, random, ...) stay inline in match.py since they're one-liners
tied to match helpers.

Nothing here mutates match state — each function RETURNS the chosen [captain_a,
captain_b] list. House-name assignment (which renames teams) stays in match.py
because it mutates team state.

Two scoring systems, matching the historical behaviour exactly:
  - smart selection: MMR similarity + Quidditch-role compat + captain-role
    bonus + a recent-captain penalty, scored as a single summed number.
  - captain_role selection: restricted to Captain-role holders not on streak
    cooldown, scored as (role_score, mmr_score) tuple (role match primary,
    MMR similarity tie-break), with fallbacks.
"""
from itertools import combinations
from collections import deque
import random


# Quidditch position roles, in priority order for role detection.
QUIDDITCH_ROLES = ['chaser', 'beater', 'seeker', 'keeper', 'flex']
# Which specialist roles a 'flex' player pairs well with.
FLEX_COMPATIBLE = {'keeper', 'seeker', 'beater'}
# How many past matches to remember for the recent-captain penalty.
CAPTAIN_HISTORY_SIZE = 5


# ── Scoring primitives (pure) ─────────────────────────────────────────────────

def get_quidditch_role(player) -> str:
	"""Return the player's Quidditch role from their Discord roles, default 'chaser'."""
	role_names = {r.name.lower() for r in player.roles}
	for role in QUIDDITCH_ROLES:
		if role in role_names:
			return role
	return 'chaser'


def role_bonus(role1: str, role2: str) -> int:
	"""+300 same role, +200 flex+specialist, +0 otherwise."""
	if role1 == role2:
		return 300
	if role1 == 'flex' and role2 in FLEX_COMPATIBLE:
		return 200
	if role2 == 'flex' and role1 in FLEX_COMPATIBLE:
		return 200
	return 0


def mmr_bonus(mmr1: int, mmr2: int) -> int:
	"""MMR similarity bonus — max +300 for identical MMR, 0 for a 1000+ gap."""
	return max(0, int(300 * (1 - abs(mmr1 - mmr2) / 1000)))


def captain_role_bonus(p1, p2, captains_role_id) -> int:
	"""+1000 both hold the captain role, +300 one holds it, +0 neither.

	`captains_role_id` may be None (feature off) -> always 0.
	"""
	if not captains_role_id:
		return 0
	p1_has = captains_role_id in {r.id for r in p1.roles}
	p2_has = captains_role_id in {r.id for r in p2.roles}
	if p1_has and p2_has:
		return 1000
	if p1_has or p2_has:
		return 300
	return 0


# ── Selection strategies ──────────────────────────────────────────────────────

def select_smart_captains(
	players,
	ratings,
	captains_role_id=None,
	last_captains=frozenset(),
	captain_history=None,
):
	"""Score every candidate pair; return the highest-scoring two as captains.

	Args:
	  players:           list of Member objects in the match.
	  ratings:           {user_id: mmr}.
	  captains_role_id:  optional captain-role id for the captain-role bonus.
	  last_captains:     frozenset of user_ids who captained the previous match
	                     (they're de-prioritised as candidates).
	  captain_history:   deque of frozensets (recent matches' captain id sets);
	                     each appearance costs -300. Defaults to empty.

	Scoring per pair (summed):
	  MMR similarity + role compat + captain-role bonus
	  - 300 * (appearances of each player across captain_history)
	"""
	if captain_history is None:
		captain_history = deque(maxlen=CAPTAIN_HISTORY_SIZE)

	def recent_count(pid):
		return sum(1 for match_caps in captain_history if pid in match_caps)

	def score_pair(p1, p2):
		mmr1 = ratings.get(p1.id, 1500)
		mmr2 = ratings.get(p2.id, 1500)
		role1 = get_quidditch_role(p1)
		role2 = get_quidditch_role(p2)
		return (
			mmr_bonus(mmr1, mmr2)
			+ role_bonus(role1, role2)
			+ captain_role_bonus(p1, p2, captains_role_id)
			+ (recent_count(p1.id) + recent_count(p2.id)) * -300
		)

	# Prefer players who weren't captains last match.
	non_recent = [p for p in players if p.id not in last_captains]
	candidates = non_recent if len(non_recent) >= 2 else players
	if len(candidates) < 2:
		return sorted(players, key=lambda p: ratings.get(p.id, 0), reverse=True)[:2]
	best = max(combinations(candidates, 2), key=lambda pair: score_pair(pair[0], pair[1]))
	return list(best)


def select_captain_role_captains(
	players,
	ratings,
	captain_role_id,
	is_capped,
	captain_streaks=None,
	captains_role_id=None,
	last_captains=frozenset(),
	captain_history=None,
):
	"""Pick captains from Captain-role holders not on streak cooldown.

	Args:
	  players:          list of Member objects.
	  ratings:          {user_id: mmr}.
	  captain_role_id:  the ELIGIBILITY role id (who may captain — CAPTAIN_ROLE_ID).
	  is_capped:        callable(streak_int) -> bool, the streak-cooldown test.
	  captain_streaks:  {user_id: streak}. Defaults to empty.
	  captains_role_id: the SCORING-BONUS role id (cfg captains_role_id) forwarded
	                    to the smart-selection fallback. This is DISTINCT from
	                    captain_role_id — eligibility vs. bonus are different roles.
	  last_captains / captain_history: forwarded to smart-selection fallback.

	Steps: collect role-holders -> drop capped -> if <2 eligible, fall back to
	smart selection (which ignores the cooldown) -> else score eligible pairs
	by (role_score, mmr_score); if no pair shares a role, random among holders.
	"""
	streaks = captain_streaks or {}

	role_holders = [
		p for p in players
		if captain_role_id in {r.id for r in p.roles}
	]
	eligible = [p for p in role_holders if not is_capped(streaks.get(p.id, 0))]
	if len(eligible) < 2:
		# Not enough fresh captains — defer to smart selection across the queue.
		# NOTE: pass the CFG captains_role_id (bonus role), NOT captain_role_id
		# (eligibility role), to match the original behaviour exactly.
		return select_smart_captains(
			players, ratings,
			captains_role_id=captains_role_id,
			last_captains=last_captains,
			captain_history=captain_history,
		)
	role_holders = eligible  # only score pairs of eligible players

	def score(p1, p2):
		role1 = get_quidditch_role(p1)
		role2 = get_quidditch_role(p2)
		role_score = role_bonus(role1, role2)
		mmr_score = mmr_bonus(
			ratings.get(p1.id, 1500),
			ratings.get(p2.id, 1500),
		)
		# Role match is the primary signal — MMR similarity is the tie-break.
		return (role_score, mmr_score)

	best_pair = max(combinations(role_holders, 2), key=lambda pair: score(*pair))
	best_role_score, _ = score(*best_pair)
	if best_role_score == 0:
		# No pair shares roles — fall back to random among role-holders.
		return random.sample(role_holders, 2)
	return list(best_pair)
