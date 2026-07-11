# -*- coding: utf-8 -*-
"""
Captain streak tracker.

Used by the `captain_role` pick_captains mode to enforce a "no more than
2 consecutive captain duties" rule. A player who captained the previous
2 matches in a row is skipped on the 3rd attempt; the next time they
queue and DON'T get picked their streak resets to 0.

DB schema (created at startup):
  captain_streak (
    channel_id  BIGINT,
    user_id     BIGINT,
    streak      INT,
    last_match  INT,
    PRIMARY KEY (channel_id, user_id)
  )

Public API:
  - init_captain_streak_table()      — startup
  - get_streak(channel_id, user_id)  — returns current streak (0 if absent)
  - record_captain(channel_id, uid)  — call when a player was made captain;
                                       increments their streak
  - reset_streak(channel_id, uid)    — call when a player was eligible but
                                       NOT picked; zeroes their streak
"""

from __future__ import annotations

import time
from core.console import log
from core.database import db


# How many consecutive captain duties before the player gets a forced rest.
# 2 means: streak 0 OK, streak 1 OK, streak 2 SKIP this time.
CAPTAIN_STREAK_LIMIT = 2


async def init_captain_streak_table() -> None:
	"""Create the captain_streak table if missing."""
	await db._ensure_table(dict(
		tname="captain_streak",
		columns=[
			dict(cname="channel_id", ctype=db.types.int),
			dict(cname="user_id",    ctype=db.types.int),
			dict(cname="streak",     ctype=db.types.int, notnull=True, default=0),
			dict(cname="last_match", ctype=db.types.int),
		],
		primary_keys=["channel_id", "user_id"],
	))


async def get_streak(channel_id: int, user_id: int) -> int:
	"""Return the player's current streak on this channel. 0 if no row."""
	try:
		row = await db.select_one(
			('streak',),
			'captain_streak',
			where={'channel_id': channel_id, 'user_id': user_id},
		)
		return (row['streak'] if row else 0) or 0
	except Exception as exc:
		log.error(f"[captain_streak] get_streak failed: {exc}")
		return 0


async def record_captain(channel_id: int, user_id: int) -> None:
	"""Increment the player's captain streak. Called once per captain pick."""
	try:
		now = int(time.time())
		row = await db.select_one(
			('streak',),
			'captain_streak',
			where={'channel_id': channel_id, 'user_id': user_id},
		)
		if row is None:
			await db.insert('captain_streak', dict(
				channel_id=channel_id,
				user_id=user_id,
				streak=1,
				last_match=now,
			))
		else:
			await db.update(
				'captain_streak',
				dict(streak=(row['streak'] or 0) + 1, last_match=now),
				keys={'channel_id': channel_id, 'user_id': user_id},
			)
	except Exception as exc:
		log.error(f"[captain_streak] record_captain failed: {exc}")


async def reset_streak(channel_id: int, user_id: int) -> None:
	"""Zero a player's streak. Called when they were eligible but not picked."""
	try:
		row = await db.select_one(
			('streak',),
			'captain_streak',
			where={'channel_id': channel_id, 'user_id': user_id},
		)
		if row is None:
			return  # nothing to reset
		await db.update(
			'captain_streak',
			dict(streak=0),
			keys={'channel_id': channel_id, 'user_id': user_id},
		)
	except Exception as exc:
		log.error(f"[captain_streak] reset_streak failed: {exc}")


def is_capped(streak: int) -> bool:
	"""Return True if the streak has hit the cooldown threshold."""
	return streak >= CAPTAIN_STREAK_LIMIT
