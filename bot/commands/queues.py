from __future__ import annotations

__all__ = [
	'add', 'remove', 'who', 'add_player', 'remove_player', 'start',
	'reset', 'server', 'remove_all', 'recommend'
]

from nextcord import Member
from core.utils import error_embed, ok_embed, join_and, find, get_nick, spawn, send_dm
import bot
# Join / Leave under every public queue update (the same as /add and /remove).
from bot.buttons import QueueButtons



async def add(ctx: bot.Context, queues: str | None = None) -> None:
	""" add author to channel queues """
	phrase = await ctx.qc.check_allowed_to_add(ctx, ctx.author)

	targets = queues.lower().split(" ") if queues else []
	if not len(targets) and len(ctx.qc.queues) == 1:
		t_queues = ctx.qc.queues
	elif len(targets):
		t_queues = [q for q in ctx.qc.queues if any(
			(t == q.name.lower() or t in (a["alias"].lower() for a in q.cfg.aliases) for t in targets)
		)]
	else:
		t_queues = [q for q in ctx.qc.queues if len(q.queue) and q.cfg.is_default]
		if not len(t_queues):
			t_queues = [q for q in ctx.qc.queues if q.cfg.is_default]

	# Track which queue each result came from so we can detect standby placement
	qr = dict()
	standby_queues = []
	for q in t_queues:
		qr[q] = await q.add_member(ctx, ctx.author)
		if qr[q] == bot.Qr.QueueStarted:
			await ctx.notice(ctx.qc.topic, view=QueueButtons())
			return
		# If the player was routed to the standby pool, remember the queue
		if qr[q] == bot.Qr.Success and ctx.author in getattr(q, 'standby', []):
			standby_queues.append(q)

	if len(not_allowed := [q for q in qr.keys() if qr[q] == bot.Qr.NotAllowed]):
		await ctx.error(ctx.qc.gt("You are not allowed to add to {queues} queues.".format(
			queues=join_and([f"**{q.name}**" for q in not_allowed])
		)))

	if standby_queues:
		# Standby placement: a private notice + a DM, so the player isn't
		# confused by the queue topic not listing them.
		queue_names = join_and([f"**{q.name}**" for q in standby_queues])
		standby_msg = (
			f"\u23f3 You\u2019re on **standby** for {queue_names}.\n"
			f"A match is currently in check-in. If any of the current players "
			f"don\u2019t ready up in time, you\u2019ll be pulled in to compete for the open slot."
		)
		# This used to be ctx.success(..., ephemeral=True). success() can't take
		# `ephemeral`, so it raised a TypeError and the fallback posted the
		# "You're on standby" notice publicly every time.
		await ctx.ignore(embed=ok_embed(standby_msg, title="On Standby"))
		spawn(send_dm(ctx.author, (
			f"\u23f3 You\u2019re on standby for {queue_names} in {ctx.channel.mention}.\n"
			f"A match is currently in check-in. If anyone fails to ready up, "
			f"you\u2019ll be pulled in \u2014 keep an eye on the channel."
		)), name="standby DM")
		return

	if bot.Qr.Success in qr.values():
		await ctx.qc.update_expire(ctx.author)
		if phrase:
			await ctx.reply(phrase)
		await ctx.notice(ctx.qc.topic, view=QueueButtons())
	else:
		await ctx.ignore(content=ctx.qc.topic, embed=error_embed(ctx.qc.gt("Action had no effect."), title=None))


async def remove(ctx: bot.Context, queues: str | None = None) -> None:
	""" remove author from channel queues """
	targets = queues.lower().split(" ") if queues else []

	if not len(targets):
		t_queues = [q for q in ctx.qc.queues if q.is_added(ctx.author)]
	else:
		t_queues = [
			q for q in ctx.qc.queues if
			any((t == q.name.lower() or t in (a["alias"].lower() for a in q.cfg.aliases) for t in targets)) and
			q.is_added(ctx.author)
		]

	if len(t_queues):
		for q in t_queues:
			q.pop_members(ctx.author)

		if not any((q.is_added(ctx.author) for q in ctx.qc.queues)):
			bot.expire.cancel(ctx.qc, ctx.author)

		await ctx.notice(ctx.qc.topic, view=QueueButtons())
	else:
		await ctx.ignore(content=ctx.qc.topic, embed=error_embed(ctx.qc.gt("Action had no effect."), title=None))


async def remove_all(ctx: bot.Context, player: Member | None = None) -> None:
	"""Remove a player from ALL queued channels on this server."""
	# Moderator check if removing someone other than themselves
	if player is not None and player.id != ctx.author.id:
		ctx.check_perms(ctx.Perms.MODERATOR)

	target   = player or ctx.author
	guild_id = ctx.channel.guild.id

	removed_from = []  # list of (channel, [queue_names])

	for qc in bot.queue_channels.values():
		if qc.guild_id != guild_id:
			continue

		affected = []
		for q in qc.queues:
			if q.is_added(target):
				q.pop_members(target)
				affected.append(q.name)

		if affected:
			channel = ctx.channel.guild.get_channel(qc.id)
			bot.expire.cancel(qc, target)
			removed_from.append((channel, affected))

	if not removed_from:
		raise bot.Exc.NotFoundError(
			f"**{get_nick(target)}** is not in any queues on this server."
		)

	lines = []
	for channel, queue_names in removed_from:
		chan_str = channel.mention if channel else "Unknown channel"
		lines.append(f"{chan_str} · **{', '.join(queue_names)}**")

	await ctx.success(
		"\n".join(lines),
		title=f"Removed **{get_nick(target)}** from all queued channels on this server:"
	)


