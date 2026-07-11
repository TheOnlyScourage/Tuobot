# -*- coding: utf-8 -*-
"""Check-in stage controller for a match: the ready/not-ready reaction flow,
race-to-ready finishing, standby fill, and abort/timeout handling.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import bot
from nextcord.errors import DiscordException

from core.utils import join_and
from core.console import log
from bot.stats.checkin_tracker import record_violation

if TYPE_CHECKING:
	from nextcord import Member


class CheckIn:
	"""Runs the check-in stage: players react to ready up or abort. Fills empty
	slots from standby partway through, finishes early once enough players ready
	(race-to-ready), or aborts and reverts the queue on timeout. All state lives
	on the shared Match object (self.m).
	"""

	READY_EMOJI = "✅"
	NOT_READY_EMOJI = "⛔"

	def __init__(self, match: bot.Match, timeout: int):
		"""Set up check-in state from config: pre-ready any auto-ready players and
		(when a timeout is set) append the CHECK_IN state to the match's state
		sequence."""
		self.m = match
		self.timeout = timeout
		self.allow_discard = self.m.cfg['check_in_discard']
		self.discard_immediately = self.m.cfg['check_in_discard_immediately']
		self.ready_players = set()
		self.ready_order = []  # Members in the order they readied — used by finish_race tie-break
		self.discarded_players = set()
		self.message = None
		self.standby_pulled = False  # True once standby fill has fired
		self._check_in_started_at = None  # frame_time when check-in actually started ticking
		self.original_player_ids = set()  # populated in start(); only these get violations

		for p in (p for p in self.m.players if p.id in bot.auto_ready.keys()):
			self.ready_players.add(p)

		if self.timeout:
			self.m.states.append(self.m.CHECK_IN)

	async def think(self, frame_time: float) -> None:
		"""Per-frame tick driven by the match loop: fires standby fill at 2/3 of
		the timeout, then aborts (or finishes) once the full timeout elapses."""
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
				# No standby waiting — mark as already pulled so we don't spam this branch
				self.standby_pulled = True

		if frame_time > (self._check_in_started_at or self.m.start_time) + self.timeout:
			ctx = bot.SystemContext(self.m.qc)
			if self.allow_discard:
				await self.abort_timeout(ctx)
			else:
				await self.finish(ctx)

	async def start(self, ctx: bot.Context) -> None:
		"""Post the check-in message, add the ready/abort reactions, register the
		reaction handler, and render the board."""
		# Snapshot originals so standby joiners aren't penalized for missing check-in.
		self.original_player_ids = {p.id for p in self.m.players}
		text = f"!spawn message {self.m.id}"
		self.message = await ctx.channel.send(text)

		emojis = [self.READY_EMOJI, '🔸', self.NOT_READY_EMOJI] if self.allow_discard else [self.READY_EMOJI]
		try:
			for emoji in emojis:
				await self.message.add_reaction(emoji)
		except DiscordException:
			pass
		bot.waiting_reactions[self.message.id] = self.process_reaction
		await self.refresh(ctx)

	async def refresh(self, ctx: bot.Context) -> None:
		"""Re-render the board and drive stage transitions: revert the queue if
		every not-ready player has been discarded, finish early once enough
		players have readied (race-to-ready), otherwise update the message or
		finish when everyone is ready."""
		not_ready = list(filter(lambda m: m not in self.ready_players, self.m.players))

		if len(self.discarded_players) and len(self.discarded_players) == len(not_ready):
			if self.message:
				bot.waiting_reactions.pop(self.message.id, None)
				try:
					await self.message.delete()
				except DiscordException:
					pass

			# All not-ready players discarded — record violations only on ranked.
			# Unranked queues are casual; missing/aborting check-in shouldn't penalize.
			if self.m.ranked:
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

	async def finish_race(self, ctx: bot.Context) -> None:
		"""Delegate to bot.match.standby.finalize_race_results."""
		from bot.match.standby import finalize_race_results
		await finalize_race_results(self, ctx)

	async def finish(self, ctx: bot.Context) -> None:
		"""Finalize check-in: clear ready state, clean up auto-ready, and advance
		state."""
		bot.waiting_reactions.pop(self.message.id)
		self.ready_players = set()
		self.ready_order = []
		await self.message.delete()

		for p in (p for p in self.m.players if p.id in bot.auto_ready.keys()):
			bot.auto_ready.pop(p.id)

		await self.m.next_state(ctx)

	async def process_reaction(self, reaction, user: Member, remove: bool = False) -> None:
		"""Handle a ready or abort reaction (add or remove). The ready reaction
		readies; the abort reaction discards or immediately aborts. Ignored
		unless in CHECK_IN and the user is a player."""
		if self.m.state != self.m.CHECK_IN or user not in self.m.players:
			return

		if str(reaction) == self.READY_EMOJI:
			if remove:
				self.ready_players.discard(user)
			else:
				self.discarded_players.discard(user)
				self.ready_players.add(user)
				if user not in self.ready_order:
					self.ready_order.append(user)
			await self.refresh(bot.SystemContext(self.m.queue.qc))

		elif str(reaction) == self.NOT_READY_EMOJI and self.allow_discard:
			if self.discard_immediately:
				return await self.abort_member(bot.SystemContext(self.m.queue.qc), user)
			return await self.discard_member(bot.SystemContext(self.m.queue.qc), user)

	async def set_ready(self, ctx: bot.Context, member: Member, ready: bool) -> None:
		"""Command-driven ready toggle: mark member ready, or (if allowed)
		discard / immediately abort them."""
		if self.m.state != self.m.CHECK_IN:
			raise bot.Exc.MatchStateError(self.m.gt("The match is not on the check-in stage."))
		if ready:
			self.ready_players.add(member)
			if member not in self.ready_order:
				self.ready_order.append(member)
			self.discarded_players.discard(member)
			await self.refresh(ctx)
		elif not ready:
			if not self.allow_discard:
				raise bot.Exc.PermissionError(self.m.gt("Discarding check-in is not allowed."))
			if self.discard_immediately:
				return await self.abort_member(ctx, member)
			return await self.discard_member(ctx, member)

	async def discard_member(self, ctx: bot.Context, member: Member) -> None:
		"""Mark member as discarded (not ready) and refresh the board."""
		self.ready_players.discard(member)
		if member in self.ready_order:
			self.ready_order.remove(member)
		self.discarded_players.add(member)
		await self.refresh(ctx)

	async def abort_member(self, ctx: bot.Context, member: Member) -> None:
		"""Abort the check-in for one member: delete the message, record a
		violation (ranked only), and revert the queue to gathering."""
		bot.waiting_reactions.pop(self.message.id)
		await self.message.delete()

		# Record violation before reverting — ranked queues only.
		if self.m.ranked:
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

	async def pull_standby(self, ctx: bot.Context) -> None:
		"""Delegate to bot.match.standby.pull_standby_into_match — kept as a
		thin shim so existing callers (think()) don't need to change."""
		self.standby_pulled = True
		from bot.match.standby import pull_standby_into_match
		await pull_standby_into_match(self, ctx)

	async def abort_timeout(self, ctx: bot.Context) -> None:
		"""Handle check-in timeout: record 'missed' violations for original
		ranked players, revert the queue, and return any over-cap ready players
		to standby."""
		not_ready = [m for m in self.m.players if m not in self.ready_players]
		if self.message:
			bot.waiting_reactions.pop(self.message.id, None)
			try:
				await self.message.delete()
			except DiscordException:
				pass

		# Record 'missed' violation ONLY for original players on RANKED queues.
		# - Unranked queues: no penalty for missing check-in (casual play)
		# - Standby joiners pulled in late: also no penalty (they did nothing wrong)
		# Fallback if original set is empty (state restored mid-checkin): treat
		# everyone as original so behaviour matches pre-standby logic.
		if self.m.ranked:
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
		else:
			log.info(f"[abort_timeout] match {self.m.id} unranked \u2014 no violations recorded")

		bot.active_matches.remove(self.m)

		await ctx.notice("\n".join((
			self.m.gt("{members} was not ready in time.").format(members=join_and([m.mention for m in not_ready])),
			self.m.gt("Reverting {queue} to the gathering stage...").format(queue=f"**{self.m.queue.name}**")
		)))

		# If we expanded past queue.cfg.size via standby, the ready set might be
		# bigger than the queue size. Trim it down using join order so revert()
		# doesn't try to start a match with too many players.
		queue_size = self.m.queue.cfg.size
		ready_list = [p for p in self.m.players if p in self.ready_players][:queue_size]
		extra_ready = [p for p in self.m.players if p in self.ready_players][queue_size:]
		# Anyone ready but over the cap goes back to standby (they didn't fail)
		for p in extra_ready:
			if p not in self.m.queue.standby:
				self.m.queue.standby.append(p)

		await self.m.queue.revert(ctx, not_ready, ready_list)
