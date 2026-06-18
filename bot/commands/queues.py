__all__ = [
	'add', 'remove', 'who', 'add_player', 'remove_player', 'promote', 'start', 'split',
	'reset', 'server', 'maps', 'remove_all'
]

import time
from random import choice
from nextcord import Member
from core.utils import error_embed, join_and, find, seconds_to_str, get_nick
import bot


async def add(ctx, queues: str = None):
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

	qr = dict()
	for q in t_queues:
		qr[q] = await q.add_member(ctx, ctx.author)
		if qr[q] == bot.Qr.QueueStarted:
			await ctx.notice(ctx.qc.topic)
			return

	if len(not_allowed := [q for q in qr.keys() if qr[q] == bot.Qr.NotAllowed]):
		await ctx.error(ctx.qc.gt("You are not allowed to add to {queues} queues.".format(
			queues=join_and([f"**{q.name}**" for q in not_allowed])
		)))

	if bot.Qr.Success in qr.values():
		await ctx.qc.update_expire(ctx.author)
		if phrase:
			await ctx.reply(phrase)
		await ctx.notice(ctx.qc.topic)
	else:
		await ctx.ignore(content=ctx.qc.topic, embed=error_embed(ctx.qc.gt("Action had no effect."), title=None))


async def remove(ctx, queues: str = None):
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

		await ctx.notice(ctx.qc.topic)
	else:
		await ctx.ignore(content=ctx.qc.topic, embed=error_embed(ctx.qc.gt("Action had no effect."), title=None))


async def remove_all(ctx, player: Member = None):
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
		chan_str = channel.mention if channel else f"Unknown channel"
		lines.append(f"{chan_str} · **{', '.join(queue_names)}**")

	await ctx.success(
		"\n".join(lines),
		title=f"Removed **{get_nick(target)}** from all queued channels on this server:"
	)


async def who(ctx, queues: str = None):
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


async def add_player(ctx, player: Member, queue: str):
	""" Add a player to a queue """
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (p := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Specified user not found."))
	if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
		raise bot.Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")

	resp = await q.add_member(ctx, p)
	if resp == bot.Qr.Success:
		await ctx.qc.update_expire(p)
		await ctx.reply(ctx.qc.topic)
	elif resp == bot.Qr.QueueStarted:
		await ctx.reply(ctx.qc.topic)
	else:
		await ctx.error(f"Got bad queue response: {resp.__name__}.")


async def remove_player(ctx, player: Member, queues: str = None):
	""" Remove a player from queues """
	ctx.check_perms(ctx.Perms.MODERATOR)

	if (p := await ctx.get_member(player)) is None:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Specified user not found."))
	ctx.author = p
	await remove(ctx, queues=queues)


async def promote(ctx, queue: str = None):
	""" Promote a queue """
	if not queue:
		if (q := next(iter(sorted(
			(i for i in ctx.qc.queues if i.length),
			key=lambda i: i.length, reverse=True
		)), None)) is None:
			raise bot.Exc.NotFoundError(ctx.qc.gt("Nothing to promote."))
	else:
		if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
			raise bot.Exc.NotFoundError(ctx.qc.gt("Specified queue not found."))

	now = int(time.time())
	if ctx.qc.cfg.promotion_delay and ctx.qc.cfg.promotion_delay+ctx.qc.last_promote > now:
		raise bot.Exc.PermissionError(ctx.qc.gt("You're promoting too often, please wait `{delay}` until next promote.".format(
			delay=seconds_to_str((ctx.qc.cfg.promotion_delay+ctx.qc.last_promote)-now)
		)))

	await q.promote(ctx)
	ctx.qc.last_promote = now


async def start(ctx, queue: str = None):
	""" Manually start a queue """
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
		raise bot.Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")
	await q.start(ctx)
	await ctx.reply(ctx.qc.topic)


async def split(ctx, queue: str, group_size: int = None, sort_by_rating: bool = False):
	""" Split queue players into X separate matches """
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
		raise bot.Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")
	await q.split(ctx, group_size=group_size, sort_by_rating=sort_by_rating)
	await ctx.reply(ctx.qc.topic)


async def reset(ctx, queue: str = None):
	""" Reset all or specified queue """
	ctx.check_perms(ctx.Perms.MODERATOR)
	if queue:
		if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
			raise bot.Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")
		await q.reset()
	else:
		for q in ctx.qc.queues:
			await q.reset()
	await ctx.reply(ctx.qc.topic)


async def server(ctx, queue: str):
	if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
		raise bot.Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")
	if not q.cfg.server:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Server for **{queue}** is not set.").format(
			queue=q.name
		))
	await ctx.success(q.cfg.server, title=ctx.qc.gt("Server for **{queue}**").format(
		queue=q.name
	))


async def maps(ctx, queue: str, one: bool = False):
	if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
		raise bot.Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")
	if not len(q.cfg.maps):
		raise bot.Exc.NotFoundError(ctx.qc.gt("No maps is set for **{queue}**.").format(
			queue=q.name
		))

	if one:
		await ctx.success(f"`{choice(q.cfg.maps)['name']}`")
	else:
		await ctx.success(
			", ".join((f"`{i['name']}`" for i in q.cfg.maps)),
			title=ctx.qc.gt("Maps for **{queue}**").format(queue=q.name)
		)
