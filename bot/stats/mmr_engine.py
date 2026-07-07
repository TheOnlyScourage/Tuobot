# -*- coding: utf-8 -*-
"""
Custom Q6 MMR engine.

The base PUBobot stats system uses a TrueSkill / Glicko2 rating model
internally. We layer a custom MMR delta on top so the user-facing rating
changes match Q6's "feel": flat rewards for captains, scaled for upset
wins/losses, multiplied by streaks.

This file owns the formula. stats.py unpacks the match into plain lists and
calls `compute_mmr_changes()` once per match, then applies the deltas.
Everything tunable lives in bot/constants.py.

Formula (canonical — matches the historical live behaviour exactly):
  1. team average — mean rating of each team (captain + picks)
  2. base — 10..200 scaled by team-rating difference (equal teams = 50),
       skewed toward the underdog. Unsigned magnitude; sign applied per team.
  3. pick offset — CENTERED around the team midpoint so per-team adjustments
       sum to zero: captain (slot 0) gets +half*step, last pick -half*step.
  4. captain bonus — flat +CAPTAIN_BONUS folded into the individual base
       BEFORE the streak multiplier (so streaks scale the captain bonus too).
  5. streak multiplier — computed from the player's NEW streak (current
       incremented/decremented toward the outcome), +5%/game capped at +25%.
  6. hard cap ±HARD_CAP, then floored so magnitude is never below 1.

NOTE ON INTERFACE: takes clean primitives (lists + captains + dicts), NOT a
match object, so the formula is testable in isolation. Draw handling lives in
the caller (stats.py) — a draw means no rating change, decided at match level.
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
	"""Return the average rating across `members`, safe default 1500.

	`ratings_by_id` maps user_id -> the player's row dict (as returned by
	rating.get_players). We read the 'rating' field, defaulting to 1500.
	"""
	if not members:
		return 1500.0
	vals = [((ratings_by_id.get(m.id) or {}).get('rating') or 1500) for m in members]
	return sum(vals) / len(vals)


def _streak_multiplier(abs_streak: int) -> float:
	"""1.0 at |streak|<=1, +MMR_STREAK_STEP per game beyond 1, capped at CAP."""
	if abs_streak <= 1:
		return 1.0
	return 1.0 + min((abs_streak - 1) * MMR_STREAK_STEP, MMR_STREAK_CAP)


def _team_base_mmr(avg_winner: float, avg_loser: float) -> float:
	"""Unsigned base magnitude (10..200) from team-average difference.

	Equal teams (diff 0) -> MMR_BASE_EQUAL (50).
	Max diff, underdog wins -> MMR_BASE_MAX (200).
	Max diff, favourite wins -> MMR_BASE_MIN (10).
	"""
	diff  = abs(avg_winner - avg_loser)
	ratio = min(diff, MMR_MAX_TEAM_DIFF) / MMR_MAX_TEAM_DIFF
	if avg_winner <= avg_loser:                          # underdog or equal
		return MMR_BASE_EQUAL + (MMR_BASE_MAX - MMR_BASE_EQUAL) * ratio
	else:                                                # favourite
		return MMR_BASE_EQUAL - (MMR_BASE_EQUAL - MMR_BASE_MIN) * ratio


def _new_streak(cur_streak: int, is_winner: bool) -> int:
	"""The streak AFTER this match, used to size the multiplier.

	Winner: extend a positive streak, else start at +1.
	Loser:  extend a negative streak, else start at -1.
	"""
	if is_winner:
		return cur_streak + 1 if cur_streak > 0 else 1
	else:
		return cur_streak - 1 if cur_streak < 0 else -1


def compute_mmr_changes(
	*,
	ratings_by_id: dict,
	winners: list,
	losers: list,
	winner_captain=None,
	loser_captain=None,
	streaks_by_id: dict = None,
) -> dict:
	"""Compute {user_id: mmr_delta} for a decided match (no draws here).

	Args:
	  ratings_by_id: {user_id: player_row_dict}. Row dicts carry 'rating' and
	                 (optionally) 'streak'. If `streaks_by_id` is given it takes
	                 precedence for streaks; otherwise streaks come from the row.
	  winners:       winning team members in pick order; winners[0] is captain.
	  losers:        losing team members in pick order; losers[0] is captain.
	  winner_captain/loser_captain: optional explicit captains. Default to
	                 winners[0] / losers[0] (pick-order captain), matching live
	                 behaviour where slot 0 is the captain.
	  streaks_by_id: optional {user_id: current_streak}. If None, streaks are
	                 read from each player's row in ratings_by_id ('streak', 0).

	Returns {user_id: int_delta}, positive for winners, negative for losers,
	within ±MMR_HARD_CAP, magnitude floored at 1.
	"""
	avg_w = team_average(ratings_by_id, winners)
	avg_l = team_average(ratings_by_id, losers)
	base  = _team_base_mmr(avg_w, avg_l)

	def _streak_for(player):
		if streaks_by_id is not None:
			return streaks_by_id.get(player.id, 0)
		return (ratings_by_id.get(player.id) or {}).get('streak', 0)

	changes = {}
	for team, is_winner in [(winners, True), (losers, False)]:
		n    = len(team)
		half = (n - 1) / 2.0                       # centre so offsets sum to 0
		for pick_pos, player in enumerate(team):
			pick_offset     = (half - pick_pos) * MMR_PICK_STEP
			captain_bonus   = float(MMR_CAPTAIN_BONUS) if pick_pos == 0 else 0.0
			individual_base = base + pick_offset + captain_bonus

			cur_streak = _streak_for(player)
			new_streak = _new_streak(cur_streak, is_winner)
			multiplier = _streak_multiplier(abs(new_streak))

			mmr = individual_base * multiplier
			mmr = min(mmr, MMR_HARD_CAP)
			mmr = max(round(mmr), 1)               # never below magnitude 1
			changes[player.id] = mmr if is_winner else -mmr

	return changes
