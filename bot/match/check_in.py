# -*- coding: utf-8 -*-
import random
import bot
from nextcord.errors import DiscordException

from core.utils import join_and
from core.console import log  # noqa: F401
from bot.stats.checkin_tracker import record_violation


class CheckIn:

	READY_EMOJI = "☑"
	NOT_READY_EMOJI = "⛔"
	INT_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6⃣", "7⃣", "8⃣", "9⃣"]

	def __init__(self, match, timeout):
		self.m = match
		self.timeout = timeout
		self.allow_discard = self.m.cfg['check_in_discard']
		self.discard_immediately = self.m.cfg['check_in_discard_immediately']
		self.ready_players = set()
		self.discarded_players = set()
		self.message = None
		self.standby_pulled = False  # True once standby fill has fired
		self._check_in_started_at = None  # frame_time when check-in actually started ticking
		self.original_player_ids = set()  # populated in start(); only these get violations

		for p in (p for p in self.m.players if p.id in bot.auto_ready.keys()):
			self.ready_players.add(p)

		if len(self.m.cfg['maps']) > 1 and self.m.cfg['vote_maps']:
			self.maps = self.m.random_maps(self.m.cfg['maps'], self.m.cfg['vote_maps'], self.m.queue.last_maps)
			self.map_votes = [set() for i in self.maps]
		else:
			self.maps = []
			self.map_votes = []

		if self.timeout:
			self.m.states.append(self.m.CHECK_IN)

	async def think(self, frame_time):
		# ── Standby fill at 2/3 of the way through check-in ──────────────────
		# Use the check-in's own clock so the trigger is independent of
		# self.m.start_time (set at match creation, before INIT \u2192 CHECK_IN).
		if not hasattr(self, '_check_in_started_at') or self._check_in_started_at is None:
			self._check_in_started_at = frame_time

		pull_at = self._check_in_started_at + int(self.timeout * 2 / 3)

		if not self.standby_pulled and frame_time > pull_at:
			standby_pool = getattr(self.m.queue, 'standby', None) or []
			if standby_pool and self.allow_discard:
				log.info(
					f"[think] match {self.m.id}: pull_at reached "
					f"(frame={int(frame_time)}, pull_at={int(pull_at)}), "
					f"standby={len(standby_pool)}"
				)
				await self.pull_standby(bot.SystemContext(self.m.qc))
			elif not standby_pool:
				# No standby waiting — mark as already pulled so we don\'t spam this branch
				self.standby_pulled = True

		if frame_time > (self._check_in_started_at or self.m.start_time) + self.timeout:
			ctx = bot.SystemContext(self.m.qc)
			if self.allow_discard:
				await self.abort_timeout(ctx)
			else:
				await self.finish(ctx)

	async def start(self, ctx):
		# Snapshot originals so standby joiners aren\'t penalized for missing check-in.
		self.original_player_ids = {p.id for p in self.m.players}
		text = f"!spawn message {self.m.id}"
		self.message = await ctx.channel.send(text)

		emojis = [self.READY_EMOJI, '🔸', self.NOT_READY_EMOJI] if self.allow_discard else [self.READY_EMOJI]
		emojis += [self.INT_EMOJIS[n] for n in range(len(self.maps))]
		try:
			for emoji in emojis:
				await self.message.add_reaction(emoji)
		except DiscordException:
			pass
		bot.waiting_reactions[self.message.id] = self.process_reaction
		await self.refresh(ctx)

	async def refresh(self, ctx):
		not_ready = list(filter(lambda m: m not in self.ready_players, self.m.players))

		if len(self.discarded_players) and len(self.discarded_players) == len(not_ready):
			if self.message:
				bot.waiting_reactions.pop(self.message.id, None)
				try:
					await self.message.delete()
				except DiscordException:
					pass

			# All not-ready players discarded — record violations for each
			for member in self.discarded_players:
				try:
					await record_violation(ctx.channel, member, 'aborted')
				except Exception as e:
					log.error(f"checkin_tracker error (refresh/abort): {e}")

			await ctx.notice('\n'.join((
				self.m.gt("{member} has aborted the check-in.").format(
					member=', '.join([m.mention for m in self.discarded_players])
				),
				self.m.gt("Reverting {queue} to the gathering stage...").format(queue=f"**{self.m.queue.name}**")
			)))

			bot.active_matches.remove(self.m)
			await self.m.queue.revert(
				ctx,
				list(self.discarded_players),
				[m for m in self.m.players if m not in self.discarded_players]
			)
			return

		# ── Race-to-ready finish condition ───────────────────────────────────
		# Once queue.cfg.size players have readied, the match is full and starts
		# immediately. Any leftover candidates go back to standby.
		queue_size = self.m.queue.cfg.size
		if len(self.ready_players) >= queue_size:
			await self.finish_race(ctx)
			return

		if len(not_ready):
			try:
				await self.message.edit(content=None, embed=self.m.embeds.check_in(not_ready))
			except DiscordException:
				pass
		else:
			await self.finish(ctx)

	async def finish_race(self, ctx):
		"""Race ended — queue.cfg.size players readied first. Trim the roster.

		The ready players become the match roster (preserving original order
		where possible). Anyone who didn\'t make it goes back to standby.
		"""
		queue_size = self.m.queue.cfg.size

		# Keep the first `queue_size` ready players (in original join order)
		kept = [p for p in self.m.players if p in self.ready_players][:queue_size]
		losers = [p for p in self.m.players if p not in kept]

		# Send losers back to standby (they did nothing wrong)
		for p in losers:
			if p not in self.m.queue.standby:
				self.m.queue.standby.append(p)

		self.m.players = kept
		self.ready_players = set(kept)

		if losers:
			await ctx.notice(self.m.gt(
				"{losers} didn\'t make the cut and have been returned to standby."
			).format(losers=join_and([m.mention for m in losers])))

		await self.finish(ctx)

	async def finish(self, ctx):
		bot.waiting_reactions.pop(self.message.id)
		self.ready_players = set()
		if len(self.maps):
			order = list(range(len(self.maps)))
			random.shuffle(order)
			order.sort(key=lambda n: len(self.map_votes[n]), reverse=True)
			self.m.maps = [self.maps[n] for n in order[:self.m.cfg['map_count']]]
		await self.message.delete()

		for p in (p for p in self.m.players if p.id in bot.auto_ready.keys()):
			bot.auto_ready.pop(p.id)

		await self.m.next_state(ctx)

	async def process_reaction(self, reaction, user, remove=False):
		if self.m.state != self.m.CHECK_IN or user not in self.m.players:
			return

		if str(reaction) in self.INT_EMOJIS:
			idx = self.INT_EMOJIS.index(str(reaction))
			if idx <= len(self.maps):
				if remove:
					self.map_votes[idx].discard(user.id)
					self.ready_players.discard(user)
				else:
					self.map_votes[idx].add(user.id)
					self.discarded_players.discard(user)
					self.ready_players.add(user)
				await self.refresh(bot.SystemContext(self.m.queue.qc))

		elif str(reaction) == self.READY_EMOJI:
			if remove:
				self.ready_players.discard(user)
			else:
				self.discarded_players.discard(user)
				self.ready_players.add(user)
			await self.refresh(bot.SystemContext(self.m.queue.qc))

		elif str(reaction) == self.NOT_READY_EMOJI and self.allow_discard:
			if self.discard_immediately:
				return await self.abort_member(bot.SystemContext(self.m.queue.qc), user)
			return await self.discard_member(bot.SystemContext(self.m.queue.qc), user)

	async def set_ready(self, ctx, member, ready):
		if self.m.state != self.m.CHECK_IN:
			raise bot.Exc.MatchStateError(self.m.gt("The match is not on the check-in stage."))
		if ready:
			self.ready_players.add(member)
			self.discarded_players.discard(member)
			await self.refresh(ctx)
		elif not ready:
			if not self.allow_discard:
				raise bot.Exc.PermissionError(self.m.gt("Discarding check-in is not allowed."))
			if self.discard_immediately:
				return await self.abort_member(ctx, member)
			return await self.discard_member(ctx, member)

	async def discard_member(self, ctx, member):
		self.ready_players.discard(member)
		self.discarded_players.add(member)
		await self.refresh(ctx)

	async def abort_member(self, ctx, member):
		bot.waiting_reactions.pop(self.message.id)
		await self.message.delete()

		# Record violation before reverting
		try:
			await record_violation(ctx.channel, member, 'aborted')
		except Exception as e:
			log.error(f"checkin_tracker error (abort_member): {e}")

		await ctx.notice("\n".join((
			self.m.gt("{member} has aborted the check-in.").format(member=f"<@{member.id}>"),
			self.m.gt("Reverting {queue} to the gathering stage...").format(queue=f"**{self.m.queue.name}**")
		)))

		bot.active_matches.remove(self.m)
		await self.m.queue.revert(ctx, [member], [m for m in self.m.players if m != member])

	async def pull_standby(self, ctx):
		"""At 2/3 of check-in: add standby as ADDITIONAL candidates.

		Nobody is kicked out. The 2 standby players now compete with the
		unready originals for the open slots — first to ready up wins.
		When `queue.cfg.size` players have readied, finish() fires and the
		rest go back to standby.
		"""
		self.standby_pulled = True

		not_ready = [m for m in self.m.players if m not in self.ready_players and m not in self.discarded_players]
		standby = list(getattr(self.m.queue, 'standby', []) or [])

		log.info(
			f"[pull_standby] match {self.m.id}: "
			f"not_ready={len(not_ready)}, standby={len(standby)}, "
			f"queue.standby={[m.display_name for m in standby]}"
		)

		if not not_ready or not standby:
			log.info(f"[pull_standby] match {self.m.id}: nothing to do, returning")
			return

		# Add standby players as additional candidates (don\'t remove anyone yet)
		added = []
		for in_player in standby:
			if in_player in self.m.players:
				continue
			self.m.players.append(in_player)
			self.m.queue.standby.remove(in_player)
			added.append(in_player)

			# Make sure ratings dict has the new player
			if in_player.id not in self.m.ratings:
				try:
					rows = await self.m.qc.rating.get_players([in_player.id])
					if rows:
						self.m.ratings[in_player.id] = rows[0]['rating']
				except Exception as e:
					log.error(f"pull_standby rating fetch failed: {e}")
					self.m.ratings.setdefault(in_player.id, 1500)

			# Auto-readies still apply for late arrivals
			if in_player.id in bot.auto_ready:
				self.ready_players.add(in_player)

		if not added:
			return

		not_ready_mentions = join_and([m.mention for m in not_ready])
		added_mentions    = join_and([m.mention for m in added])

		# Build a jump URL to the check-in message so standby players can react directly
		jump_url = self.message.jump_url if self.message else None

		lines = [
			f"\U0001f6a8 **STANDBY PULLED IN!** \U0001f6a8",
			f"{added_mentions} \u2014 you have a chance to claim a spot!",
			f"You\u2019re now competing with {not_ready_mentions} for the remaining slots.",
			f"**First to react {self.READY_EMOJI} on the check-in message wins their spot.**",
		]
		if jump_url:
			lines.append(f"\U0001f449 Jump to check-in: {jump_url}")

		# Send the alert with the standby players in the content field so they
		# get a guaranteed mobile/desktop ping (embeds don\'t always notify).
		try:
			await ctx.channel.send(
				content=added_mentions,  # forces a ping
				embed=None
			)
		except Exception:
			pass

		await ctx.notice("\n".join(lines))

		# DM each standby player so they get notified even if they\'re not watching the channel
		dm_text = self.m.gt(
			"\U0001f6a8 You\u2019ve been pulled in from standby for **{queue}** @ {channel}!\n"
			"Hurry \u2014 react {ready} on the check-in message to claim a spot before time runs out."
		).format(
			queue=self.m.queue.name,
			channel=ctx.channel.mention,
			ready=self.READY_EMOJI,
		)
		for member in added:
			try:
				await member.send(dm_text)
			except Exception:
				pass  # DMs disabled or blocked — channel ping is the fallback

		await self.refresh(ctx)

	async def abort_timeout(self, ctx):
		not_ready = [m for m in self.m.players if m not in self.ready_players]
		if self.message:
			bot.waiting_reactions.pop(self.message.id, None)
			try:
				await self.message.delete()
			except DiscordException:
				pass

		# Record 'missed' violation ONLY for original players who didn\'t check in.
		# Standby joiners pulled in late shouldn\'t be penalized.
		# If original set is empty (e.g. state restored mid-checkin), fall back
		# to penalizing everyone like the old behaviour.
		originals = self.original_player_ids if self.original_player_ids else {p.id for p in self.m.players}
		for member in not_ready:
			if member.id not in originals:
				log.info(
					f"[abort_timeout] skipping violation for standby joiner {member.display_name}"
				)
				continue
			try:
				await record_violation(ctx.channel, member, 'missed')
			except Exception as e:
				log.error(f"checkin_tracker error (abort_timeout): {e}")

		bot.active_matches.remove(self.m)

		await ctx.notice("\n".join((
			self.m.gt("{members} was not ready in time.").format(members=join_and([m.mention for m in not_ready])),
			self.m.gt("Reverting {queue} to the gathering stage...").format(queue=f"**{self.m.queue.name}**")
		)))

		# If we expanded past queue.cfg.size via standby, the ready set might be
		# bigger than the queue size. Trim it down using join order so revert()
		# doesn\'t try to start a match with too many players.
		queue_size = self.m.queue.cfg.size
		ready_list = [p for p in self.m.players if p in self.ready_players][:queue_size]
		extra_ready = [p for p in self.m.players if p in self.ready_players][queue_size:]
		# Anyone ready but over the cap goes back to standby (they didn\'t fail)
		for p in extra_ready:
			if p not in self.m.queue.standby:
				self.m.queue.standby.append(p)

		await self.m.queue.revert(ctx, not_ready, ready_list)
