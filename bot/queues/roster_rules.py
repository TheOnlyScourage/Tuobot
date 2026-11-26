# -*- coding: utf-8 -*-
"""Keep every person to ONE slot in a queue or a match. Pure: no bot, db or
Discord imports, so the dependency-free test suite loads it by path. A
"player" is anything with an `.id` (a Discord member in the bot).

Why it exists: on Nov 11 and Nov 20 the 1v1 started Worpel, then
GreenBlackKatt, against themselves, and the result then failed to save.
Starting the 1v1 left players who were also in a higher-priority queue
listed in it. When the check-in failed, the queue refilled from that
stale list, so the ready player was added twice. The trigger was fixed on
Nov 25. These helpers make the outcome impossible whatever the trigger."""
from __future__ import annotations


def distinct(players: list) -> list:
	"""`players` with repeats removed (same id), keeping the first."""
	return distinct_across((), players)[0]


def distinct_across(exclude, *groups) -> list[list]:
	"""Each group with repeats removed. Anyone kept in an earlier group is
	dropped from the later ones, and anyone in `exclude` is dropped from
	all of them. Order inside each group is kept.

	revert() calls it as distinct_across(not_ready, ready, standby, queued):
	the ready players keep their places, nobody fills two slots, and
	whoever just wasn't ready isn't put straight into the next match."""
	taken = {p.id for p in exclude}
	out = []
	for group in groups:
		kept = []
		for p in group:
			if p.id not in taken:
				taken.add(p.id)
				kept.append(p)
		out.append(kept)
	return out
