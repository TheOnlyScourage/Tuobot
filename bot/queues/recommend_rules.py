# -*- coding: utf-8 -*-
"""Pure decision rules for /recommend (no bot/db/discord imports, so the
dependency-free test suite can load this by path — mmr_engine-style).

Vocabulary: a *format* is a label like "3v3"; its *team size* is 3 and it
*needs* 2 * 3 = 6 players. A format may only be recommended from a queue
that is strictly bigger than it (you can't recommend 5v5 from a 5v5 queue),
so the 6v6 queue (size 12) exposes all five formats.
"""
from __future__ import annotations

from bot.constants import RECOMMEND_FORMATS


def format_team_size(label: str) -> int | None:
	"""Team size for a format label ("3v3" -> 3), or None if unknown."""
	return RECOMMEND_FORMATS.get(label)


def players_needed(team_size: int) -> int:
	return 2 * team_size


def allowed_formats(queue_size: int) -> list[str]:
	"""Formats strictly smaller than the queue, in the table's order."""
	return [label for label, ts in RECOMMEND_FORMATS.items() if players_needed(ts) < queue_size]


def pick_roster(accepted_ids: list[int], queued_ids: set[int], needed: int) -> list[int] | None:
	"""First-come roster: the earliest accepters who are STILL in the queue.
	Returns exactly `needed` ids, or None if not enough remain eligible
	(accepters who /removed themselves silently drop out)."""
	eligible = [uid for uid in accepted_ids if uid in queued_ids]
	if len(eligible) < needed:
		return None
	return eligible[:needed]


def cooldown_left(now: float, until: float) -> int:
	"""Whole seconds of cooldown remaining (0 when expired)."""
	return max(0, int(until - now))
