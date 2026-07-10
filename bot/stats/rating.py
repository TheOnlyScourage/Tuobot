# -*- coding: utf-8 -*-
"""Per-channel rating storage and maintenance for a queue: fetching/seeding
ratings, admin adjustments, weekly decay, rank snapping, and season reset. The
MMR formula itself lives in bot/stats/mmr_engine.py."""
from __future__ import annotations

from typing import TYPE_CHECKING

import time

from core.database import db
from core.utils import find, get_nick

from bot.stats import stats

if TYPE_CHECKING:
	from nextcord import Member


class Rating:
	"""Per-channel rating store (table qc_players): fetches/seeds player ratings,
	applies admin rating adjustments, weekly decay, rank snapping, and season
	reset. The per-match MMR change is computed in bot/stats/mmr_engine.py."""

	table = "qc_players"

	def __init__(
			self, channel_id: int, init_rp: int = 1500, init_deviation: int = 300, min_deviation: int | None = None, scale: int = 100,
			loss_scale: int = 100, win_scale: int = 100, draw_bonus: int = 0, ws_boost: bool = False, ls_boost: bool = False
	):
		"""Configure the rating store from queue config. The scaling knobs are kept
		for config compatibility but no longer affect MMR (see mmr_engine.py)."""
		self.channel_id = channel_id
		self.init_rp = init_rp
		self.init_deviation = init_deviation
		self.min_deviation = min_deviation or 0
		self.scale = (scale or 100)/100.0
		self.win_scale = (win_scale or 100)/100.0
		self.loss_scale = (loss_scale or 100)/100.0
		self.draw_bonus = (draw_bonus or 0)/100.0
		self.ws_boost = ws_boost
		self.ls_boost = ls_boost

	async def get_players(self, user_ids) -> list[dict]:
		""" Return rating or initial rating for each member """
		data = await db.select(
			['user_id', 'rating', 'deviation', 'channel_id', 'wins', 'losses', 'draws', 'streak'], self.table,
			where={'channel_id': self.channel_id}
		)
		results = []
		for user_id in user_ids:
			if d := find(lambda p: p['user_id'] == user_id, data):  # noqa: B023
				if d['rating'] is None:
					d['rating'] = self.init_rp
					d['deviation'] = self.init_deviation
				else:
					d['deviation'] = min(self.init_deviation, d['deviation'])
			else:
				d = dict(
					channel_id=self.channel_id, user_id=user_id, rating=self.init_rp,
					deviation=self.init_deviation, wins=0, losses=0, draws=0
				)
			results.append(d)
		return results

	async def set_rating(self, member: Member, rating: int | None = None, deviation: int | None = None, penality: int = 0, reason: str | None = None) -> None:
		"""Admin-set a rating (optionally with a penalty), inserting the row if the
		player is new, and record the change in qc_rating_history."""
		old = await db.select_one(
			('rating', 'deviation'), self.table,
			where=dict(channel_id=self.channel_id, user_id=member.id)
		)

		if not old:
			rating = max(1, rating - penality if rating else self.init_rp - penality)
			await db.insert(
				self.table,
				dict(
					channel_id=self.channel_id, nick=get_nick(member), user_id=member.id,
					rating=rating, deviation=deviation or self.init_deviation
				)
			)
			old = dict(rating=self.init_rp, deviation=self.init_deviation)
		else:
			rating = max(1, rating - penality if rating else old['rating'] - penality)
			old['rating'] = old['rating'] or self.init_rp
			old['deviation'] = old['deviation'] or self.init_deviation
			await db.update(
					self.table,
					dict(rating=rating, deviation=deviation or old['deviation']),
					keys=dict(channel_id=self.channel_id, user_id=member.id)
				)

		await db.insert(
			"qc_rating_history",
			dict(
				channel_id=self.channel_id, user_id=member.id, at=int(time.time()), rating_before=old['rating'],
				deviation_before=old['deviation'], rating_change=rating-old['rating'],
				deviation_change=deviation-old['deviation'] if deviation else 0,
				match_id=None, reason=reason
			)
		)

	async def hide_player(self, user_id: int, hide: bool = True) -> None:
		"""Flag a player hidden (excluded from leaderboards) or unhidden."""
		await db.update(self.table, dict(is_hidden=hide), keys=dict(channel_id=self.channel_id, user_id=user_id))

	async def snap_ratings(self, ranks_table: list[dict]) -> None:
		"""Snap every rating down to the nearest rank threshold at or below it
		(floored at the lowest rank), recording each change in history."""
		ranks = [i['rating'] for i in ranks_table if i['rating'] != 0]
		lowest = min(ranks)
		data = await db.select(('*',), self.table, where=dict(channel_id=self.channel_id))
		history = []
		now = int(time.time())
		for p in (p for p in data if p['rating'] is not None):
			new_rating = max([i for i in ranks if i <= p['rating']] + [lowest])
			history.append(dict(
				user_id=p['user_id'],
				channel_id=self.channel_id,
				at=now,
				rating_before=p['rating'],
				rating_change=new_rating - p['rating'],
				deviation_before=p['deviation'],
				deviation_change=0,
				match_id=None,
				reason="ratings snap"
			))
			p['rating'] = new_rating
		await db.insert_many(self.table, data, on_dublicate='replace')
		await db.insert_many('qc_rating_history', history)

	async def apply_decay(self, rating: int, deviation: int, ranks_table: list[dict]) -> None:
		""" Apply weekly rating and deviation decay """
		now = int(time.time())
		ranks = [i['rating'] for i in ranks_table if i['rating'] != 0]
		data = await stats.last_games(self.channel_id)
		history = []
		to_update = []
		for p in data:
			if None in (p['rating'], p['deviation'], p['at']):
				continue

			new_deviation = min((self.init_deviation, p['deviation'] + deviation))

			min_rating = max([i for i in ranks if i <= p['rating']]+[0])
			if min_rating != 0 and p['at'] < (now-(60*60*24*7)):
				new_rating = max((min_rating, p['rating']-rating))
			else:
				new_rating = p['rating']

			if new_rating != p['rating'] or new_deviation != p['deviation']:
				history.append(dict(
					user_id=p['user_id'],
					channel_id=self.channel_id,
					at=now,
					rating_before=p['rating'],
					rating_change=new_rating-p['rating'],
					deviation_before=p['deviation'],
					deviation_change=new_deviation-p['deviation'],
					match_id=None,
					reason="inactivity rating decay"
				))
				p.pop('at')
				p['deviation'] = new_deviation
				p['rating'] = new_rating
				to_update.append(p)

		if len(history):
			await db.insert_many('qc_rating_history', history)
			await db.insert_many(self.table, to_update, on_dublicate='replace')

	async def reset(self) -> None:
		"""Clear all ratings/deviations for the channel (set to NULL), recording a
		reset in history for anyone who had a non-default rating."""
		data = await db.select(('user_id', 'rating', 'deviation'), self.table, where=dict(channel_id=self.channel_id))
		history = []
		now = int(time.time())

		for p in data:
			if p['rating'] is not None and (p['rating'] != self.init_rp or p['deviation'] != self.init_deviation):
				history.append(dict(
					user_id=p['user_id'],
					channel_id=self.channel_id,
					at=now,
					rating_before=p['rating'],
					rating_change=self.init_rp-p['rating'],
					deviation_before=p['deviation'],
					deviation_change=self.init_deviation-p['deviation'],
					match_id=None,
					reason="ratings reset"
				))

		await db.update(
			self.table, dict(rating=None, deviation=None), keys=dict(channel_id=self.channel_id)
		)
		if len(history):
			await db.insert_many('qc_rating_history', history)
