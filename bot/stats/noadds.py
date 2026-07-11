# -*- coding: utf-8 -*-
"""Queue-ban (noadd) system and per-player custom phrases, with a background
job that auto-expires bans once their duration elapses."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING
from core.database import db
from core.utils import get_nick

if TYPE_CHECKING:
	import bot
	from nextcord import Member

db.ensure_table(dict(
	tname="noadds",
	columns=[
		dict(cname="id", ctype=db.types.int, autoincrement=True),
		dict(cname="guild_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="name", ctype=db.types.str),
		dict(cname="is_active", ctype=db.types.bool, default=1),
		dict(cname="at", ctype=db.types.int),
		dict(cname="duration", ctype=db.types.int),
		dict(cname="reason", ctype=db.types.text),
		dict(cname="by", ctype=db.types.str),
		dict(cname="released_by", ctype=db.types.str)
	],
	primary_keys=["id"]
))

db.ensure_table(dict(
	tname="qc_phrases",
	columns=[
		dict(cname="channel_id", ctype=db.types.int),
		dict(cname="user_id", ctype=db.types.int),
		dict(cname="phrase", ctype=db.types.text),
	]
))


class NoAdds:
	"""Per-guild queue bans and per-player custom phrases."""

	def __init__(self):
		self.next_tick = 0
		self.phrase_cursors = {}

	async def get_user(self, ctx: bot.Context, member: Member) -> list:
		""" returns [ban_left, phrase]"""

		m_noadd = await db.select_one(
			['duration', 'at'], 'noadds', where=dict(guild_id=ctx.channel.guild.id, user_id=member.id, is_active=1)
		)
		ban_left = max(0, (m_noadd['duration']+m_noadd['at'])-int(time.time())) if m_noadd else 0
		phrases = await db.select(['phrase'], 'qc_phrases', where=dict(channel_id=ctx.channel.id, user_id=member.id))

		if not phrases:
			phrase = None
		else:
			# Cycle through the phrases in the order they were added (A, B, C, A, ...)
			# instead of picking at random. The cursor is per (channel, player) and
			# lives in memory, so it restarts from the first phrase after a reboot.
			key = (ctx.channel.id, member.id)
			idx = self.phrase_cursors.get(key, 0)
			phrase = phrases[idx % len(phrases)]['phrase']
			self.phrase_cursors[key] = idx + 1
		return [ban_left, phrase]

	@staticmethod
	async def phrases_add(ctx: bot.Context, member: Member, phrase: str) -> None:
		"""Add a custom phrase for a player in this channel."""
		await db.insert('qc_phrases', dict(channel_id=ctx.channel.id, user_id=member.id, phrase=phrase))

	@staticmethod
	async def phrases_clear(ctx: bot.Context, member: Member | None = None) -> None:
		"""Clear phrases for one player, or the whole channel if member is None."""
		if member:
			await db.delete('qc_phrases', where=dict(channel_id=ctx.channel.id, user_id=member.id))
		else:
			await db.delete('qc_phrases', where=dict(channel_id=ctx.channel.id))

	@staticmethod
	async def noadd(ctx: bot.Context, member: Member, duration: int, moderator: Member, reason: str | None = None) -> None:
		"""Ban a member from queues for duration seconds, replacing any active ban."""
		await db.update(
			'noadds',
			dict(is_active=0, released_by="another noadd"),
			keys=dict(guild_id=ctx.channel.guild.id, user_id=member.id, is_active=1)
		)
		await db.insert('noadds', dict(
			guild_id=ctx.channel.guild.id,
			user_id=member.id,
			name=get_nick(member),
			at=int(time.time()),
			duration=duration,
			reason=reason,
			by=get_nick(moderator)
		))

	@staticmethod
	async def forgive(ctx: bot.Context, member: Member, moderator: Member) -> bool:
		"""Lift an active ban for the member; return False if none exists."""
		noadd_id = await db.select_one(
			['id'], 'noadds', where=dict(guild_id=ctx.channel.guild.id, user_id=member.id, is_active=1)
		)
		if not noadd_id:
			return False
		await db.update(
			'noadds',
			dict(is_active=0, released_by=get_nick(moderator)),
			keys=noadd_id
		)
		return True

	@staticmethod
	async def get_noadds(ctx: bot.Context) -> list:
		"""Return all active bans in the guild."""
		return await db.select(['*'], 'noadds', where=dict(guild_id=ctx.channel.guild.id, is_active=1))

	async def think(self, frame_time: int) -> None:
		"""Frame tick: expire bans whose duration has elapsed (checked once a minute)."""
		if frame_time > self.next_tick:
			await db.execute("UPDATE `noadds` SET is_active=0, released_by='time' WHERE (`at`+`duration`)<%s", (frame_time, ))
			self.next_tick = frame_time + 60


noadds = NoAdds()
