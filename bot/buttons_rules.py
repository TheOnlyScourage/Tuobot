# -*- coding: utf-8 -*-
"""Pure helpers for Tuobot's buttons and menus (bot/buttons.py): the fixed
custom ids and the draft pick-menu options. No nextcord import, so the test
suite can load this file directly.

Every button/menu is a PERSISTENT component: its custom_id never changes, one
handler per id is registered at startup, and a press is routed by WHO pressed
it (exactly like the matching slash command), never by which message it was
on. That keeps old buttons working after a redeploy and means no per-message
state to clean up."""

QUEUE_JOIN = "tuobot:queue:join"
QUEUE_LEAVE = "tuobot:queue:leave"
CHECKIN_READY = "tuobot:checkin:ready"
CHECKIN_NOT_READY = "tuobot:checkin:notready"
DRAFT_PICK = "tuobot:draft:pick"
REPORT_LOSS = "tuobot:report:loss"
REPORT_ABORT = "tuobot:report:abort"

ALL_IDS = (QUEUE_JOIN, QUEUE_LEAVE, CHECKIN_READY, CHECKIN_NOT_READY, DRAFT_PICK, REPORT_LOSS, REPORT_ABORT)

# Discord's limits for a select menu.
MAX_OPTIONS = 25
MAX_TEXT = 100


def _clip(text: str) -> str:
	text = str(text)
	return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"


def pick_options(players, nick_of, ratings=None):
	"""Options for the captain's pick menu: [{'label', 'value', 'description'}].

	Highest rated first when ratings are known (else by name), at most 25
	(Discord's limit; anyone beyond that is still pickable with /pick), with
	labels and descriptions cut to 100 characters. `value` is the user id as
	text, so it survives the round trip through Discord."""
	ratings = ratings or {}
	seen = set()
	rows = []
	for p in players:
		if p.id in seen:
			continue
		seen.add(p.id)
		rows.append(p)
	if ratings:
		rows.sort(key=lambda p: (-(ratings.get(p.id) or 0), nick_of(p).lower()))
	else:
		rows.sort(key=lambda p: nick_of(p).lower())
	return [
		dict(
			label=_clip(nick_of(p) or p.id),
			value=str(p.id),
			description=_clip(f"{ratings[p.id]} rating") if ratings.get(p.id) is not None else None,
		)
		for p in rows[:MAX_OPTIONS]
	]
