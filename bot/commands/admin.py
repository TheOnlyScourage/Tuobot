from __future__ import annotations

__all__ = [
	'noadds', 'noadd', 'forgive', 'rating_seed', 'rating_penality', 'rating_hide',
	'rating_reset', 'rating_snap', 'stats_reset', 'stats_reset_player', 'stats_replace_player',
	'phrases_add', 'phrases_clear', 'undo_match',
	'douche_add', 'douche_summary', 'douche_leaderboard'
]

from time import time
from datetime import timedelta
from nextcord import Member, Embed, Colour

from core.utils import seconds_to_str, get_nick

import bot


async def noadds(ctx: bot.Context) -> None:
	"""List the active queue bans on the channel (id, player, time left, reason)."""
	data = await bot.noadds.get_noadds(ctx)
	now = int(time())
	s = "```markdown\n"
	s += ctx.qc.gt(" ID | Prisoner | Left | Reason")
	s += "\n----------------------------------------\n"
	if len(data):
		s += "\n".join((
			f" {i['id']} | {i['name']} | {seconds_to_str(max(0, (i['at'] + i['duration']) - now))} | {i['reason'] or '-'}"
			for i in data
		))
	else:
		s += ctx.qc.gt("Noadds are empty.")
	await ctx.reply(s + "\n```")


async def noadd(ctx: bot.Context, player: Member, duration: timedelta, reason: str | None = None) -> None:
	"""Ban a player from queues for a duration (defaults to 2 hours)."""
	ctx.check_perms(ctx.Perms.MODERATOR)
	if not duration:
		duration = timedelta(hours=2)
	if duration > timedelta(days=365*100):
		raise bot.Exc.ValueError(ctx.qc.gt("Specified duration time is too long."))
	await bot.noadds.noadd(
		ctx=ctx, member=player, duration=int(duration.total_seconds()), moderator=ctx.author, reason=reason
	)
	await ctx.success(ctx.qc.gt("Banned **{member}** for `{duration}`.").format(
		member=get_nick(player),
		duration=duration.__str__()
	))


async def forgive(ctx: bot.Context, player: Member) -> None:
	"""Lift the active queue ban on a player."""
	ctx.check_perms(ctx.Perms.MODERATOR)
	if await bot.noadds.forgive(ctx=ctx, member=player, moderator=ctx.author):
		await ctx.success(ctx.qc.gt("Done."))
	else:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified member is not banned."))


async def rating_seed(ctx: bot.Context, player: str, rating: int, deviation: int | None = None) -> None:
	"""Manually set a player's rating (and optional deviation)."""
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541
	if not 0 < rating < 10000 or not 0 < (deviation or 1) < 3000:
		raise bot.Exc.ValueError("Bad rating or deviation value.")

	await ctx.qc.rating.set_rating(player, rating=rating, deviation=deviation, reason="manual seeding")
	await ctx.qc.update_rating_roles(player)
	await ctx.success(ctx.qc.gt("Done."))


async def rating_penality(ctx: bot.Context, player: str, penality: int, reason: str | None = None) -> None:
	"""Apply a rating penalty (positive or negative) to a player."""
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541
	if abs(penality) > 10000:
		raise ValueError("Bad penality value.")
	reason = "penality: " + reason if reason else "penality by a moderator"

	await ctx.qc.rating.set_rating(player, penality=penality, reason=reason)
	await ctx.qc.update_rating_roles(player)
	await ctx.success(ctx.qc.gt("Done."))


async def rating_hide(ctx: bot.Context, player: str, hide: bool = True) -> None:
	"""Hide or unhide a player from the leaderboards."""
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541
	await ctx.qc.rating.hide_player(player.id, hide=hide)
	await ctx.success(ctx.qc.gt("Done."))


async def rating_reset(ctx: bot.Context) -> None:
	"""Reset every player's rating on the channel."""
	ctx.check_perms(ctx.Perms.ADMIN)
	await ctx.qc.rating.reset()
	await ctx.success(ctx.qc.gt("Done."))


async def rating_snap(ctx: bot.Context) -> None:
	"""Snap all ratings down to the nearest rank threshold."""
	ctx.check_perms(ctx.Perms.ADMIN)
	await ctx.qc.rating.snap_ratings(ctx.qc._ranks_table)
	await ctx.success(ctx.qc.gt("Done."))


