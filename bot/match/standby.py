# -*- coding: utf-8 -*-
"""
Standby pool and check-in race logic.

Lives in its own module so check_in.py stays focused on the basic
ready/not-ready flow. The standby system has its own moving parts:

  - pull_standby_into_match()     — at 2/3 of check-in, add standby
                                    players as additional candidates
  - finalize_race_results()       — when the first `queue_size` players
                                    have readied, trim the roster and
                                    send losers back to standby

Each function takes the check-in handler as `ci` so it can access ready
state and post messages. They're called from check_in.py's think()/refresh().
"""

import asyncio
from core.console import log
from core.utils import join_and
import bot


# Roster size of the standby cushion above check-in. Standby is unlimited
# for additions, but only the first N can race for spots.
_MAX_STANDBY_PULL = 6


async def pull_standby_into_match(ci, ctx):
	"""At 2/3 of check-in time, add standby players as additional candidates.

	Nobody is kicked out. The standby players join the ready race —
	whichever players (original or standby) hit ✅ first claim the slots
	when finalize_race_results() runs.

	Caller (check_in.think) should set `ci.standby_pulled = True` BEFORE
	calling this so we don't re-pull on every tick.
	"""
	match = ci.m
	not_ready = [
		m for m in match.players
		if m not in ci.ready_players and m not in ci.discarded_players
	]
	standby_pool = list(getattr(match.queue, 'standby', None) or [])
	if not not_ready or not standby_pool:
		log.info(
			f"[standby] match {match.id}: nothing to pull "
			f"(not_ready={len(not_ready)}, standby={len(standby_pool)})"
		)
		return

	added = []
	for in_player in standby_pool[:_MAX_STANDBY_PULL]:
		if in_player in match.players:
			continue
		match.players.append(in_player)
		try:
			match.queue.standby.remove(in_player)
		except ValueError:
			pass
		added.append(in_player)

		# Make sure ratings dict has the new player so embeds don't KeyError
		if in_player.id not in match.ratings:
			try:
				rows = await match.qc.rating.get_players([in_player.id])
				match.ratings[in_player.id] = (rows[0]['rating'] if rows else 1500) or 1500
			except Exception as exc:
				log.error(f"[standby] rating fetch failed for {in_player.id}: {exc}")
				match.ratings.setdefault(in_player.id, 1500)

		# Honour auto-ready preference for standby joiners
		if in_player.id in bot.auto_ready:
			ci.ready_players.add(in_player)
			if in_player not in ci.ready_order:
				ci.ready_order.append(in_player)

	if not added:
		return

	added_mentions = join_and([m.mention for m in added])
	not_ready_mentions = join_and([m.mention for m in not_ready])

	# Channel ping (plain content forces mobile/desktop notification)
	try:
		await ctx.channel.send(content=added_mentions)
	except Exception:
		pass

	jump = ci.message.jump_url if ci.message else None
	lines = [
		f"\U0001f6a8 **STANDBY PULLED IN!** \U0001f6a8",
		f"{added_mentions} \u2014 you have a chance to claim a spot!",
		f"You\u2019re now competing with {not_ready_mentions} for the remaining slots.",
		f"**First to react {ci.READY_EMOJI} on the check-in message wins their spot.**",
	]
	if jump:
		lines.append(f"\U0001f449 Jump to check-in: {jump}")
	await ctx.notice("\n".join(lines))

	# DM each pulled-in player so they're notified even if tabbed away.
	dm_text = match.gt(
		"\U0001f6a8 You\u2019ve been pulled in from standby for **{queue}** @ {channel}!\n"
		"Hurry \u2014 react {ready} on the check-in message to claim a spot "
		"before time runs out."
	).format(
		queue=match.queue.name,
		channel=ctx.channel.mention,
		ready=ci.READY_EMOJI,
	)
	for m in added:
		async def _dm(member=m, text=dm_text):
			try:
				await member.send(text)
			except Exception:
				pass
		asyncio.ensure_future(_dm())

	await ci.refresh(ctx)


async def finalize_race_results(ci, ctx):
	"""Called when at least `queue_size` players have readied.

	Trims the roster down to `queue_size` based on who readied FIRST
	(using ci.ready_order). Anyone left over (unready originals OR
	late-ready standby joiners) is returned to standby with no penalty,
	then ci.finish(ctx) is invoked to advance the match.
	"""
	match = ci.m
	queue_size = match.queue.cfg.size

	# Keep the first N players to ready up, in actual ready order.
	kept = [p for p in ci.ready_order if p in ci.ready_players][:queue_size]
	# Fallback: if ready_order missed someone, fill from ready_players set.
	if len(kept) < queue_size:
		extras = [p for p in ci.ready_players if p not in kept]
		kept.extend(extras[:queue_size - len(kept)])

	losers = [p for p in match.players if p not in kept]

	# Losers go back to standby — they did nothing wrong.
	for p in losers:
		if p not in match.queue.standby:
			match.queue.standby.append(p)

	match.players = kept
	ci.ready_players = set(kept)
	# Rebuild ready_order to match the trimmed roster, preserving relative order.
	ci.ready_order = [p for p in ci.ready_order if p in ci.ready_players]

	# Clear offline-immunity for players who made the final roster, mirroring
	# what queue_channel.queue_started() does for normally-filled matches.
	# Without this, a player pulled in from standby keeps allow_offline set
	# after their match, because the standby path never goes through
	# queue_started. Losers are intentionally NOT cleared — they're back on
	# standby waiting, so their immunity should persist like anyone else's.
	for p in kept:
		if p.id in bot.allow_offline:
			bot.allow_offline.remove(p.id)

	if losers:
		await ctx.notice(match.gt(
			"{losers} didn\u2019t make the cut and have been returned to standby."
		).format(losers=join_and([m.mention for m in losers])))

	await ci.finish(ctx)
