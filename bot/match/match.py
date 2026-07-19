# -*- coding: utf-8 -*-
"""Match object: the per-match state machine (INIT -> CHECK_IN -> DRAFT ->
WAITING_REPORT), team/captain setup and matchmaking, result reporting, and
rating/house-point finalization. Owns the CheckIn, Draft, and Embeds
sub-controllers.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from time import time
from itertools import combinations
from collections import deque
import random
from nextcord import DiscordException, Embed, Colour

import bot
from bot.constants import HOUSE_ROLES, ALL_HOUSES, get_rank_emoji, CAPTAIN_ROLE_ID, MATCH_COLOUR_REPORTED
from core.utils import find, get, join_and, get_nick
from core.console import log
from core.client import dc

from .check_in import CheckIn
from .draft import Draft
from .embeds import Embeds
from .captain_selection import (
	select_smart_captains,
	select_captain_role_captains,
	CAPTAIN_HISTORY_SIZE as _CAPTAIN_HISTORY_SIZE,
)

if TYPE_CHECKING:
	from nextcord import Member


# House roles centralized in bot/constants.py — imported above.
# Captain-selection scoring/strategies live in captain_selection.py; init_captains
# below is a thin dispatcher that delegates the 'smart' and 'captain_role' modes
# there and keeps the simpler inline modes here.


class Match:

	INIT = 0
	CHECK_IN = 1
	DRAFT = 2
	WAITING_REPORT = 3

	TEAM_EMOJIS = [
		":fox:", ":wolf:", ":dog:", ":bear:", ":panda_face:", ":tiger:", ":lion:", ":pig:", ":octopus:", ":boar:",
		":scorpion:", ":crab:", ":eagle:", ":shark:", ":bat:", ":rhino:", ":dragon_face:", ":deer:"
	]

	default_cfg = dict(
		teams=None, team_names=['Alpha', 'Beta'], team_emojis=None, ranked=False,
		team_size=1, pick_captains="no captains", captains_role_id=None, pick_teams="draft",
		pick_order=None, check_in_timeout=0,
		check_in_discard=True, check_in_discard_immediately=True, match_lifetime=3*60*60, start_msg=None, server=None,
		show_streamers=True
	)

	class Team(list):
		"""A team roster (a list of Members) with a name, emoji, board index (0 or
		1 for the two teams, -1 for the unpicked pool), and a draw flag."""
		def __init__(self, name: str | None = None, emoji: str | None = None, players: list[Member] | None = None, idx: int = -1):
			super().__init__(players or [])
			self.name = name
			self.emoji = emoji
			self.draw_flag = False
			self.idx = idx

		def set(self, players: list[Member]) -> None:
			self.clear()
			self.extend(players)

		def add(self, p: Member) -> None:
			if p not in self:
				self.append(p)

	@classmethod
	async def new(cls, ctx: bot.Context, queue: bot.PickupQueue, players: list[Member], **kwargs) -> None:
		"""Create a match: fetch ratings, run captain selection and team setup,
		update captain streaks (captain_role mode), assign house names, stash the
		season number, and register it in bot.active_matches."""
		ratings = {p['user_id']: p['rating'] for p in await ctx.qc.rating.get_players((p.id for p in players))}
		match_id = await bot.stats.next_match()
		match = cls(match_id, queue, ctx.qc, players, ratings, **kwargs)
		# Pre-fetch captain streaks if this queue uses captain_role mode.
		# The selection runs synchronously, so we read streaks ahead of time and
		# stash them on the match for the sync code to consult.
		match._captain_streaks = {}
		if match.cfg['pick_captains'] == 'captain_role':
			try:
				from bot.stats.captain_streak import get_streak
				for p in players:
					match._captain_streaks[p.id] = await get_streak(ctx.qc.id, p.id)
			except Exception as exc:
				from core.console import log
				log.error(f"[captain_streak] prefetch failed: {exc}")

		match.init_captains(match.cfg['pick_captains'], match.cfg['captains_role_id'])
		# 🟡 The Snitch flip: FIRST PICK is a genuine coin toss, not an artifact
		# of captain-selection order. Shuffling here decides which captain seats
		# into teams[0] vs teams[1] — and therefore who the pick_order hands the
		# opening pick. Draft.start() announces the result with the snitch embed.
		if len(match.captains) == 2:
			random.shuffle(match.captains)
		match.init_teams(match.cfg['pick_teams'])

		# Update streaks now that captains are locked in. Picked captains get
		# their counter incremented; everyone else who was role-eligible but
		# not picked gets reset to 0.
		if match.cfg['pick_captains'] == 'captain_role':
			try:
				from bot.stats.captain_streak import record_captain, reset_streak
				captain_ids = {c.id for c in match.captains}
				for p in players:
					has_role = CAPTAIN_ROLE_ID in {r.id for r in p.roles}
					if p.id in captain_ids:
						await record_captain(ctx.qc.id, p.id)
					elif has_role:
						await reset_streak(ctx.qc.id, p.id)
			except Exception as exc:
				from core.console import log
				log.error(f"[captain_streak] post-pick update failed: {exc}")
		match._assign_house_names()  # Name teams from captain house roles

		# Stash the current season number on the match so embeds can show it in the footer.
		try:
			from bot.stats.season import get_current_season_number
			match.season_number = await get_current_season_number(ctx.qc.id)
		except Exception:
			match.season_number = None

		if match.ranked:
			match.states.append(match.WAITING_REPORT)
		bot.active_matches.append(match)

	@classmethod
	async def fake_ranked_match(cls, ctx: bot.Context, queue: bot.PickupQueue, qc: bot.QueueChannel, winners: list[Member], losers: list[Member], draw: bool = False, **kwargs) -> None:
		"""Build a match with a pre-decided winner/loser and register it as ranked
		(used to inject a result without a live match, e.g. admin fixes)."""
		players = winners + losers
		if len(set(players)) != len(players):
			raise bot.Exc.ValueError("Players list can not contains duplicates.")
		ratings = {p['user_id']: p['rating'] for p in await qc.rating.get_players((p.id for p in players))}
		match_id = await bot.stats.next_match()
		match = cls(match_id, queue, qc, players, ratings, pick_teams="premade", **kwargs)
		match.teams[0].set(winners)
		match.teams[1].set(losers)
		if draw:
			match.winner = None
		else:
			match.winner = 0
			match.scores[match.winner] = 1
		await bot.stats.register_match_ranked(ctx, match)

	def serialize(self) -> dict:
		"""Return a JSON-serializable snapshot of match state (ids, not objects)
		for persistence across restarts."""
		return dict(
			match_id=self.id,
			queue_id=self.queue.id,
			channel_id=self.queue.qc.id,
			cfg=self.cfg,
			players=[p.id for p in self.players if p],
			teams=[[p.id for p in team if p] for team in self.teams],
			state=self.state,
			states=self.states,
			ready_players=[p.id for p in self.check_in.ready_players if p],
			fill_subs=self.fill_subs
		)

	@classmethod
	async def from_json(cls, data: dict) -> None:
		"""Rebuild a match from a serialized snapshot: re-resolve members, teams,
		and ready players, restore state, and re-register it as active."""
		if (qc := bot.queue_channels.get(data['channel_id'])) is None:
			raise bot.Exc.ValueError('QueueChannel not found.')
		if (queue := get(qc.queues, id=data['queue_id'])) is None:
			raise bot.Exc.ValueError('Queue not found.')
		if (guild := dc.get_guild(qc.guild_id)) is None:
			raise bot.Exc.ValueError('Guild not found.')

		data['players'] = [guild.get_member(user_id) for user_id in data['players']]
		if None in data['players']:
			raise bot.Exc.ValueError(f"Error fetching guild members.")  # noqa: F541

		for i in range(len(data['teams'])):
			data['teams'][i] = [get(data['players'], id=user_id) for user_id in data['teams'][i]]
		data['ready_players'] = [get(data['players'], id=user_id) for user_id in data['ready_players']]

		ratings = {p['user_id']: p['rating'] for p in await qc.rating.get_players((p.id for p in data['players']))}
		match_id = await bot.stats.next_match()
		match = cls(match_id, queue, qc, data['players'], ratings, **data['cfg'])

		for i in range(len(match.teams)):
			match.teams[i].set(data['teams'][i])
		match.check_in.ready_players = set(data['ready_players'])
		match.state = data['state']
		match.states = data['states']
		if match.state == match.CHECK_IN:
			ctx = bot.SystemContext(qc)
			await match.check_in.start(ctx)

		bot.active_matches.append(match)

	def __init__(self, match_id: int, queue: bot.PickupQueue, qc: bot.QueueChannel, players: list[Member], ratings: dict, **cfg):
		"""Initialize match state from merged config: build the two teams plus the
		unpicked pool, and create the CheckIn, Draft, and Embeds sub-controllers."""
		self.queue = queue
		self.qc = qc
		self.gt = qc.gt

		cfg = {k: v for k, v in cfg.items() if v is not None}
		self.cfg = self.default_cfg.copy()
		self.cfg.update(cfg)

		self.id = match_id
		self.ranked = self.cfg['ranked'] and self.cfg['pick_teams'] != 'no teams'
		self.players = list(players)
		self.ratings = ratings
		self.winner = None
		self.scores = [0, 0]

		team_names = self.cfg['team_names']
		team_emojis = self.cfg['team_emojis'] or random.sample(self.TEAM_EMOJIS, 2)
		self.teams = [
			self.Team(name=team_names[0], emoji=team_emojis[0], idx=0),
			self.Team(name=team_names[1], emoji=team_emojis[1], idx=1),
			self.Team(name="unpicked", emoji="📋", idx=-1)
		]

		self.captains = []
		self.states = []
		self.lifetime = self.cfg['match_lifetime']
		self.start_time = int(time())
		self.state = self.INIT

		self.check_in = CheckIn(self, self.cfg['check_in_timeout'])
		self.draft = Draft(self, self.cfg['pick_order'], self.cfg['captains_role_id'])
		self.embeds = Embeds(self)
		self.party_code = None   # set after draft when pick_teams=='draft'
		self.fill_subs: dict = {}  # {player2_id: (player1_id, team_idx)} for 'Match in progress' subs

	def sort_players(self, players: list[Member]) -> list[Member]:
		"""Sort players by captain-role eligibility first, then rating (both
		descending)."""
		return sorted(
			players,
			key=lambda p: [self.cfg['captains_role_id'] in [role.id for role in p.roles], self.ratings[p.id]],
			reverse=True
		)

	# ── House name assignment ──────────────────────────────────────────────────

	@staticmethod
	def _get_house(player: Member) -> str | None:
		"""Return the player's Hogwarts house name from their Discord roles, or None."""
		for role in player.roles:
			if role.id in HOUSE_ROLES:
				return HOUSE_ROLES[role.id]
		return None

	def _assign_house_names(self) -> None:
		"""Rename teams from captain house roles.
		If a captain has no house role, a house is chosen at random.
		Always tries to give both teams different houses.
		"""
		if len(self.captains) < 2:
			return

		all_houses = ALL_HOUSES  # Hufflepuff, Slytherin, Gryffindor, Ravenclaw

		house_a = self._get_house(self.captains[0])
		house_b = self._get_house(self.captains[1])

		# No role on captain A — pick randomly, preferring one different from B's house
		if not house_a:
			pool = [h for h in all_houses if h != house_b] if house_b else all_houses
			house_a = random.choice(pool)

		# No role on captain B — pick randomly, preferring one different from A's house
		if not house_b:
			pool = [h for h in all_houses if h != house_a]
			house_b = random.choice(pool) if pool else random.choice(all_houses)

		self.teams[0].name = house_a
		self.teams[1].name = house_b

	def init_captains(self, pick_captains: str, captains_role_id: int | None) -> None:
		"""Select two captains from the current roster per pick_captains mode.
		'smart' and 'captain_role' delegate to captain_selection.py; the simpler
		modes are trivial one-liners kept inline here."""
		if pick_captains == "smart":
			self.captains = select_smart_captains(
				self.players,
				self.ratings,
				captains_role_id=captains_role_id,
				last_captains=getattr(self.qc, '_last_captains', frozenset()),
				captain_history=getattr(self.qc, '_captain_history', None),
			)
		elif pick_captains == "captain_role":
			from bot.stats.captain_streak import is_capped
			self.captains = select_captain_role_captains(
				self.players,
				self.ratings,
				CAPTAIN_ROLE_ID,
				is_capped,
				captain_streaks=getattr(self, '_captain_streaks', {}),
				captains_role_id=captains_role_id,
				last_captains=getattr(self.qc, '_last_captains', frozenset()),
				captain_history=getattr(self.qc, '_captain_history', None),
			)
		elif pick_captains == "by role and rating":
			self.captains = self.sort_players(self.players)[:2]
		elif pick_captains == "fair pairs":
			candidates = sorted(self.players, key=lambda p: [self.ratings[p.id]], reverse=True)
			i = random.randrange(len(candidates) - 1)
			self.captains = [candidates[i], candidates[i + 1]]
		elif pick_captains == "random":
			self.captains = random.sample(self.players, 2)
		elif pick_captains == "random with role preference":
			rand = random.sample(self.players, len(self.players))
			self.captains = sorted(
				rand, key=lambda p: self.cfg['captains_role_id'] in [role.id for role in p.roles], reverse=True
			)[:2]

	def init_teams(self, pick_teams: str) -> None:
		"""Populate teams[0], teams[1], and the unpicked pool (teams[2]) per
		pick_teams mode: 'draft' seats captains and pools the rest; the matchmaking
		modes split players into the most rating-balanced teams; 'random teams'
		splits at random."""
		if pick_teams == "draft":
			self.teams[0].set(self.captains[:1])
			self.teams[1].set(self.captains[1:])
			self.teams[2].set([p for p in self.players if p not in self.captains])
		elif pick_teams == "matchmaking":
			team_len = min(self.cfg['team_size'], int(len(self.players)/2))
			best_rating = sum(self.ratings.values())/2
			best_team = min(
				combinations(self.players, team_len),
				key=lambda team: abs(sum([self.ratings[m.id] for m in team])-best_rating)
			)
			self.teams[0].set(self.sort_players(best_team[:self.cfg['team_size']]))
			self.teams[1].set(self.sort_players(
				[p for p in self.players if p not in best_team][:self.cfg['team_size']]
			))
			self.teams[2].set([p for p in self.players if p not in [*self.teams[0], *self.teams[1]]])
		elif pick_teams == "captain based matchmaking":
			team_len = min(self.cfg['team_size'], int(len(self.players)/2))
			best_rating = sum(self.ratings.values())/2
			regular_team = min(
				combinations(self.players, team_len),
				key=lambda team: abs(sum([self.ratings[m.id] for m in team])-best_rating)
			)
			regular_diff = abs(sum(self.ratings[m.id] for m in regular_team) - best_rating) * 2
			sorted_by_rating = sorted(self.players, key=lambda p: self.ratings[p.id], reverse=True)
			captain_strong = sorted_by_rating[0]
			captain_weak = sorted_by_rating[1]
			remaining = sorted_by_rating[2:]
			remaining_team_len = team_len - 1
			favor_combo = None
			favor_score = float('inf')
			balanced_combo = None
			balanced_diff = float('inf')
			for combo in combinations(remaining, remaining_team_len):
				others = [p for p in remaining if p not in combo][:remaining_team_len]
				strong_captain_team_elo = self.ratings[captain_strong.id] + sum(self.ratings[p.id] for p in combo)
				weak_captain_team_elo = self.ratings[captain_weak.id] + sum(self.ratings[p.id] for p in others)
				diff = weak_captain_team_elo - strong_captain_team_elo
				abs_diff = abs(diff)
				if abs_diff < balanced_diff:
					balanced_diff = abs_diff
					balanced_combo = combo
				score = diff if diff >= 0 else abs_diff + 1e6
				if score < favor_score:
					favor_score = score
					favor_combo = combo
			favor_diff = abs(
				(self.ratings[captain_strong.id] + sum(self.ratings[p.id] for p in favor_combo)) -
				(self.ratings[captain_weak.id] + sum(self.ratings[p.id] for p in
					[p for p in remaining if p not in favor_combo][:remaining_team_len]))
			)
			if favor_diff - regular_diff <= 50:
				best_combo = favor_combo
			elif balanced_diff - regular_diff <= 50:
				best_combo = balanced_combo
			else:
				self.teams[0].set(self.sort_players(regular_team[:self.cfg['team_size']]))
				self.teams[1].set(self.sort_players(
					[p for p in self.players if p not in regular_team][:self.cfg['team_size']]
				))
				self.teams[2].set([p for p in self.players if p not in [*self.teams[0], *self.teams[1]]])
				return
			weak_team_remaining = [p for p in remaining if p not in best_combo][:remaining_team_len]
			self.captains = [captain_strong, captain_weak]
			self.teams[0].set(self.sort_players([captain_strong] + list(best_combo)))
			self.teams[1].set(self.sort_players([captain_weak] + weak_team_remaining))
			self.teams[2].set([p for p in self.players if p not in [*self.teams[0], *self.teams[1]]])
		elif pick_teams == "random teams":
			self.teams[0].set(random.sample(self.players, min(len(self.players)//2, self.cfg['team_size'])))
			self.teams[1].set([p for p in self.players if p not in self.teams[0]][:self.cfg['team_size']])
			self.teams[2].set([p for p in self.players if p not in [*self.teams[0], *self.teams[1]]])

	async def think(self, frame_time: float) -> None:
		"""Per-frame tick: advance out of INIT, drive check-in, or cancel the match
		once it outlives its lifetime."""
		if self.state == self.INIT:
			await self.next_state(bot.SystemContext(self.qc))
		elif self.state == self.CHECK_IN:
			await self.check_in.think(frame_time)
		elif frame_time > self.lifetime + self.start_time:
			ctx = bot.SystemContext(self.qc)
			try:
				await ctx.error(self.gt("Match {queue} ({id}) has timed out.").format(
					queue=self.queue.name, id=self.id
				))
			except DiscordException:
				pass
			await self.cancel(ctx)

	async def next_state(self, ctx: bot.Context) -> None:
		"""Advance the state machine: pop the next queued state and start its
		handler, or finish the match when no states remain."""
		if len(self.states):
			self.state = self.states.pop(0)
			if self.state == self.CHECK_IN:
				await self.check_in.start(ctx)
			elif self.state == self.DRAFT:
				await self._priority_cross_queue_cleanup(ctx)
				await self.draft.start(ctx)
			elif self.state == self.WAITING_REPORT:
				await self._priority_cross_queue_cleanup(ctx)
				await self.start_waiting_report(ctx)
		else:
			if self.state != self.WAITING_REPORT:
				await self.final_message(ctx)
			await self.finish_match(ctx)

	async def _priority_cross_queue_cleanup(self, ctx: bot.Context) -> None:
		"""Remove this match's players from other queues, respecting priority.

		Called when the match transitions from check-in into the next phase
		(DRAFT or WAITING_REPORT for queues without a draft). Players are
		only removed from queues whose priority is <= this queue's priority.
		Higher-priority queues keep them, matching the documented behaviour.
		Also flushes the standby pool back into the queue, since standby
		only applies during the check-in window.
		"""
		if getattr(self, "_priority_cleanup_done", False):
			return
		self._priority_cleanup_done = True

		# Flush standby back to the queue — standby is over once we leave check-in.
		if hasattr(self.queue, "standby") and self.queue.standby:
			flushed = list(self.queue.standby)
			self.queue.standby = []
			for p in flushed:
				if p not in self.queue.queue and len(self.queue.queue) < self.queue.cfg.size:
					self.queue.queue.append(p)
			if self.queue.queue and self.queue not in bot.active_queues:
				bot.active_queues.append(self.queue)

		my_priority = getattr(self, "_my_priority", None)
		if my_priority is None:
			my_priority = getattr(self.queue.cfg, "priority", None) or 0

		player_ids = {p.id for p in self.players}
		for qc in bot.queue_channels.values():
			for q in qc.queues:
				if q is self.queue:
					continue
				q_prio = getattr(q.cfg, "priority", None) or 0
				if q_prio > my_priority:
					continue  # higher-priority queue protects its members
				to_remove = [m for m in q.queue if m.id in player_ids]
				for m in to_remove:
					q.queue.remove(m)
				if not q.queue and q in bot.active_queues:
					bot.active_queues.remove(q)

	def rank_str(self, member: Member) -> str:
		return self.queue.qc.rating_rank(self.ratings[member.id])['rank']

	async def start_waiting_report(self, ctx: bot.Context) -> None:
		"""Enter the waiting-report stage: drop any unpicked players, post the final
		message, and (for draft matches) start party-code collection."""
		if len(self.teams[2]):
			for p in self.teams[2]:
				self.players.remove(p)
			await ctx.notice(self.gt("{players} were removed from the match.").format(
				players=join_and([m.mention for m in self.teams[2]])
			))
			unpicked = list(self.teams[2])
			self.teams[2].clear()
			await self.final_message(ctx)
			await self.queue.revert(ctx, [], unpicked)
		else:
			await self.final_message(ctx)
			# Start party-up and code collection phase for draft matches
			if self.cfg.get('pick_teams') == 'draft' and self.teams[0] and self.teams[1]:
				try:
					from bot.match.party_code import PartyCode
					self.party_code = PartyCode(self)
					await self.party_code.start(ctx)
				except Exception as e:
					log.error(f"PartyCode start error: {e}")

	async def report_loss(self, ctx: bot.Context, member: Member, draw_flag: int) -> None:
		"""Captain reports their team's loss/draw/abort. draw_flag: 0 = loss, 1 =
		draw offer, 2 = abort offer; a draw or abort needs the other captain to
		confirm."""
		if self.state != self.WAITING_REPORT:
			raise bot.Exc.MatchStateError(self.gt("The match must be on the waiting report stage."))

		team = find(lambda team: member in team[:1], self.teams[:2])
		if team is None:
			raise bot.Exc.PermissionError(self.gt("You must be a team captain to report a loss or draw."))

		enemy_team = self.teams[1-team.idx]
		if draw_flag and not enemy_team.draw_flag == draw_flag:  # noqa: SIM201
			team.draw_flag = draw_flag
			await ctx.notice(
				self.gt(
					"{self} is calling a draw, waiting for {enemy} to type `/report draw`." if draw_flag == 1 else
					"{self} offers to cancel the match, waiting for {enemy} to type `/report abort`."
				).format(self=member.mention, enemy=enemy_team[0].mention)
			)
			return

		if draw_flag == 2:
			await self.cancel(ctx)
			return
		elif draw_flag == 1:
			self.winner = None
		else:
			self.winner = enemy_team.idx
			self.scores[self.winner] = 1
		await self.finish_match(ctx)

	async def report_win(self, ctx: bot.Context, team_name: str | None, draw: bool = False) -> None:
		"""Captain reports a win by team_name (or a draw), then finalizes the
		match."""
		if self.state != self.WAITING_REPORT:
			raise bot.Exc.MatchStateError(self.gt("The match must be on the waiting report stage."))

		if draw:
			self.winner = None
		elif team_name and (team := find(lambda t: t.name.lower() == team_name.lower(), self.teams[:2])) is not None:
			self.winner = team.idx
			self.scores[self.winner] = 1
		else:
			raise bot.Exc.SyntaxError(self.gt("Specified team name not found."))

		await self.finish_match(ctx)

	async def report_scores(self, ctx: bot.Context, scores: list[int]) -> None:
		"""Record explicit scores, derive the winner (or draw), then finalize."""
		if self.state != self.WAITING_REPORT:
			raise bot.Exc.MatchStateError(self.gt("The match must be on the waiting report stage."))

		if scores[0] > scores[1]:
			self.winner = 0
		elif scores[1] > scores[0]:
			self.winner = 1
		else:
			self.winner = None

		self.scores = scores
		await self.finish_match(ctx)

	async def print_rating_results(self, ctx: bot.Context, before: dict, after: dict) -> None:
		"""Post a rich embed matching the Q6Bot results style."""
		if self.winner is not None:
			winners = self.teams[self.winner]
			losers = self.teams[abs(self.winner - 1)]
		else:
			winners, losers = self.teams[:2]

		def team_avg(team, data):
			ratings = [data[p.id]['rating'] for p in team]
			return int(sum(ratings) / len(ratings)) if ratings else 0

		def format_team_field(team, data_before, data_after):
			lines = []
			for p in team:
				b = data_before[p.id]['rating']
				a = data_after[p.id]['rating']
				change = a - b
				emoji = get_rank_emoji(b)
				sign = "+" if change >= 0 else ""
				lines.append(f"{emoji} {get_nick(p)}  {b} ↦ {a} ({sign}{change})")
			return "\n".join(lines)

		avg_before_w = team_avg(winners, before)
		avg_after_w = team_avg(winners, after)
		avg_before_l = team_avg(losers, before)
		avg_after_l = team_avg(losers, after)

		w_change = avg_after_w - avg_before_w
		l_change = avg_after_l - avg_before_l

		if self.winner is None:
			title = f"🤝 {self.queue.name.capitalize()} Results — Match {self.id} (Draw)"
		else:
			title = f"{self.queue.name.capitalize()} Results — Match {self.id}"

		# Reported = purple regardless of outcome (the 🤝 title already marks
		# draws); part of the state palette in bot/constants.py.
		embed = Embed(title=title, colour=Colour(MATCH_COLOUR_REPORTED))

		w_sign = "+" if w_change >= 0 else ""
		l_sign = "+" if l_change >= 0 else ""

		if self.winner is not None:
			winner_header = f"🏆 Winner — {winners.name}  {avg_before_w} ↦ {avg_after_w} ({w_sign}{w_change})"
			loser_header = f"💀 Loser — {losers.name}  {avg_before_l} ↦ {avg_after_l} ({l_sign}{l_change})"
		else:
			winner_header = f"⚔️ {winners.name}  {avg_before_w} ↦ {avg_after_w} ({w_sign}{w_change})"
			loser_header = f"⚔️ {losers.name}  {avg_before_l} ↦ {avg_after_l} ({l_sign}{l_change})"

		embed.add_field(
			name=winner_header,
			value=format_team_field(winners, before, after),
			inline=False
		)
		embed.add_field(name="—", value="\u200b", inline=False)
		embed.add_field(
			name=loser_header,
			value=format_team_field(losers, before, after),
			inline=False
		)
		embed.set_footer(text=f"Match id: {self.id}")
		await ctx.notice(embed=embed)

	async def final_message(self, ctx: bot.Context) -> None:
		try:
			await ctx.notice(embed=self.embeds.final_message())
		except DiscordException:
			pass

	async def finish_match(self, ctx: bot.Context) -> None:
		"""Finalize the match: record captains for future selection, register the
		result (ranked/unranked), and award house points to the winner."""
		bot.active_matches.remove(self)

		# Track captains for smart captain selection in future matches
		if len(self.teams[0]) and len(self.teams[1]):
			cap_ids = frozenset({self.teams[0][0].id, self.teams[1][0].id})
			self.qc._last_captains = cap_ids
			if not hasattr(self.qc, '_captain_history'):
				self.qc._captain_history = deque(maxlen=_CAPTAIN_HISTORY_SIZE)
			self.qc._captain_history.append(cap_ids)

		if self.ranked:
			try:
				await bot.stats.register_match_ranked(ctx, self)
			except Exception as e:
				await self._notify_registration_failure(ctx, e)
				raise
		else:
			try:
				await bot.stats.register_match_unranked(ctx, self)
			except Exception as e:
				await self._notify_registration_failure(ctx, e)
				raise

		# Award Hogwarts house points to the winning team (ranked wins only; no-op on draws/cancels)
		if self.ranked and self.winner is not None and self.winner in (0, 1):
			try:
				from bot.stats.house_points import award_for_win
				winning_team = self.teams[self.winner]
				# match_id makes the award reversible via the house_awards ledger.
				awarded = await award_for_win(winning_team, self.id)
				if awarded:
					await self._post_house_award_embed(ctx, awarded)
			except Exception as e:
				bot_log = __import__("core.console", fromlist=["log"]).log
				bot_log.error(f"[house_points] award_for_win failed: {e}")

	async def _notify_registration_failure(self, ctx: bot.Context, exc: Exception) -> None:
		"""Loudly announce that a finished match FAILED to save its result.

		The silent version of this (July 2026: the season-column ALTER never
		ran, so every ranked registration crashed) cost real match results —
		players were freed, no embed posted, and /lastgame just showed the
		previous match. Never again: log it, tell the channel, and point
		admins at the recovery tool. The caller re-raises after this."""
		from core.console import log
		log.error(f"[match {self.id}] registration FAILED — result NOT saved: {exc}")
		from nextcord import Embed, Colour
		embed = Embed(
			colour=Colour(0xED4245),
			title=f"\u26a0\ufe0f Match {str(self.id).zfill(6)} failed to save",
			description=(
				"The match finished but its result could **not** be recorded — "
				"ratings and stats were **not** updated.\n"
				"Admins: check the logs, fix the cause, then re-record the "
				"result with `/admin match create`."
			)
		)
		try:
			await ctx.channel.send(embed=embed)
		except Exception:
			pass

	async def _post_house_award_embed(self, ctx: bot.Context, awarded: dict) -> None:
		"""Brief announcement listing house point awards from this match."""
		from nextcord import Embed, Colour
		from bot.constants import HOUSE_EMOJIS

		lines = []
		for house, pts in sorted(awarded.items(), key=lambda kv: -kv[1]):
			emoji = HOUSE_EMOJIS.get(house, "")
			lines.append(f"{emoji} **{house}** +{pts}")

		embed = Embed(
			colour=Colour(0xf1c40f),
			title=f"\U0001f4dc House Points \u2014 Match {str(self.id).zfill(6)}",
			description="\n".join(lines)
		)
		try:
			await ctx.channel.send(embed=embed)
		except Exception:
			pass

	def print(self) -> str:
		return f"> *({self.id})* **{self.queue.name}** | `{join_and([get_nick(p) for p in self.players])}`"

	async def cancel(self, ctx: bot.Context) -> None:
		"""Cancel the match: tear down check-in reactions and party code, notify
		players, and remove it from active matches."""
		if self.check_in.message and self.check_in.message.id in bot.waiting_reactions.keys():
			bot.waiting_reactions.pop(self.check_in.message.id)
		if self.party_code:
			self.party_code.cancel()
		try:
			await ctx.notice(
				self.gt("{players} your match has been canceled.").format(
					players=join_and([p.mention for p in self.players])
				)
			)
		except DiscordException:
			pass
		bot.active_matches.remove(self)
		if hasattr(self.queue, 'standby'):
			self.queue.standby = []