async def stats_reset(ctx: bot.Context) -> None:
	"""Wipe all match stats and ratings for the channel."""
	ctx.check_perms(ctx.Perms.ADMIN)
	await bot.stats.reset_channel(ctx.qc.id)
	await ctx.success(ctx.qc.gt("Done."))


async def stats_reset_player(ctx: bot.Context, player: str) -> None:
	"""Wipe one player's stats and rating on the channel."""
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (player := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541

	await bot.stats.reset_player(ctx.qc.id, player.id)
	await ctx.success(ctx.qc.gt("Done."))


async def stats_replace_player(ctx: bot.Context, player1: str, player2: str) -> None:
	"""Transfer player1's records onto player2's id."""
	ctx.check_perms(ctx.Perms.ADMIN)
	if (player1 := await ctx.get_member(player1)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541
	if (player2 := await ctx.get_member(player2)) is None:
		raise bot.Exc.SyntaxError(f"Specified member not found on the server.")  # noqa: F541

	await bot.stats.replace_player(ctx.qc.id, player1.id, player2.id, get_nick(player2))
	await ctx.success(ctx.qc.gt("Done."))


async def phrases_add(ctx: bot.Context, player: Member, phrase: str) -> None:
	"""Add a custom phrase for a player."""
	ctx.check_perms(ctx.Perms.MODERATOR)
	await bot.noadds.phrases_add(ctx, player, phrase)
	await ctx.success(ctx.qc.gt("Done."))


async def phrases_clear(ctx: bot.Context, player: Member) -> None:
	"""Clear a player's custom phrases."""
	ctx.check_perms(ctx.Perms.MODERATOR)
	await bot.noadds.phrases_clear(ctx, member=player)
	await ctx.success(ctx.qc.gt("Done."))


async def undo_match(ctx: bot.Context, match_id: int) -> None:
	"""Reverse a recorded match by id, rolling back its rating changes and any
	Hogwarts house points it awarded (via the house_awards ledger)."""
	ctx.check_perms(ctx.Perms.MODERATOR)

	# None = match not found; a dict (possibly empty) = undone, with any
	# reverted house points listed. `if result:` would misread {} as failure.
	result = await bot.stats.undo_match(ctx, match_id)
	if result is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Could not find match with specified id."))
	msg = ctx.qc.gt("Done.")
	if result:
		reverted = ", ".join(f"{house} -{points}" for house, points in result.items())
		msg += f" House points reverted: {reverted}."
	await ctx.success(msg)


async def douche_add(ctx: bot.Context, player: Member, target: Member) -> None:
	"""Record that one player 'douched' another."""
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (member := await ctx.get_member(player)) is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
	if (target_member := await ctx.get_member(target)) is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
	await bot.douche.douche.add(ctx.channel.guild.id, member, target_member, ctx.author)
	await ctx.success(ctx.qc.gt("Recorded: **{member}** douched **{target}**.").format(
		member=get_nick(member), target=get_nick(target_member)
	))


async def douche_summary(ctx: bot.Context, player: Member | None = None) -> None:
	"""Show a player's douche record: received, given, and recent."""
	target = ctx.author if player is None else await ctx.get_member(player)
	if not target:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
	data = await bot.douche.douche.user_summary(ctx.channel.guild.id, target)
	embed = Embed(title=f"Douche record — {get_nick(target)}", colour=Colour(0xCD5C5C))
	embed.add_field(name="Received", value=str(data['received']), inline=True)
	embed.add_field(name="Given", value=str(data['given']), inline=True)
	if data['recent']:
		now = int(time())
		embed.add_field(
			name="Recently douched",
			value="\n".join(
				f"• {r['target_name']} ({seconds_to_str(max(0, now - r['at']))} ago)"
				for r in data['recent']
			),
			inline=False
		)
	await ctx.reply(embed=embed)


async def douche_leaderboard(ctx: bot.Context) -> None:
	"""Show the douche leaderboard for the guild."""
	rows = await bot.douche.douche.leaderboard(ctx.channel.guild.id)
	if not rows:
		raise bot.Exc.NotFoundError(ctx.qc.gt("No douche records yet."))
	embed = Embed(title="Douche leaderboard", colour=Colour(0xCD5C5C))
	embed.add_field(
		name="Player",
		value="\n".join(f"**{i + 1}.** {r['name']}" for i, r in enumerate(rows)),
		inline=True
	)
	embed.add_field(
		name="Count",
		value="\n".join(str(r['count']) for r in rows),
		inline=True
	)
	await ctx.reply(embed=embed)
