# -*- coding: utf-8 -*-
from time import time
from itertools import combinations
from collections import deque
import random
from nextcord import DiscordException, Embed, Colour

import bot
from core.utils import find, get, iter_to_dict, join_and, get_nick  # noqa: F401
from core.console import log  # noqa: F401
from core.client import dc

from .check_in import CheckIn
from .draft import Draft
from .embeds import Embeds


# Custom rank emojis for Q6 server
RANK_EMOJIS = [
	(0,    "<:CHAD:1471923932558000270>"),
	(800,  "<:Q6Wood:1514727440692547685>"),
	(1000, "<:Q6Iron:1514727400200470820>"),
	(1200, "<:Q6Bronze:1514727471205847170>"),
	(1400, "<:Q6Silver:1514727221808332800>"),
	(1600, "<:Q6Gold:1514727359461462076>"),
	(1800, "<:Q6Diamond:1514727335549472930>"),
	(2000, "<:Q6Champion:1514727158596112464>"),
	(2200, "<:Q6Star:1514727286132441238>"),
]


def get_rank_emoji(rating):
	"""Return the custom emoji for a given rating."""
	emoji = RANK_EMOJIS[0][1]
	for threshold, e in RANK_EMOJIS:
		if rating >= threshold:
			emoji = e
	return emoji



# ── Hogwarts house roles → team names ─────────────────────────────────────────
# Captain's Discord role determines their team name.
HOUSE_ROLES = {
	1468807660760596593: 'Hufflepuff',
	1467995936621068308: 'Slytherin',
	1468807395659485265: 'Gryffindor',
	1468807668197097711: 'Ravenclaw',
}


