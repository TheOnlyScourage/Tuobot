# -*- coding: utf-8 -*-
"""Milestone & rank-up detection for the ranked results embed.

Pure module: imports only `bot.constants` (rank thresholds) and the stdlib —
no Discord, no database — so it's fully covered by tests/test_milestones.py.
The match flow (`Match._collect_milestones`) feeds it each participant's
all-time ranked results, their old/new rating for this match, and their
pre-match all-time peak; it returns ready-to-post embed lines.

Detected events (a player can stack all three in one match):
  - **Match-count milestones** — all-time ranked appearances hitting one of
	MATCH_MILESTONES. Fires win, lose, or abort: showing up is the milestone.
  - **Rank-ups** — the rating crossed a rank threshold UPWARD this match,
	tagged "for the first time" when the new tier beats the player's
	all-time peak tier (so post-season re-climbs celebrate without lying).
  - **New best win streak** — the all-time record was just extended, at or
	above BEST_STREAK_FLOOR. Only the match that sets the record fires.
"""
from __future__ import annotations

import re

from bot.constants import RANK_EMOJIS

# All-time ranked-match counts worth celebrating.
MATCH_MILESTONES = (50, 100, 250, 500, 1000)

# A "new best streak" only gets a line at W4+ — early-career bests like W2
# would fire constantly and mean nothing.
BEST_STREAK_FLOOR = 4


def rank_tier(rating: int) -> int:
	"""Index into RANK_EMOJIS of the rank this rating holds (0 = lowest)."""
	tier = 0
	for i, (threshold, _emoji) in enumerate(RANK_EMOJIS):
		if rating >= threshold:
			tier = i
	return tier


def rank_label(tier: int) -> tuple[str, str]:
	"""(emoji, display name) for a tier — the name parsed from the emoji
	string, e.g. '<:Q6Diamond:...>' -> 'Diamond', '<:CHAD:...>' -> 'Chad'."""
	emoji = RANK_EMOJIS[tier][1]
	m = re.search(r'<:(?:Q6)?([A-Za-z]+):', emoji)
	name = (m.group(1) if m else 'Unranked').capitalize()
	return emoji, name


def _streak_walk(results) -> tuple[int, int, int]:
	"""Walk chronological ranked results -> (current run, best overall, best
	BEFORE the final result). Losses reset the run; ABORTS (winner NULL — Q6
	has no draws) are invisible to streaks, mirroring the register/profile/
	highlights convention."""
	cur = best = best_before_last = 0
	last = len(results) - 1
	for i, (winner, team) in enumerate(results):
		if i == last:
			best_before_last = best
		if winner is None:
			continue
		if winner == team:
			cur += 1
			best = max(best, cur)
		else:
			cur = 0
	return cur, best, best_before_last


def detect_milestones(players: list[dict]) -> list[str]:
	"""Run all detections over the match's participants.

	Each dict: nick, results ([(winner, team), ...] chronological, RANKED
	only, INCLUDING the match just played), old_rating, new_rating, and
	peak_before (all-time peak rating before this match; None if no prior
	rating history). Returns display-ready lines, possibly empty.
	"""
	lines = []
	for p in players:
		nick = p['nick']
		results = p['results']

		# 1) Match-count milestone — fires win, lose, or draw.
		total = len(results)
		if total in MATCH_MILESTONES:
			lines.append(f"🎖️ **{nick}** just played their **{total}th** ranked match!")

		# 2) Rank-up (upward tier crossings only; going down stays quiet).
		old_r, new_r = p.get('old_rating'), p.get('new_rating')
		if old_r is not None and new_r is not None:
			t_old, t_new = rank_tier(old_r), rank_tier(new_r)
			if t_new > t_old:
				peak = p.get('peak_before')
				t_peak = rank_tier(max(peak, old_r)) if peak is not None else t_old
				emoji, name = rank_label(t_new)
				first = " for the first time" if t_new > t_peak else ""
				lines.append(f"{emoji} **{nick}** reached **{name}**{first}!")

		# 3) New all-time best win streak — only the record-setting win fires.
		cur, best, best_before = _streak_walk(results)
		if best > best_before and best >= BEST_STREAK_FLOOR and cur == best:
			lines.append(f"🔥 **{nick}** — new best win streak: **W{best}**!")

	return lines
