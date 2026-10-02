# -*- coding: utf-8 -*-
"""Pure rules for the /autoreply prank responder (no bot/db/discord imports,
so the dependency-free test suite can load this by path — mmr_engine-style).
The glue that talks to Discord and MySQL lives in bot/stats/autoreply.py."""
from __future__ import annotations

from bot.constants import AUTOREPLY_COOLDOWN, AUTOREPLY_LINES


class _SafeFields(dict):
	"""format_map helper: an unknown {placeholder} is left verbatim instead of
	raising, so a typo in a line can never crash a reply."""
	def __missing__(self, key):
		return '{' + key + '}'


def render_line(line: str, pinger: str, mention: str) -> str:
	"""Fill a line's placeholders: {pinger} = the pinger's server name,
	{mention} = an @-ping of them."""
	return line.format_map(_SafeFields(pinger=pinger, mention=mention))


def line_at(cursor: int) -> str:
	"""The story line for a cursor position, wrapping past the end."""
	return AUTOREPLY_LINES[cursor % len(AUTOREPLY_LINES)]


def advance(cursor: int) -> int:
	return (cursor + 1) % len(AUTOREPLY_LINES)


def on_cooldown(last: float | None, now: float, cooldown: float = AUTOREPLY_COOLDOWN) -> bool:
	"""True while a pinger's last reply is more recent than the cooldown.
	A never-seen pinger (last is None) is never on cooldown — explicit, rather
	than relying on `now` being a large epoch timestamp."""
	return last is not None and (now - last) < cooldown
