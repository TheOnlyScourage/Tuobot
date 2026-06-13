# -*- coding: utf-8 -*-
"""Pure helpers for the /subauto command.

Kept free of nextcord / core imports on purpose: the selection rule is the
only genuinely new logic /subauto introduces (the rebalance reuses the
existing Match.init_teams("matchmaking") path), and isolating it here lets
it be unit-tested without a live queue or Discord client.
"""


def pick_available(candidates, busy_ids):
	"""Return the first candidate whose ``id`` is not in ``busy_ids``, else None.

	``/subauto`` uses this to grab the next queued player who isn't already
	committed to another active match. Order is preserved, so the front of
	the queue wins.
	"""
	for candidate in candidates:
		if candidate.id not in busy_ids:
			return candidate
	return None
