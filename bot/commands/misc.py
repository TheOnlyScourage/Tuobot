from __future__ import annotations

__all__ = ['expire', 'switch_dms', 'cointoss', 'set_nick']

from time import time
from datetime import timedelta
from random import randint

from nextcord import DiscordException

from core.utils import seconds_to_str
from core.database import db

import bot

MAX_EXPIRE_TIME = timedelta(hours=12)

# (auto_ready, default_expire and allow_offline used to live here too. Nothing
# called them: /auto_ready and /allow_offline are written inline in
# bot/context/slash/commands.py, and default_expire had no command at all.)


async def expire(ctx: bot.Context, duration: timedelta | None = None) -> None:
	"""Set or report the caller expire timer (time until auto-removal from queues)."""
	if not duration:
		if task := bot.expire.get(ctx.qc, ctx.author):
			await ctx.reply(ctx.qc.gt("You have {duration} expire time left.").format(
				duration=seconds_to_str(task.at - int(time()))
			))
			return
		await ctx.reply(ctx.qc.gt("You don't have an expire timer set right now."))
		return

	if duration > MAX_EXPIRE_TIME:
		raise bot.Exc.ValueError(ctx.qc.gt("Expire time must be less than {time}.".format(
			time=MAX_EXPIRE_TIME.__str__()
		)))

	bot.expire.set(ctx.qc, ctx.author, duration.total_seconds())
	await ctx.success(ctx.qc.gt("Set your expire time to {duration}.").format(
		duration=duration.__str__()
	))


async def switch_dms(ctx: bot.Context) -> None:
	"""Toggle the caller DM notifications on or off."""
	data = await db.select_one(('allow_dm',), 'players', where={'user_id': ctx.author.id})
	if data:
		allow_dm = 1 if data['allow_dm'] == 0 else 0
		await db.update('players', {'allow_dm': allow_dm}, keys={'user_id': ctx.author.id})
	else:
		allow_dm = 0
		await db.insert('players', {'allow_dm': allow_dm, 'user_id': ctx.author.id})

	if allow_dm:
		await ctx.success(ctx.qc.gt("Your DM notifications is now turned on."))
	else:
		await ctx.success(ctx.qc.gt("Your DM notifications is now turned off."))


async def cointoss(ctx: bot.Context, side: str | None = None) -> None:
	"""Flip a coin; the caller may call heads or tails."""
	pick = 0
	if side in ["tails", ctx.qc.gt("tails")]:
		pick = 1

	result = randint(0, 1)
	if pick == result:
		await ctx.reply(ctx.qc.gt("{member} won, its **{side}**!").format(
			member=ctx.author.mention, side=ctx.qc.gt(["heads", "tails"][result])
		))
	else:
		await ctx.reply(ctx.qc.gt("{member} lost, its **{side}**!").format(
			member=ctx.author.mention, side=ctx.qc.gt(["heads", "tails"][result])
		))


async def set_nick(ctx: bot.Context, nick: str) -> None:
	"""Set the caller's nickname, with the [rating] prefix when this channel
	shows ratings in nicknames (the `rating_nicks` setting).

	It used to look the rating up with the player's own id as the CHANNEL id,
	which never matched, so every nick came out as the default rating until
	the player's next ranked match rewrote it."""
	from bot.queue_channel import _build_rated_nick
	data = await db.select_one(
		['rating'], 'qc_players',
		where={'channel_id': ctx.qc.rating.channel_id, 'user_id': ctx.author.id}
	)
	rating = data['rating'] if data and data['rating'] is not None else ctx.qc.rating.init_rp
	new_nick = _build_rated_nick(rating, nick) if ctx.qc.cfg.rating_nicks else nick[:32]
	try:
		await ctx.author.edit(nick=new_nick)
	except DiscordException:  # the server owner, or anyone ranked above the bot
		raise bot.Exc.PermissionError(ctx.qc.gt("I'm not allowed to change your nickname here."))
	await ctx.ignore(ctx.qc.gt("Done."))
