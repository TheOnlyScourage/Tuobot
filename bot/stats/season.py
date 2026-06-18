# -*- coding: utf-8 -*-
import time
from core.database import db


async def init_season_table():
	"""Create the season_info table if it doesn't exist."""
	await db._ensure_table(dict(
		tname="season_info",
		columns=[
			dict(cname="id",            ctype=db.types.int, autoincrement=True),
			dict(cname="channel_id",    ctype=db.types.int),
			dict(cname="season_number", ctype=db.types.int),
			dict(cname="ended_at",      ctype=db.types.int),
		],
		primary_keys=["id"]
	))


async def get_current_season_number(channel_id: int) -> int:
	"""Return the current season number (1 for first season, increments on each end)."""
	rows = await db.select(['id'], 'season_info', where={'channel_id': channel_id})
	return len(rows) + 1


async def record_season_end(channel_id: int, season_number: int):
	"""Persist a season-end record."""
	await db.insert('season_info', dict(
		channel_id=channel_id,
		season_number=season_number,
		ended_at=int(time.time()),
	))
