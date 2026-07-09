# -*- coding: utf-8 -*-
"""Draft/pick stage controller for a match."""
from __future__ import annotations

from typing import TYPE_CHECKING

import bot
from core.utils import find
from nextcord import DiscordException

from .subbing import pick_available

if TYPE_CHECKING:
	from nextcord import Member


class Draft:
	"""Manages the draft stage: captains claim or relinquish team slots, then
	alternately pick from the unpicked pool (teams[2]) following pick_order,
	with automatic last-picks when one team's remaining turns cover the pool.
	Also handles manual roster edits (put) and substitutions (sub_me / sub_for /
	sub_auto) across the CHECK_IN, DRAFT, and WAITING_REPORT stages. All state
	is mutated on the shared Match object (self.m).

	pick_order is stored as team indices (0/1) translated from the config's
	letter form via pick_steps; e.g. "abba" -> [0, 1, 1, 0].
	"""

	# Maps the config's captain-letter pick order ("a"/"b") to team indices.
	pick_steps = {
		"a": 0,
		"b": 1
	}

	def __init__(self, match: bot.Match, pick_order: str | None, captains_role_id: int | None):
		"""Translate pick_order into team indices and, for draft-mode configs,
		append the DRAFT state to the match's state sequence."""
		self.m = match
		self.pick_order = [self.pick_steps[i] for i in pick_order] if pick_order else []
		self.captains_role_id = captains_role_id
		self.sub_queue = []

		if self.m.cfg['pick_teams'] == "draft":
			self.m.states.append(self.m.DRAFT)

	async def _refresh_ratings(self) -> None:
		"""Rebuild self.m.ratings from the current roster. Call after any roster
		change (put/sub) so downstream matchmaking and rating math see correct
		ELOs."""
		self.m.ratings = {
			p['user_id']: p['rating']
			for p in await self.m.qc.rating.get_players((p.id for p in self.m.players))
		}

	async def start(self, ctx: bot.Context) -> None:
		"""Entry point for the draft stage: render the initial board."""
		await self.refresh(ctx)

	async def print(self, ctx: bot.Context) -> None:
		"""Render the draft board embed."""
		try:
			await ctx.notice(embed=self.m.embeds.draft())
		except DiscordException as e:
			# Don't crash the match on a failed embed (rate limit / perms), but
			# surface it in the Railway logs rather than swallowing silently.
			bot.log.error(f"Draft.print failed for match {self.m.id}: {e}")

	async def refresh(self, ctx: bot.Context) -> None:
		"""Redraw the board or advance the state machine: reprint while not in
		DRAFT, or while players remain unpicked and any team is still short;
		once the pool empties and teams are full, advance to the next state."""
		if self.m.state != self.m.DRAFT:  # noqa: SIM114
			await self.print(ctx)
		elif len(self.m.teams[2]) and any((len(t) < self.m.cfg['team_size'] for t in self.m.teams)):
			await self.print(ctx)
		else:
			await self.m.next_state(ctx)

	async def cap_me(self, ctx: bot.Context, author: Member) -> None:
		"""Relinquish captaincy: a captain who hasn't picked yet steps back into
		the unpicked pool. (The claim direction is cap_for.)"""
		if self.m.state != self.m.DRAFT:
			raise bot.Exc.MatchStateError(self.m.gt("The match is not on the draft stage."))

		team = find(lambda t: author in t, self.m.teams)
		if team.idx == 2 or team.index(author) != 0:
			raise bot.Exc.PermissionError(self.m.gt("You are not a captain."))
		if len(team) > 1:
			raise bot.Exc.PermissionError(self.m.gt("Can't do that after you've started picking."))

		team.remove(author)
		self.m.teams[2].add(author)
		await self.print(ctx)

	async def cap_for(self, ctx: bot.Context, author: Member, team_name: str) -> None:
		"""Claim an empty captain slot on the named team (position 0), moving the
		caller out of their current team. Requires the captain role if one is
		configured."""
		if self.m.state != self.m.DRAFT:
			raise bot.Exc.MatchStateError(self.m.gt("The match is not on the draft stage."))
		elif self.captains_role_id and self.captains_role_id not in (r.id for r in author.roles):
			raise bot.Exc.PermissionError(self.m.gt("You must possess the captain's role."))
		elif (team := find(lambda t: t.name.lower() == team_name.lower(), self.m.teams[:2])) is None:
			raise bot.Exc.SyntaxError(self.m.gt("Specified team name not found."))
		elif len(team):
			raise bot.Exc.PermissionError(
				self.m.gt(f"Team **{team.name}** already have a captain. The captain must type **/capme** first.")
			)

		find(lambda t: author in t, self.m.teams).remove(author)
		team.insert(0, author)
		await self.print(ctx)

	async def pick(self, ctx: bot.Context, author: Member, players: list[Member]) -> None:
		"""Captain picks one or more players from the unpicked pool, enforcing
		pick_order turn-taking. When one team's remaining turns cover the rest
		of the pool, the last picks are auto-assigned."""
		# State and captaincy don't change mid-loop, so validate once up front.
		if self.m.state != self.m.DRAFT:
			raise bot.Exc.MatchStateError(self.m.gt("The match is not on the draft stage."))
		elif (team := find(lambda t: author in t[:1], self.m.teams[:2])) is None:
			raise bot.Exc.PermissionError(self.m.gt("You are not a captain."))

		for player in players:
			pick_step = max(0, (len(self.m.teams[0]) + len(self.m.teams[1]) - 2))
			# picker_team is None on the final step, which intentionally disables the
			# turn check below (the last pick is forced / handled by auto last-pick).
			picker_team = self.m.teams[self.pick_order[pick_step]] if pick_step < len(self.pick_order) - 1 else None

			if picker_team is not None and picker_team is not team:
				raise bot.Exc.PermissionError(self.m.gt("Not your turn to pick."))
			elif player not in self.m.teams[2]:
				raise bot.Exc.NotFoundError(self.m.gt("Specified player not in the unpicked list."))

			self.m.teams[2].remove(player)
			team.append(player)

			# auto last-pick rest of the players if possible
			# if rest of pick_order covers the unpicked list
			if len(self.m.teams[2]) and len(self.pick_order[pick_step+1:]) >= len(self.m.teams[2]):  # noqa: SIM102
				# if rest of pick_order is a single team
				if len(set(self.pick_order[pick_step+1:])) == 1:
					picker_team = self.m.teams[self.pick_order[pick_step+1]]
					picker_team.extend(self.m.teams[2])
					self.m.teams[2].clear()

		await self.refresh(ctx)

	async def put(self, ctx: bot.Context, player: Member, team_name: str) -> None:
		"""Manually place a player on the named team during DRAFT or
		WAITING_REPORT: move them if already rostered, otherwise add them
		(refreshing ratings), then remove them from the queue."""
		if (team := find(lambda t: t.name.lower() == team_name.lower(), self.m.teams)) is None:
			raise bot.Exc.SyntaxError(self.m.gt("Specified team name not found."))
		if self.m.state not in [self.m.DRAFT, self.m.WAITING_REPORT]:
			raise bot.Exc.MatchStateError(self.m.gt("The match must be on the draft or waiting report stage."))

		if (old_team := find(lambda t: player in t, self.m.teams)) is not None:
			old_team.remove(player)
		else:
			self.m.players.append(player)
			await self._refresh_ratings()

		team.append(player)
		await self.m.qc.remove_members(player, ctx=ctx)
		await self.refresh(ctx)

	async def sub_me(self, ctx: bot.Context, author: Member) -> None:
		"""Toggle whether the caller is listed as looking for a substitute."""
		if self.m.state not in [self.m.DRAFT, self.m.WAITING_REPORT]:
			raise bot.Exc.MatchStateError(self.m.gt("The match must be on the draft or waiting report stage."))

		if author in self.sub_queue:
			self.sub_queue.remove(author)
			await ctx.success(self.m.gt("You have stopped looking for a substitute."))
		else:
			self.sub_queue.append(author)
			await ctx.success(self.m.gt("You are now looking for a substitute."))

	async def sub_for(self, ctx: bot.Context, player1: Member, player2: Member, force: bool = False) -> None:
		"""Swap player1 out for player2 on their team (force skips the
		looking-for-sub check), updating the roster, players list, sub queue,
		and ratings, then refresh the stage-appropriate view."""
		if self.m.state not in [self.m.CHECK_IN, self.m.DRAFT, self.m.WAITING_REPORT]:
			raise bot.Exc.MatchStateError(self.m.gt("The match must be on the check-in, draft or waiting report stage."))
		elif not force and player1 not in self.sub_queue:
			raise bot.Exc.PermissionError(self.m.gt("Specified player is not looking for a substitute."))

		team = find(lambda t: player1 in t, self.m.teams)
		team[team.index(player1)] = player2
		self.m.players.remove(player1)
		self.m.players.append(player2)
		if player1 in self.sub_queue:
			self.sub_queue.remove(player1)
		await self._refresh_ratings()
		await self.m.qc.remove_members(player2, ctx=ctx)
		await bot.remove_players(player2, reason="pickup started")

		if self.m.state == self.m.CHECK_IN:
			await self.m.check_in.refresh()
		elif self.m.state == self.m.WAITING_REPORT:
			await ctx.notice(embed=self.m.embeds.final_message())
		else:
			await self.print(ctx)

	async def sub_auto(self, ctx: bot.Context, author: Member) -> None:
		"""Substitute the caller with the next available queued player, then
		fully re-matchmake both teams (unlike sub_for, which swaps in place)."""
		if self.m.state not in [self.m.DRAFT, self.m.WAITING_REPORT]:
			raise bot.Exc.MatchStateError(self.m.gt("The match must be on the draft or waiting report stage."))

		# Grab the next queued player who isn't already committed to another
		# active match. busy_ids spans every active match, so it also excludes
		# this match's own players (the caller included).
		busy_ids = {p.id for m in bot.active_matches for p in m.players}
		candidate = pick_available(self.m.queue.queue, busy_ids)
		if candidate is None:
			raise bot.Exc.NotFoundError(self.m.gt("There are no available players in the queue to substitute in."))

		# Swap the caller out for the candidate and recompute ratings for the
		# new roster so the rebalance below sees correct ELOs.
		self.m.players.remove(author)
		self.m.players.append(candidate)
		if author in self.sub_queue:
			self.sub_queue.remove(author)
		await self._refresh_ratings()

		# Pull the candidate out of the queue and expire timers, like /subfor.
		await self.m.qc.remove_members(candidate, ctx=ctx)
		await bot.remove_players(candidate, reason="pickup started")

		# Full re-matchmaking: re-split everyone into the two most ELO-balanced
		# teams. Reuses the proven matchmaking path; teams[0][0]/teams[1][0]
		# become each team's reporting captain (sorted by rating).
		self.m.init_teams("matchmaking")

		await ctx.notice(self.m.gt("{old} was substituted by {new}. Teams have been rebalanced.").format(
			old=author.mention, new=candidate.mention
		))

		if self.m.state == self.m.WAITING_REPORT:
			await ctx.notice(embed=self.m.embeds.final_message())
		else:
			await self.refresh(ctx)
