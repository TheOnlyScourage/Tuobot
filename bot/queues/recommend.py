# -*- coding: utf-8 -*-
"""/recommend — when the big queue stalls, a queued player proposes a smaller
CASUAL game to everyone else in that queue.

Flow: the proposal is an embed with Accept/Deny buttons that only players
currently in the queue can answer; the recommender counts as the first
accept. The moment `2 * team_size` players have accepted, the earliest
accepters who are still queued start a casual match via
PickupQueue.start(players=..., team_size=..., casual=True): instant random
"Team A"/"Team B", no check-in, no draft, no report — the match posts its
start embed and finishes on the spot, and NOBODY leaves the queue (accepters
included), so the real queue can still pop underneath them. Casual means no
ratings, no history rows, no house points, no captain-streak bookkeeping.

One open recommendation per queue at a time; a normal queue start cancels
it; a short cooldown follows every close. All state is in-memory and short-
lived (constants.RECOMMEND_WINDOW), so nothing persists across restarts.
"""
from __future__ import annotations

import time
import traceback
from typing import TYPE_CHECKING

import nextcord
from nextcord import Embed, Colour

from core.console import log
from core.utils import get_nick

from bot.constants import (
	RECOMMEND_WINDOW, RECOMMEND_COOLDOWN, MATCH_COLOUR_DRAFT, MATCH_COLOUR_LIVE,
)
from bot.queues.recommend_rules import players_needed, pick_roster

if TYPE_CHECKING:
	import bot
	from nextcord import Member

_CLOSED_COLOUR = 0x95a5a6  # grey — expired / withdrawn / cancelled

_active: dict[tuple[int, str], Recommendation] = {}
_cooldown_until: dict[tuple[int, str], float] = {}


def _key(queue: bot.PickupQueue) -> tuple[int, str]:
	return (queue.qc.id, queue.name)


def active_for(queue: bot.PickupQueue) -> Recommendation | None:
	return _active.get(_key(queue))


def cooldown_left(queue: bot.PickupQueue) -> int:
	return max(0, int(_cooldown_until.get(_key(queue), 0) - time.time()))


async def propose(ctx: bot.Context, queue: bot.PickupQueue, label: str, team_size: int, recommender: Member) -> None:
	"""Post a recommendation for `queue`, pinging the other queued players."""
	rec = Recommendation(queue, label, team_size, recommender)
	_active[_key(queue)] = rec
	others = [m.mention for m in queue.queue if m.id != recommender.id]
	await ctx.reply(content=" ".join(others) or None, embed=rec.embed(), view=rec.view)
	await rec.view.bind(ctx)


async def cancel_for(queue: bot.PickupQueue, reason: str) -> None:
	"""Close any open recommendation for `queue` (e.g. the queue filled up)."""
	rec = active_for(queue)
	if rec is not None and not rec.done:
		await rec.close(f"Cancelled — {reason}.", _CLOSED_COLOUR)