# ── Smart captain selection constants ─────────────────────────────────────────
_QUIDDITCH_ROLES = ['chaser', 'beater', 'seeker', 'keeper', 'flex']
_FLEX_COMPATIBLE = {'keeper', 'seeker', 'beater'}
# How many past matches to remember for the recent captain penalty
_CAPTAIN_HISTORY_SIZE = 5


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
		pick_order=None, maps=[], vote_maps=0, map_count=0, check_in_timeout=0,
		check_in_discard=True, check_in_discard_immediately=True, match_lifetime=3*60*60, start_msg=None, server=None,
		show_streamers=True
	)

	class Team(list):
		def __init__(self, name=None, emoji=None, players=None, idx=-1):
			super().__init__(players or [])
			self.name = name
			self.emoji = emoji
			self.draw_flag = False
			self.idx = idx

		def set(self, players):
			self.clear()
			self.extend(players)

		def add(self, p):
			if p not in self:
				self.append(p)

		def rem(self, p):
			if p in self:
				self.remove(p)

	@classmethod
	async def new(cls, ctx, queue, players, **kwargs):
		ratings = {p['user_id']: p['rating'] for p in await ctx.qc.rating.get_players((p.id for p in players))}
		match_id = await bot.stats.next_match()
		match = cls(match_id, queue, ctx.qc, players, ratings, **kwargs)
		match.maps = match.random_maps(match.cfg['maps'], match.cfg['map_count'], queue.last_maps)
		match.init_captains(match.cfg['pick_captains'], match.cfg['captains_role_id'])
		match.init_teams(match.cfg['pick_teams'])
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
	async def fake_ranked_match(cls, ctx, queue, qc, winners, losers, draw=False, **kwargs):
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

	def serialize(self):
		return dict(
			match_id=self.id,
			queue_id=self.queue.id,
			channel_id=self.queue.qc.id,
			cfg=self.cfg,
			players=[p.id for p in self.players if p],
			teams=[[p.id for p in team if p] for team in self.teams],
			maps=self.maps,
			state=self.state,
			states=self.states,
			ready_players=[p.id for p in self.check_in.ready_players if p],
			fill_subs=self.fill_subs
		)

	@classmethod
	async def from_json(cls, data):
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
		match.maps = data['maps']
		match.state = data['state']
		match.states = data['states']
		if match.state == match.CHECK_IN:
			ctx = bot.SystemContext(qc)
			await match.check_in.start(ctx)

		bot.active_matches.append(match)

	def __init__(self, match_id, queue, qc, players, ratings, **cfg):
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
		self.maps = []
		self.lifetime = self.cfg['match_lifetime']
		self.start_time = int(time())
		self.state = self.INIT

		self.check_in = CheckIn(self, self.cfg['check_in_timeout'])
		self.draft = Draft(self, self.cfg['pick_order'], self.cfg['captains_role_id'])
		self.embeds = Embeds(self)
		self.party_code = None   # set after draft when pick_teams=='draft'
		self.fill_subs: dict = {}  # {player2_id: (player1_id, team_idx)} for 'Match in progress' subs

	@staticmethod
	def random_maps(maps, map_count, last_maps=None):
		for last_map in (last_maps or [])[::-1]:
			if last_map in maps and map_count < len(maps):
				maps.remove(last_map)
		return random.sample(maps, min(map_count, len(maps)))

	def sort_players(self, players):
		return sorted(
			players,
			key=lambda p: [self.cfg['captains_role_id'] in [role.id for role in p.roles], self.ratings[p.id]],
			reverse=True
		)


	# ── Smart captain helpers ──────────────────────────────────────────────────

	def _get_quidditch_role(self, player):
		"""Return the player's Quidditch role from their Discord roles. Defaults to chaser."""
		role_names = {r.name.lower() for r in player.roles}
		for role in _QUIDDITCH_ROLES:
			if role in role_names:
				return role
		return 'chaser'

	@staticmethod
	def _role_bonus(role1, role2):
		"""Role compatibility bonus: +300 same role, +200 flex+specialist, +0 otherwise."""
		if role1 == role2:
			return 300
		if role1 == 'flex' and role2 in _FLEX_COMPATIBLE:
			return 200
		if role2 == 'flex' and role1 in _FLEX_COMPATIBLE:
			return 200
		return 0

	@staticmethod
	def _mmr_bonus(mmr1, mmr2):
		"""MMR similarity bonus — max +300 for identical MMR, 0 for 1000+ gap."""
		return max(0, int(300 * (1 - abs(mmr1 - mmr2) / 1000)))

	def _captain_role_bonus(self, p1, p2):
		"""Captain role bonus: +1000 both have role, +300 one has role, +0 neither."""
		cap_role_id = self.cfg.get('captains_role_id')
		if not cap_role_id:
			return 0
		p1_has = cap_role_id in {r.id for r in p1.roles}
		p2_has = cap_role_id in {r.id for r in p2.roles}
		if p1_has and p2_has:
			return 1000
		if p1_has or p2_has:
			return 300
		return 0

	def _smart_captain_selection(self):
		"""Score every candidate pair and return the highest-scoring two players as captains.

		Scoring per pair:
		  MMR similarity  : max(0, int(300 * (1 - |mmr_a - mmr_b| / 1000)))
		  Role bonus      : +300 same Quidditch role, +200 Flex+specialist, +0 otherwise
		  Captain role    : +1000 both have role, +300 one has role
		  Recent penalty  : -300 × appearances in last _CAPTAIN_HISTORY_SIZE matches each
		"""
		last_captains = getattr(self.qc, '_last_captains', frozenset())
		captain_history = getattr(self.qc, '_captain_history', deque(maxlen=_CAPTAIN_HISTORY_SIZE))

		def recent_count(pid):
			return sum(1 for match_caps in captain_history if pid in match_caps)

		def score_pair(p1, p2):
			mmr1 = self.ratings.get(p1.id, 1500)
			mmr2 = self.ratings.get(p2.id, 1500)
			role1 = self._get_quidditch_role(p1)
			role2 = self._get_quidditch_role(p2)
			return (
				self._mmr_bonus(mmr1, mmr2)
				+ self._role_bonus(role1, role2)
				+ self._captain_role_bonus(p1, p2)
				+ (recent_count(p1.id) + recent_count(p2.id)) * -300
			)

		# Prefer players who weren't captains last match
		non_recent = [p for p in self.players if p.id not in last_captains]
		candidates = non_recent if len(non_recent) >= 2 else self.players

		if len(candidates) < 2:
			return sorted(self.players, key=lambda p: self.ratings.get(p.id, 0), reverse=True)[:2]

		best = max(combinations(candidates, 2), key=lambda pair: score_pair(pair[0], pair[1]))
		return list(best)


	# ── House name assignment ──────────────────────────────────────────────────

	@staticmethod
	def _get_house(player):
		"""Return the player's Hogwarts house name from their Discord roles, or None."""
		for role in player.roles:
			if role.id in HOUSE_ROLES:
				return HOUSE_ROLES[role.id]
		return None

	def _assign_house_names(self):
		"""Rename teams from captain house roles.
		If a captain has no house role, a house is chosen at random.
		Always tries to give both teams different houses.
		"""
		if len(self.captains) < 2:
			return

		all_houses = list(HOUSE_ROLES.values())  # Hufflepuff, Slytherin, Gryffindor, Ravenclaw

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

	def init_captains(self, pick_captains, captains_role_id):
		if pick_captains == "smart":
			self.captains = self._smart_captain_selection()
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

	def init_teams(self, pick_teams):
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

	async def think(self, frame_time):
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

	async def next_state(self, ctx):
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

	async def _priority_cross_queue_cleanup(self, ctx):
		"""Remove this match\'s players from other queues, respecting priority.

		Called when the match transitions from check-in into the next phase
		(DRAFT or WAITING_REPORT for queues without a draft). Players are
		only removed from queues whose priority is <= this queue\'s priority.
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

	def rank_str(self, member):
		return self.queue.qc.rating_rank(self.ratings[member.id])['rank']

	async def start_waiting_report(self, ctx):
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

	async def report_loss(self, ctx, member, draw_flag):
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

	async def report_win(self, ctx, team_name, draw=False):
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

	async def report_scores(self, ctx, scores):
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

	async def print_rating_results(self, ctx, before, after):
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

		embed = Embed(title=title, colour=Colour(0x27b75e) if self.winner is not None else Colour(0xf5d858))

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

	async def final_message(self, ctx):
		try:
			await ctx.notice(embed=self.embeds.final_message())
		except DiscordException:
			pass

	async def finish_match(self, ctx):
		bot.active_matches.remove(self)

		# Track captains for smart captain selection in future matches
		if len(self.teams[0]) and len(self.teams[1]):
			cap_ids = frozenset({self.teams[0][0].id, self.teams[1][0].id})
			self.qc._last_captains = cap_ids
			if not hasattr(self.qc, '_captain_history'):
				self.qc._captain_history = deque(maxlen=_CAPTAIN_HISTORY_SIZE)
			self.qc._captain_history.append(cap_ids)

		self.queue.last_maps += self.maps
		self.queue.last_maps = self.queue.last_maps[-len(self.maps)*self.queue.cfg.map_cooldown:]

		if self.ranked:
			await bot.stats.register_match_ranked(ctx, self)
		else:
			await bot.stats.register_match_unranked(ctx, self)

		# Award Hogwarts house points to the winning team (no-op on draws/cancels)
		if self.winner is not None and self.winner in (0, 1):
			try:
				from bot.stats.house_points import award_for_win
				winning_team = self.teams[self.winner]
				awarded = await award_for_win(winning_team)
				if awarded:
					await self._post_house_award_embed(ctx, awarded)
			except Exception as e:
				bot_log = __import__("core.console", fromlist=["log"]).log
				bot_log.error(f"[house_points] award_for_win failed: {e}")

	async def _post_house_award_embed(self, ctx, awarded):
		"""Brief announcement listing house point awards from this match."""
		from nextcord import Embed, Colour
		from bot.match.embeds import HOUSE_EMOJIS

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

	def print(self):
		return f"> *({self.id})* **{self.queue.name}** | `{join_and([get_nick(p) for p in self.players])}`"

	async def cancel(self, ctx):
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
