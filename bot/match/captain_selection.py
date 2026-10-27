# -*- coding: utf-8 -*-
"""
Captain selection logic for draft matches.

Extracted from match.py so the scoring/selection is pure and testable in
isolation. These functions take explicit parameters (players, ratings, roles,
history, streaks) rather than reading match state, so they can be unit-tested
without constructing a Match.

match.py's `init_captains` is a thin dispatcher: it narrows the lobby to the
ELIGIBLE pool with `eligible_captains` and then applies the queue's pick mode
('smart' calls into here; by rating / fair pairs / random are one-liners in
match.py).

Nothing here mutates match state — each function RETURNS the chosen [captain_a,
captain_b] list. House-name assignment (which renames teams) stays in match.py
because it mutates team state.

Eligibility (Oct 2026 — replaced the Captain ROLE system): a player may be
auto-picked as captain unless they hold the "bar" role (the captains ban list)
or have fewer than CAPTAIN_MIN_GAMES games on their profile. When fewer than
two queued players qualify, the most experienced non-barred players fill in;
barred players are used only if literally everyone in the lobby is barred.

Smart scoring: MMR similarity + Quidditch-role compat + a recent-captain
penalty, summed into one number per candidate pair.
"""
from itertools import combinations
from collections import deque


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


# ── Eligibility (pure) ────────────────────────────────────────────────────────

def eligible_captains(players, games_by_id, bar_role_id, min_games):
	"""Narrow a lobby to the players who may be auto-picked as captain.

	Args:
	  players:     list of Member objects in the match.
	  games_by_id: {user_id: career games on this channel} (missing = 0).
	  bar_role_id: the "bar" role — holders are never captains if avoidable.
	  min_games:   minimum career games to qualify outright.

	Returns (pool, tier):
	  tier 0 — >= 2 players are not barred AND have min_games: pool = them.
	  tier 1 — fewer than 2 qualify: the qualifiers plus the most-experienced
	           NON-barred players, enough to make 2 (ties broken by list
	           order, i.e. queue order). Barred players stay out.
	  tier 2 — fewer than 2 non-barred players exist at all: pool = everyone
	           (the only way to seat two captains).
	The caller picks the final pair from the pool with the queue's mode."""
	def games(p):
		return games_by_id.get(p.id, 0)

	def barred(p):
		return bar_role_id in {r.id for r in p.roles}

	not_barred = [p for p in players if not barred(p)]
	qualified = [p for p in not_barred if games(p) >= min_games]
	if len(qualified) >= 2:
		return qualified, 0
	if len(not_barred) >= 2:
		fill = sorted((p for p in not_barred if p not in qualified), key=games, reverse=True)
		return qualified + fill[:2 - len(qualified)], 1
	return list(players), 2


# ── Selection strategies ──────────────────────────────────────────────────────

def select_smart_captains(
	players,
	ratings,
	last_captains=frozenset(),
	captain_history=None,
):
	"""Score every candidate pair; return the highest-scoring two as captains.

	Args:
	  players:           the ELIGIBLE pool (see eligible_captains).
	  ratings:           {user_id: mmr}.
	  last_captains:     frozenset of user_ids who captained the previous match
	                     (they're de-prioritised as candidates).
	  captain_history:   deque of frozensets (recent matches' captain id sets);
	                     each appearance costs -300. Defaults to empty.

	Scoring per pair (summed):
	  MMR similarity + role compat
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
			+ (recent_count(p1.id) + recent_count(p2.id)) * -300
		)

	# Prefer players who weren't captains last match.
	non_recent = [p for p in players if p.id not in last_captains]
	candidates = non_recent if len(non_recent) >= 2 else players
	if len(candidates) < 2:
		return sorted(players, key=lambda p: ratings.get(p.id, 0), reverse=True)[:2]
	best = max(combinations(candidates, 2), key=lambda pair: score_pair(pair[0], pair[1]))
	return list(best)
