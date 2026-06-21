# -*- coding: utf-8 -*-
"""
Custom Q6 MMR engine.

The base PUBobot stats system uses a TrueSkill / Glicko2 rating model
internally. We layer a custom MMR delta on top so the user-facing rating
changes match Q6's "feel": flat rewards for captains, scaled for upset
wins/losses, multiplied by streaks.

This file owns the formula. stats.py calls `compute_mmr_changes()`
once per match and applies the deltas. Everything tunable lives in
bot/constants.py.

Formula breakdown:
  1. team_average — mean rating of each team (captain + picks)
  2. base — 10..200 scaled by team-rating difference (equal teams = 50)
  3. captain_bonus — captain gets a flat +10 / -10 above the team's base
  4. pick_offset — earlier picks gain more, later picks gain less,
       in steps of MMR_PICK_STEP per slot
  5. streak_multiplier — +5% per consecutive win/loss, capped at +25%
  6. hard_cap — final value clamped to ±MMR_HARD_CAP
"""

from bot.constants import (
	MMR_MAX_TEAM_DIFF,
	MMR_BASE_EQUAL,
	MMR_BASE_MIN,
	MMR_BASE_MAX,
	MMR_HARD_CAP,
	MMR_CAPTAIN_BONUS,
	MMR_STREAK_STEP,
	MMR_STREAK_CAP,
	MMR_PICK_STEP,
)


def team_average(ratings_by_id: dict, members: list) -> float:
	"""Return the average rating across `members`, with safe default 1500."""
	if not members:
		return 1500.0
	vals = [(ratings_by_id.get(m.id) or 1500) for m in members]
	return sum(vals) / len(vals)


def _streak_multiplier(streak: int) -> float:
	"""+5% per streak game beyond 1, capped at +25%.

	A 1-game streak returns 1.0; a 2-game streak returns 1.05; a 6-game
	streak hits the cap at 1.25; anything beyond also returns 1.25.
	"""
	if streak <= 1:
		return 1.0
	bonus = (streak - 1) * MMR_STREAK_STEP
	return 1.0 + min(bonus, MMR_STREAK_CAP)


def _base_mmr_for_outcome(my_team_avg: float, opp_team_avg: float, is_winner: bool) -> float:
	"""Return the base MMR (10..200) for the team's outcome.

	When teams are equal the base is exactly MMR_BASE_EQUAL (50). The
	farther apart the teams are, the more skewed the base is in favour
	of the underdog: an underdog win earns close to MMR_BASE_MAX,
	a favoured win earns close to MMR_BASE_MIN. Symmetric for losses.
	"""
	diff = my_team_avg - opp_team_avg
	# Clamp to ±MAX_TEAM_DIFF and normalize to [-1, +1]
	norm = max(-1.0, min(1.0, diff / MMR_MAX_TEAM_DIFF))

	if is_winner:
		# Favoured (norm>0) → lean toward MIN. Underdog (norm<0) → lean toward MAX.
		# At norm = 0 → EQUAL.
		if norm >= 0:
			return MMR_BASE_EQUAL + norm * (MMR_BASE_MIN - MMR_BASE_EQUAL)
		else:
			return MMR_BASE_EQUAL + (-norm) * (MMR_BASE_MAX - MMR_BASE_EQUAL)
	else:
		# Losing favoured → lose more. Losing underdog → lose less.
		if norm >= 0:
			return -(MMR_BASE_EQUAL + norm * (MMR_BASE_MAX - MMR_BASE_EQUAL))
		else:
			return -(MMR_BASE_EQUAL + (-norm) * (MMR_BASE_MIN - MMR_BASE_EQUAL))


def compute_mmr_change(
	*,
	is_winner: bool,
	is_captain: bool,
	pick_slot: int,
	my_team_avg: float,
	opp_team_avg: float,
	streak: int,
) -> int:
	"""Compute the MMR delta for one player.

	Args:
	  is_winner:   True if this player was on the winning team
	  is_captain:  True if this player was a team captain
	  pick_slot:   0 = captain (or first to act), 1 = first pick,
	               2 = second pick, etc. Captains pass 0 here.
	  my_team_avg: average rating of this player's team
	  opp_team_avg: average rating of the opposing team
	  streak:      number of consecutive results (W or L) for this player

	Returns:
	  Integer MMR change. Positive for gain, negative for loss. Always
	  within ±MMR_HARD_CAP.
	"""
	base = _base_mmr_for_outcome(my_team_avg, opp_team_avg, is_winner)

	# Captain flat bonus (additive). Captain on a winning team: +10 extra.
	# Captain on a losing team: -10 extra (they accept the leadership cost).
	captain_extra = MMR_CAPTAIN_BONUS * (1 if is_winner else -1) if is_captain else 0

	# Pick-order offset: earlier picks gain a bit more / lose a bit less.
	# Slot 0 (captain) gets no offset since they have the captain_extra.
	pick_offset = -(pick_slot * MMR_PICK_STEP) if is_winner else (pick_slot * MMR_PICK_STEP)

	# Streak multiplier applied to base + offsets (NOT to captain_extra so the
	# leadership bonus doesn't snowball on long streaks).
	mult = _streak_multiplier(streak)
	raw = (base + pick_offset) * mult + captain_extra

	# Hard cap
	if raw > MMR_HARD_CAP:
		raw = MMR_HARD_CAP
	elif raw < -MMR_HARD_CAP:
		raw = -MMR_HARD_CAP

	return int(round(raw))


def compute_mmr_changes(
	*,
	ratings_by_id: dict,
	winners: list,
	losers: list,
	winner_captain,
	loser_captain,
	streaks_by_id: dict,
) -> dict:
	"""Compute MMR deltas for everyone in a match.

	Args:
	  ratings_by_id:  {user_id: current_rating}
	  winners:        team members in pick order; winners[0] is the captain
	  losers:         same for losing team
	  winner_captain: Member object — must equal winners[0]
	  loser_captain:  Member object — must equal losers[0]
	  streaks_by_id:  {user_id: current_streak_length} (positive integer)

	Returns:
	  {user_id: delta_int}
	"""
	w_avg = team_average(ratings_by_id, winners)
	l_avg = team_average(ratings_by_id, losers)

	out = {}

	for i, p in enumerate(winners):
		out[p.id] = compute_mmr_change(
			is_winner=True,
			is_captain=(p == winner_captain),
			pick_slot=i,
			my_team_avg=w_avg,
			opp_team_avg=l_avg,
			streak=streaks_by_id.get(p.id, 1),
		)

	for i, p in enumerate(losers):
		out[p.id] = compute_mmr_change(
			is_winner=False,
			is_captain=(p == loser_captain),
			pick_slot=i,
			my_team_avg=l_avg,
			opp_team_avg=w_avg,
			streak=streaks_by_id.get(p.id, 1),
		)

	return out
