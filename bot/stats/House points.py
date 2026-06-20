# -*- coding: utf-8 -*-
"""
Hogwarts house points system.

Rules:
  - Every winning player awards 5 points to THEIR Discord house.
  - The winning captain (team[0]) awards 10 points instead of 5.
  - A player with no house role contributes nothing.
  - Points stack — same house can be awarded multiple times per match.

DB schema (created at startup):
  house_points (house TEXT PK, points INT, last_updated INT)
"""

import time
from core.console import log
from core.database import db


# House Discord role IDs → house name (must match HOUSE_ROLES in match.py)
HOUSE_ROLES = {
	1468807660760596593: 'Hufflepuff',
	1467995936621068308: 'Slytherin',
	1468807395659485265: 'Gryffindor',
	1468807668197097711: 'Ravenclaw',
}

ALL_HOUSES = list(HOUSE_ROLES.values())

POINTS_PER_PLAYER  = 5
POINTS_FOR_CAPTAIN = 10


async def init_house_points_table():
	"""Create the house_points table and seed all four houses to 0."""
	await db._ensure_table(dict(
		tname="house_points",
		columns=[
			dict(cname="house",        ctype=db.types.str),
			dict(cname="points",       ctype=db.types.int, notnull=True, default=0),
			dict(cname="last_updated", ctype=db.types.int),
		],
		primary_keys=["house"],
	))

	# Seed missing houses
	for h in ALL_HOUSES:
		row = await db.select_one(('house',), 'house_points', where={'house': h})
		if not row:
			await db.insert('house_points', dict(
				house=h, points=0, last_updated=int(time.time())
			))


def _get_house(member) -> str | None:
	"""Return the player's Hogwarts house name from their Discord roles."""
	if member is None:
		return None
	for role in getattr(member, 'roles', []) or []:
		if role.id in HOUSE_ROLES:
			return HOUSE_ROLES[role.id]
	return None


async def _add_points(house: str, amount: int):
	"""Increment a house's running point total."""
	row = await db.select_one(('points',), 'house_points', where={'house': house})
	now = int(time.time())
	if row is None:
		await db.insert('house_points', dict(
			house=house, points=amount, last_updated=now
		))
	else:
		await db.update(
			'house_points',
			dict(points=(row['points'] or 0) + amount, last_updated=now),
			keys={'house': house}
		)


async def award_for_win(winning_team) -> dict:
	"""
	Award points to every house represented on the winning team.

	  - Captain (team[0])      → POINTS_FOR_CAPTAIN  (10)
	  - All other players       → POINTS_PER_PLAYER   (5)

	Returns a dict {house_name: points_awarded} for the announcement embed.
	Players without a house role contribute nothing.
	"""
	awarded: dict[str, int] = {}

	for i, player in enumerate(winning_team):
		house = _get_house(player)
		if house is None:
			continue   # no role → no points

		amount = POINTS_FOR_CAPTAIN if i == 0 else POINTS_PER_PLAYER
		awarded[house] = awarded.get(house, 0) + amount

	for house, amount in awarded.items():
		try:
			await _add_points(house, amount)
		except Exception as e:
			log.error(f"[house_points] failed to award {amount} to {house}: {e}")

	return awarded


async def get_standings() -> list[dict]:
	"""Return all four houses sorted by points descending."""
	rows = await db.fetchall(
		"SELECT house, points FROM house_points ORDER BY points DESC, house ASC",
		[]
	)
	# Ensure all four houses appear even if not in the table yet
	seen = {r['house']: r['points'] for r in rows}
	out = []
	for h in ALL_HOUSES:
		out.append({'house': h, 'points': seen.get(h, 0)})
	# Re-sort the merged list
	out.sort(key=lambda r: (-r['points'], r['house']))
	return out


async def reset_all():
	"""Zero out every house. Used by an admin command."""
	now = int(time.time())
	for h in ALL_HOUSES:
		await db.update('house_points', dict(points=0, last_updated=now), keys={'house': h})