async def who(ctx: bot.Context, queues: str | None = None) -> None:
	""" List added players """
	targets = queues.lower().split(" ") if queues else []

	if len(targets):
		t_queues = [
			q for q in ctx.qc.queues if
			any((t == q.name.lower() or t in (a["alias"].lower() for a in q.cfg.aliases) for t in targets))
		]
	else:
		t_queues = [q for q in ctx.qc.queues if len(q.queue)]

	if not len(t_queues):
		await ctx.reply(f"> {ctx.qc.gt('no players')}")
	else:
		await ctx.reply("\n".join([f"> **{q.name}** ({q.status}) | {q.who}" for q in t_queues]))


async def add_player(ctx: bot.Context, player: Member, queue: str) -> None:
	""" Add a player to a queue """
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (p := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Specified user not found."))
	if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
		raise bot.Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")

	resp = await q.add_member(ctx, p)
	if resp == bot.Qr.Success:
		# Standby detection — the player landed in standby: DM them, and tell the
		# moderator privately (this also used to go public: see /add above).
		if p in getattr(q, 'standby', []):
			spawn(send_dm(p, (
				f"\u23f3 You\u2019ve been added to **standby** for **{q.name}** in {ctx.channel.mention}.\n"
				f"A match is currently in check-in. If anyone fails to ready up, "
				f"you\u2019ll be pulled in to compete for the spot."
			)), name="standby DM")
			await ctx.ignore(embed=ok_embed(f"**{get_nick(p)}** added to standby for **{q.name}**.", title="On Standby"))
			return
		await ctx.qc.update_expire(p)
		await ctx.reply(ctx.qc.topic, view=QueueButtons())
	elif resp == bot.Qr.QueueStarted:
		await ctx.reply(ctx.qc.topic, view=QueueButtons())
	else:
		await ctx.error(f"Got bad queue response: {resp.__name__}.")


async def remove_player(ctx: bot.Context, player: Member, queues: str | None = None) -> None:
	""" Remove a player from queues """
	ctx.check_perms(ctx.Perms.MODERATOR)

	if (p := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Specified user not found."))
	ctx.author = p
	await remove(ctx, queues=queues)


async def start(ctx: bot.Context, queue: str | None = None) -> None:
	""" Manually start a queue """
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
		raise bot.Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")
	await q.start(ctx)
	await ctx.reply(ctx.qc.topic, view=QueueButtons())


async def reset(ctx: bot.Context, queue: str | None = None) -> None:
	""" Reset all or specified queue """
	ctx.check_perms(ctx.Perms.MODERATOR)
	if queue:
		if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
			raise bot.Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")
		await q.reset()
	else:
		for q in ctx.qc.queues:
			await q.reset()
	await ctx.reply(ctx.qc.topic, view=QueueButtons())


async def server(ctx: bot.Context, queue: str) -> None:
	"""Show the configured server for a queue."""
	if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
		raise bot.Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")
	if not q.cfg.server:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Server for **{queue}** is not set.").format(
			queue=q.name
		))
	await ctx.success(q.cfg.server, title=ctx.qc.gt("Server for **{queue}**").format(
		queue=q.name
	))


async def recommend(ctx: bot.Context, format: str, queue: str | None = None) -> None:
	"""Propose a smaller CASUAL game to everyone in a stalled queue.
	The caller must be in the queue; the format must be strictly smaller than
	it; one open proposal per queue, with a cooldown after each one closes."""
	from bot.queues import recommend as rec
	from bot.queues.recommend_rules import format_team_size, allowed_formats

	team_size = format_team_size(format)
	if team_size is None:
		raise bot.Exc.ValueError(ctx.qc.gt("Unknown format."))

	mine = [q for q in ctx.qc.queues if ctx.author in q.queue]
	if not mine:
		raise bot.Exc.ValueError(ctx.qc.gt("You need to be in a queue to recommend a smaller game."))

	if queue:
		wanted = queue.lower()
		target = find(
			lambda q: q.name.lower() == wanted or any(a["alias"].lower() == wanted for a in q.cfg.aliases),
			ctx.qc.queues
		)
		if target is None:
			raise bot.Exc.NotFoundError(ctx.qc.gt("Queue '{queue}' not found.").format(queue=queue))
		if ctx.author not in target.queue:
			raise bot.Exc.ValueError(ctx.qc.gt("You're not in the **{queue}** queue.").format(queue=target.name))
	else:
		target = max(mine, key=lambda q: int(q.cfg.size))  # the biggest queue you're in

	if format not in allowed_formats(int(target.cfg.size)):
		raise bot.Exc.ValueError(ctx.qc.gt(
			"A {format} isn't smaller than the **{queue}** queue ({size} players)."
		).format(format=format, queue=target.name, size=target.cfg.size))

	if rec.active_for(target):
		raise bot.Exc.ValueError(ctx.qc.gt(
			"There's already an open recommendation in **{queue}** — answer it instead."
		).format(queue=target.name))
	left = rec.cooldown_left(target)
	if left:
		raise bot.Exc.ValueError(ctx.qc.gt(
			"Recommendations for **{queue}** are on cooldown for another {seconds}s."
		).format(queue=target.name, seconds=left))

	await rec.propose(ctx, target, format, team_size, ctx.author)