class Recommendation:
	"""One open proposal: who accepted (in order), who denied, and the buttons."""

	def __init__(self, queue: bot.PickupQueue, label: str, team_size: int, recommender: Member):
		self.queue = queue
		self.label = label
		self.team_size = team_size
		self.needed = players_needed(team_size)
		self.recommender = recommender
		self.accepted: list[Member] = [recommender]  # first-come order
		self.denied: set[int] = set()
		self.expires_at = int(time.time()) + RECOMMEND_WINDOW
		self.done = False
		self.view = RecommendView(self)

	# ── state helpers ────────────────────────────────────────────────────────

	def queued_ids(self) -> set[int]:
		return {m.id for m in self.queue.queue}

	def prune(self) -> None:
		"""Drop accepters who left the queue since they pressed."""
		queued = self.queued_ids()
		self.accepted = [m for m in self.accepted if m.id in queued]

	def roster(self) -> list[Member] | None:
		ids = pick_roster([m.id for m in self.accepted], self.queued_ids(), self.needed)
		if ids is None:
			return None
		by_id = {m.id: m for m in self.accepted}
		return [by_id[i] for i in ids]

	# ── rendering ────────────────────────────────────────────────────────────

	def embed(self, status: str | None = None, colour: int = MATCH_COLOUR_DRAFT) -> Embed:
		names = ", ".join(f"`{get_nick(m)}`" for m in self.accepted) or "—"
		lines = [
			f"**{self.queue.name}** isn't filling — play a **casual {self.label}** "
			f"({self.needed} players) right now instead?",
			"No MMR, no records — instant random teams, and everyone **stays in the queue**.",
			"",
			f"✅ **{len(self.accepted)}/{self.needed}** accepted · ❌ {len(self.denied)} · closes <t:{self.expires_at}:R>",
			f"**In:** {names}",
		]
		if status:
			lines += ["", f"**{status}**"]
		e = Embed(
			colour=Colour(colour),
			title=f"🤝 {get_nick(self.recommender)} recommends a {self.label}",
			description="\n".join(lines),
		)
		e.set_footer(text="Only players in the queue can answer. The recommender can withdraw with ❌.")
		return e

	# ── lifecycle ────────────────────────────────────────────────────────────

	async def close(self, status: str, colour: int) -> None:
		"""Finalize: unregister, arm the cooldown, freeze the buttons, edit once."""
		self.done = True
		_active.pop(_key(self.queue), None)
		_cooldown_until[_key(self.queue)] = time.time() + RECOMMEND_COOLDOWN
		for child in self.view.children:
			child.disabled = True
		self.view.stop()
		message = getattr(self.view, 'message', None)
		if message is not None:
			try:
				await message.edit(embed=self.embed(status, colour), view=self.view)
			except Exception as e:
				log.error(f"[recommend] final edit failed: {e}")

	async def try_start(self) -> bool:
		"""Start the casual match if enough accepters are still queued."""
		roster = self.roster()
		if roster is None:
			return False
		names = ", ".join(f"`{get_nick(m)}`" for m in roster)
		await self.close(f"Starting a casual {self.label} with {names}!", MATCH_COLOUR_LIVE)
		import bot
		ctx = bot.SystemContext(self.queue.qc)
		try:
			await self.queue.start(ctx, players=roster, team_size=self.team_size, casual=True)
		except Exception as e:
			# Best-effort by design rule: never let a party trick take down
			# the button handler; log loudly with the roster for forensics.
			log.error(
				f"[recommend] casual {self.label} start failed for {[m.id for m in roster]}: {e}\n"
				f"{traceback.format_exc()}"
			)
		return True


class RecommendView(nextcord.ui.View):
	"""Accept/Deny buttons, gated to players currently in the queue."""

	def __init__(self, rec: Recommendation):
		super().__init__(timeout=RECOMMEND_WINDOW)
		self.rec = rec
		self.message = None  # set by bind() so on_timeout can edit

	async def bind(self, ctx: bot.Context) -> None:
		"""Grab the sent message (slash contexts only), LeaderboardView-style."""
		interaction = getattr(ctx, 'interaction', None)
		if interaction is None:
			return
		try:
			self.message = await interaction.original_message()
		except Exception:
			pass

	async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
		if self.rec.done:
			await interaction.response.send_message("This recommendation is closed.", ephemeral=True)
			return False
		if interaction.user not in self.rec.queue.queue:
			await interaction.response.send_message(
				f"Only players in the **{self.rec.queue.name}** queue can answer this.", ephemeral=True
			)
			return False
		return True

	@nextcord.ui.button(label="Accept", emoji="✅", style=nextcord.ButtonStyle.success)
	async def accept(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
		rec = self.rec
		rec.prune()
		member = interaction.user
		if member in rec.accepted:
			await interaction.response.send_message("You already accepted.", ephemeral=True)
			return
		rec.denied.discard(member.id)
		rec.accepted.append(member)
		await interaction.response.edit_message(embed=rec.embed(), view=self)
		await rec.try_start()

	@nextcord.ui.button(label="Deny", emoji="❌", style=nextcord.ButtonStyle.danger)
	async def deny(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
		rec = self.rec
		member = interaction.user
		if member.id == rec.recommender.id:
			await interaction.response.defer()
			await rec.close("Withdrawn by the recommender.", _CLOSED_COLOUR)
			return
		rec.accepted = [m for m in rec.accepted if m.id != member.id]
		rec.denied.add(member.id)
		await interaction.response.edit_message(embed=rec.embed(), view=self)

	async def on_timeout(self):
		rec = self.rec
		if not rec.done:
			await rec.close(f"Expired — {len(rec.accepted)}/{rec.needed} accepted.", _CLOSED_COLOUR)
