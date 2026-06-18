# -*- coding: utf-8 -*-
"""
Tracks check-in violations per player and auto-bans on threshold.

Violation types:
  'missed'  – player did not ready up before the timeout
  'aborted' – player actively clicked the abort emoji or triggered abort
"""
import time
from core.database import db
from core.utils import get_nick
from nextcord import Embed, Colour

BAN_THRESHOLD    = 3               # total violations that trigger auto-ban
BAN_WINDOW_DAYS  = 3               # rolling window in days
BAN_WINDOW_SECS  = BAN_WINDOW_DAYS * 24 * 3600
BAN_DURATION_SECS = 3600           # 1-hour auto-ban


async def init_checkin_tracker_table():
	"""Create the checkin_violations table if it doesn't exist."""
	await db._ensure_table(dict(
		tname="checkin_violations",
		columns=[
			dict(cname="id",             ctype=db.types.int, autoincrement=True),
			dict(cname="guild_id",       ctype=db.types.int),
			dict(cname="user_id",        ctype=db.types.int),
			dict(cname="at",             ctype=db.types.int),
			dict(cname="violation_type", ctype=db.types.str),  # 'missed' | 'aborted'
		],
		primary_keys=["id"]
	))


async def record_violation(channel, member, violation_type: str):
	"""
	Record one check-in violation, post the warning embed, and
	auto-ban if the rolling threshold is reached.
	"""
	now          = int(time.time())
	guild_id     = channel.guild.id
	window_start = now - BAN_WINDOW_SECS

	# Persist the violation
	await db.insert('checkin_violations', dict(
		guild_id=guild_id,
		user_id=member.id,
		at=now,
		violation_type=violation_type,
	))

	# Count all recent violations within the rolling window
	rows = await db.fetchall(
		"SELECT violation_type FROM checkin_violations "
		"WHERE guild_id=%s AND user_id=%s AND at>=%s",
		[guild_id, member.id, window_start],
	)
	missed  = sum(1 for r in rows if r['violation_type'] == 'missed')
	aborted = sum(1 for r in rows if r['violation_type'] == 'aborted')
	total   = missed + aborted

	# Warning embed — matches Image 1 style
	embed = Embed(colour=Colour(0xe67e22))
	embed.description = (
		f"{member.mention} you are responsible for the check-in phase failing.\n"
		f"**Last {BAN_WINDOW_DAYS} days**\n"
		f"Missed Check-ins: **{missed}**\n"
		f"Aborted Queues: **{aborted}**\n\n"
		f"If you cause **{BAN_THRESHOLD}** check-ins to fail in the span of "
		f"{BAN_WINDOW_DAYS} days, you will be temporarily banned from adding to queues."
	)
	await channel.send(embed=embed)

	# Auto-ban when threshold is hit
	if total >= BAN_THRESHOLD:
		# Deactivate any existing active noadd first
		await db.update(
			'noadds',
			dict(is_active=0, released_by="auto-ban override"),
			keys=dict(guild_id=guild_id, user_id=member.id, is_active=1),
		)
		await db.insert('noadds', dict(
			guild_id=guild_id,
			user_id=member.id,
			name=get_nick(member),
			at=now,
			duration=BAN_DURATION_SECS,
			reason=f"Auto-ban: {total} check-in violations in {BAN_WINDOW_DAYS} days",
			by="Tuobot",
		))
		ban_embed = Embed(colour=Colour(0xe74c3c))
		ban_embed.description = (
			f"🚫 {member.mention} has been temporarily banned from queues for **1 hour** "
			f"due to repeated check-in violations."
		)
		await channel.send(embed=ban_embed)
