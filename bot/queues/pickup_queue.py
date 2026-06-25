# -*- coding: utf-8 -*-

from core.console import log  # noqa: F401
from core.cfg_factory import FactoryTable, CfgFactory, Variables, VariableTable
from core.utils import get_nick, get, SafeTemplateDict
from core.client import dc

import bot


class PickupQueue:

	cfg_factory = CfgFactory(
		table=FactoryTable(name="pq_configs", p_key="pq_id", f_key="channel_id"),
		name="pq_config",
		sections=["General", "Teams", "Appearance", "Maps"],
		icon='pq.png',
		variables=[
			Variables.StrVar(
				"name",
				display="Queue name",
				section="General",
				notnull=True,
				verify=lambda name: len(name) and not any((c in name for c in ": \t\n")),
				verify_message="Invalid queue name. A queue name should be one word without +-: characters."
			),
			Variables.TextVar(
				"description",
				display="Description",
				section="Appearance",
				description="Set an answer on '!help queue' command."
			),
			Variables.IntVar(
				"size",
				display="Queue size",
				section="General",
				verify=lambda i: 0 < i < 1001,
				notnull=True
			),
			Variables.BoolVar(
				"is_default",
				display="is default",
				section="General",
				default=1,
				description="Set if users can add to this queue without specifying its name.",
				notnull=True
			),
			Variables.BoolVar(
				"ranked",
				display="is ranked",
				section="General",
				default=0,
				description="Enable rating features on this queue.",
				notnull=True
			),
			Variables.IntVar(
				"priority",
				display="Queue priority",
				section="General",
				default=0,
				verify=lambda n: 0 <= n <= 1000,
				verify_message="Priority must be between 0 and 1000.",
				description="\n".join([
					"Higher-priority queues protect their members when a lower-priority queue starts.",
					"Example: ranked=100, bonanza=80 → players stay in ranked when bonanza pops.",
					"Set to 0 (default) to use the original behaviour (removed from all queues)."
				])
			),
			Variables.BoolVar(
				"autostart",
				display="Start when full",
				section="General",
				default=1,
				notnull=True
			),
			Variables.DurationVar(
				"check_in_timeout",
				display="Require check-in",
				section="General",
				verify=lambda i: 0 < i < 3601,
				default=60*5,
				verify_message="Check in timeout must be less than a hour.",
				description="Set the check-in stage duration."
			),
			Variables.BoolVar(
				"check_in_discard",
				display="Allow discard check-in",
				section="General",
				default=1,
				description="".join((
					"Allow to discard participation during the check-in stage ",
					"and abort the match if not everyone is ready in time.")),
				notnull=True
			),
			Variables.BoolVar(
				"check_in_discard_immediately",
				display="Check-in discard immediately",
				section="General",
				default=1,
				description="Revert check-in state immediately when someone discards check-in",
				notnull=True
			),
			Variables.IntVar(
				"team_size",
				display="Force team size",
				section="Teams",
				description="Force a maximum amount of players per team.",
				verify=lambda i: 0 < i < 101
			),
			Variables.OptionVar(
				"pick_teams",
				display="Pick teams",
				section="Teams",
				options=["draft", "matchmaking", "captain based matchmaking", "random teams", "no teams"],
				default="draft",
				description="\n".join([
					"Set how teams should be picked:",
					"  draft - host a draft stage where captains will have to pick players",
					"  matchmaking - form teams automatically based on players ratings",
					"  captain based matchmaking - top 2 rated players become captains",
					"  random teams - form teams randomly",
					"  no teams - do not form teams, only print the players list"
				]),
				notnull=True
			),
			Variables.OptionVar(
				"pick_captains",
				display="Pick captains",
				section="Teams",
				options=["smart", "captain_role", "by role and rating", "fair pairs", "random with role preference", "random", "no captains"],
				default="smart",
				description="\n".join([
					"Set how captains should be picked:",
					"  smart - score all pairs by MMR similarity, Quidditch role, captain role, recent penalty",
					"  captain_role - require the Captain role; falls back to smart if <2 holders in queue",
					"  by role and rating - sort by captain role and rating",
					"  fair pairs - random pair with closest ratings",
					"  random with role preference - random with captain role priority",
					"  random - pick captains randomly",
					"  no captains - do not pick captains automatically"
				]),
				notnull=True
			),
			Variables.StrVar(
				"pick_order",
				display="Teams picking order",
				section="Teams",
				verify=lambda s: set(s) == set("ab"),
				default="abababba",
				verify_message="Pick order can only contain a and b characters.",
				description="a - 1st team picks, b - 2nd team picks, example: ababba"
			),
			Variables.StrVar(
				"team_names",
				display="Team names",
				section="Teams",
				verify=lambda s: len(s.split()) == 2,
				verify_message="Team names must be exactly two words separated by space.",
				description="Team names separated by space, example: Alpha Beta"
			),
			Variables.StrVar(
				"team_emojis",
				display="Team emojis",
				section="Teams",
				verify=lambda s: len(s.split()) == 2,
				verify_message="Team emojis must be exactly two emojis separated by space.",
				description="Team emojis separated by space."
			),
			Variables.TextVar(
				"start_msg",
				display="Start message",
				section="Appearance",
				verify=lambda s: len(s) < 1001,
				description="Set additional information to be printed on a match start.",
				verify_message="Start message is too long."
			),
			Variables.TextVar(
				"start_direct_msg",
				display="Start direct message",
				section="Appearance",
				verify=lambda s: len(s) < 1001,
				description="\n".join([
					"Set the content of a direct message sent to players when the queue starts.",
					"You can use this aliases in text: {queue}, {channel}, {server}.",
					"If not set, default translated message is used."
				]),
				verify_message="Start direct message is too long."
			),
			Variables.TextVar(
				"server",
				display="Server",
				section="Appearance",
				description="Print this server on a match start.",
				verify=lambda s: len(s) < 501,
				verify_message="Server string is too long."
			),
			Variables.RoleVar(
				"promotion_role",
				display="Promotion role",
				section="General",
				description="Set a role to highlight on !promote and !sub commands."
			),
			Variables.TextVar(
				"promotion_msg",
				display="Promotion message",
				section="Appearance",
				description="Replace default promotion message. You can use {name}, {role} and {left} placeholders in the text."
			),
			Variables.BoolVar(
				"show_streamers",
				display="Show streamers",
				section="Appearance",
				default=1,
				notnull=True,
				description="Show streaming players on a match start."
			),
			Variables.RoleVar(
				"captains_role",
				display="Captains role",
				section="Teams",
				description="Users with this role may have preference in captains choosing process."
			),
			Variables.RoleVar(
				"blacklist_role",
				display="Blacklist role",
				section="General",
				description="Users with this role wont be able to add to this queue."
			),
			Variables.RoleVar(
				"whitelist_role",
				display="Whitelist role",
				section="General",
				description="Only users with this role will be able to add to this queue."
			),
			Variables.DurationVar(
				"match_lifetime",
				display="Match lifetime",
				verify=lambda i: 299 < i < 86401,
				verify_message="Must be not lesser than 5 minutes and not bigger than 24 hours.",
				section="General",
				description="Set a custom match life time before it times out. Default: 3 hours."
			),
			Variables.IntVar(
				"map_count",
				display="Map count",
				section="Maps",
				default=1,
				verify=lambda n: 0 <= n <= 15,
				verify_message="Maps number must be between 0 and 5.",
				description="Number of maps to show on match start."
			),
			Variables.IntVar(
				"map_cooldown",
				display="Map cooldown",
				section="Maps",
				default=1,
				notnull=True,
				verify=lambda n: 0 <= n <= 100,
				verify_message="Map cooldown number must be between 0 and 100.",
				description="\n".join([
					"Prefer to not choose last played map(s) for the next specified matches amount.",
					"Set 0 to disable."
				])
			),
			Variables.IntVar(
				"vote_maps",
				display="Vote poll map count",
				section="Maps",
				default=None,
				verify=lambda n: 2 <= n <= 9,
				verify_message="Vote maps number must be between 2 and 9.",
				description="Set to enable map voting, this requires check-in timeout to be set."
			),
			VariableTable(
				"aliases", display="Aliases", section="General",
				description="Other names for this queue.",
				variables=[
					Variables.StrVar("alias", notnull=True)
				]
			),
			VariableTable(
				"maps", display="Maps", section="Maps",
				description="List of maps to choose from.",
				variables=[
					Variables.StrVar("name", notnull=True)
				]
			)
		]
	)

	@staticmethod
	def validate_name(name):
		if not len(name) or any((c in name for c in ": \t\n")):
			raise ValueError(f"Invalid queue name '{name}'. A queue name should be one word without +-: characters.")
		return name

	@classmethod
	async def create(cls, ctx, name, size=2):
		cfg = await cls.cfg_factory.spawn(ctx.channel.guild, f_key=ctx.channel.id)
		await cfg.update({"name": name, "size": str(size)})
		return cls(ctx.qc, cfg)

	def serialize(self):
		return dict(
			queue_type=self.__class__.__name__,
			queue_id=self.id,
			channel_id=self.qc.id,
			players=[i.id for i in self.queue if i]
		)

	@classmethod
	async def from_json(cls, data):
		"""Restore queue state from saved JSON.  Missing members are skipped
		gracefully instead of aborting the whole restore."""
		if (qc := bot.queue_channels.get(data['channel_id'])) is None:
			raise bot.Exc.ValueError("QueueChannel not found.")
		if (q := get(qc.queues, id=data['queue_id'])) is None:
			raise bot.Exc.ValueError("Queue not found.")
		if (guild := dc.get_guild(qc.guild_id)) is None:
			raise bot.Exc.ValueError("Guild not found.")

		# Skip members who left the server instead of failing the whole restore
		players = [guild.get_member(uid) for uid in data['players']]
		players = [p for p in players if p is not None]

		q.queue = players
		if q.length and q not in bot.active_queues:
			bot.active_queues.append(q)

	def __init__(self, qc, cfg):
		self.qc = qc
		self.cfg = cfg
		self.id = self.cfg.p_key
		self.queue = []
		self.standby = []  # players who add while a match is in check-in or draft
		self.last_maps = []

	@property
	def name(self):
		return self.cfg.name

	@property
	def status(self):
		return f"{len(self.queue)}/{self.cfg.size}"

	@property
	def who(self):
		return "/".join([f"`{get_nick(m)}`" for m in self.queue])

	@property
	def length(self):
		return len(self.queue)

	def _match_cfg(self):
		return dict(
			team_names=self.cfg.team_names.split(" ") if self.cfg.team_names else None,
			team_emojis=self.cfg.team_emojis.split(" ") if self.cfg.team_emojis else None,
			ranked=self.cfg.ranked, pick_captains=self.cfg.pick_captains,
			captains_role_id=self.cfg.captains_role.id if self.cfg.captains_role else None,
			pick_teams=self.cfg.pick_teams, pick_order=self.cfg.pick_order,
			maps=[i['name'] for i in self.cfg.maps], vote_maps=self.cfg.vote_maps,
			map_count=self.cfg.map_count, check_in_timeout=self.cfg.check_in_timeout,
			check_in_discard=self.cfg.check_in_discard, check_in_discard_immediately=self.cfg.check_in_discard_immediately,
			match_lifetime=self.cfg.match_lifetime,
			start_msg=self.cfg.start_msg, server=self.cfg.server
		)

	async def promote(self, ctx):
		promotion_role = self.cfg.promotion_role or self.qc.cfg.promotion_role
		promotion_msg = self.cfg.promotion_msg or self.qc.gt("{role} Please add to **{name}** pickup, `{left}` players left!")
		promotion_msg = promotion_msg.format_map(SafeTemplateDict(
			role=promotion_role.mention if promotion_role else "",
			name=self.name,
			left=self.cfg.size-self.length
		))
		if (
			promotion_role and not promotion_role.mentionable and
			ctx.channel.guild.me and not ctx.channel.guild.me.guild_permissions.mention_everyone
		):
			raise bot.Exc.PermissionError("Insufficient permissions to ping the promotion role.")
		else:
			await ctx.ignore(ctx.qc.gt("Sending **{queue}** promotion...").format(queue=self.name))
			await ctx.notice(promotion_msg)

	async def reset(self):
		self.queue = []
		if self in bot.active_queues:
			bot.active_queues.remove(self)

	async def check_allowed_to_add(self, member):
		if (
			self.cfg.blacklist_role and self.cfg.blacklist_role in member.roles
			or self.cfg.whitelist_role and self.cfg.whitelist_role not in member.roles
		):
			raise bot.Exc.PermissionError(
				self.qc.gt("You are not allowed to add to {queues} queues.".format(queues=self.name))
			)

	async def add_member(self, ctx, member):
		if (
			self.cfg.blacklist_role and self.cfg.blacklist_role in member.roles
			or self.cfg.whitelist_role and self.cfg.whitelist_role not in member.roles
		):
			return bot.Qr.NotAllowed

		# ── Standby pool ─────────────────────────────────────────────────────
		# Standby only applies during the CHECK_IN phase — that's the only
		# stage where late joiners can still claim a slot. Once a match reaches
		# DRAFT or WAITING_REPORT, new adders go straight into the normal queue
		# so they're ready for the next match.
		active_match = next(
			(
				m for m in bot.active_matches
				if m.queue.id == self.id and m.state == m.CHECK_IN
			),
			None
		)
		log.info(
			f"[add_member] queue={self.name} member={member.display_name} "
			f"active_match_in_checkin={active_match.id if active_match else None} "
			f"standby_before={[m.display_name for m in self.standby]}"
		)
		if active_match is not None:
			if member in self.queue or member in self.standby or member in active_match.players:
				log.info(f"[add_member] {member.display_name} already present, returning Duplicate")
				return bot.Qr.Duplicate
			self.standby.append(member)
			log.info(f"[add_member] {member.display_name} added to standby (now {len(self.standby)} players)")
			return bot.Qr.Success

		# If there's no check-in but standby has stale entries (e.g. from a
		# previous match that already finished), flush them back into the queue
		# proper so they can join the next match normally.
		if self.standby:
			flushed = list(self.standby)
			self.standby = []
			for p in flushed:
				if p not in self.queue and len(self.queue) < self.cfg.size:
					self.queue.append(p)
			log.info(f"[add_member] flushed {len(flushed)} stale standby players back into queue")

		if len(self.queue) >= self.cfg.size:
			return bot.Qr.QueueFull

		if member not in self.queue:
			self.queue.append(member)
			if self not in bot.active_queues:
				bot.active_queues.append(self)
			if len(self.queue) == self.cfg.size and self.cfg.autostart:
				await self.start(ctx)
				return bot.Qr.QueueStarted
			return bot.Qr.Success
		else:
			return bot.Qr.Duplicate

	def is_added(self, member):
		return member in self.queue or member in self.standby

	def pop_members(self, *members):
		ids = [m.id for m in members]
		in_queue = [member for member in self.queue if member.id in ids]
		in_standby = [member for member in self.standby if member.id in ids]
		for m in in_queue:
			self.queue.remove(m)
		for m in in_standby:
			self.standby.remove(m)
		return in_queue + in_standby

	async def start(self, ctx):
		if len(self.queue) < 2:
			raise bot.Exc.PubobotException(self.qc.gt("Not enough players to start the queue."))

		player_ids = {p.id for p in self.queue}

		# ── Save states that survive match start ──────────────────────────────
		# allow_offline / auto_ready are cleared by remove_players() inside
		# queue_started(); we restore them after Match.new() so they persist
		# through the match (and through a failed check-in).
		saved_offline    = {uid for uid in bot.allow_offline if uid in player_ids}
		saved_auto_ready = {uid: t for uid, t in bot.auto_ready.items() if uid in player_ids}

		# ── Queue-priority protection ─────────────────────────────────────────
		# A player who is also in a queue with STRICTLY HIGHER priority than this
		# one is protected: they must NOT be removed from any queue when this
		# queue starts, and must not trigger the "removed from all queues"
		# message. Priority protection is permanent — they stay queued in the
		# higher-priority queue through and beyond this match.
		# We collect those user-IDs and pass them as `exclude` so the removal
		# chain (queue_started → remove_players → remove_members) skips them
		# entirely. This replaces the old remove-everyone-then-readd approach,
		# which briefly removed them and fired the misleading message.
		my_priority = getattr(self.cfg, 'priority', None) or 0
		protected_ids = set()
		for qc in bot.queue_channels.values():
			for q in qc.queues:
				if q is self:
					continue
				q_priority = getattr(q.cfg, 'priority', None) or 0
				if q_priority > my_priority:
					for p in self.queue:
						if q.is_added(p):
							protected_ids.add(p.id)

		players = list(self.queue)
		dm_text = self.cfg.start_direct_msg or self.qc.gt("**{queue}** pickup has started @ {channel}!")
		await self.qc.queue_started(
			ctx,
			members=players,
			message=dm_text.format_map(SafeTemplateDict(
				queue=self.name,
				channel=ctx.channel.mention,
				server=self.cfg.server
			)),
			exclude=protected_ids
		)

		if self.cfg.team_size:
			team_size = min(int(self.cfg.size / 2), int(self.cfg.team_size))
		else:
			team_size = int(self.cfg.size / 2)

		await bot.Match.new(ctx, self, players, team_size=team_size, **self._match_cfg())

		# ── Restore states after match creation ───────────────────────────────
		for uid in saved_offline:
			if uid not in bot.allow_offline:
				bot.allow_offline.append(uid)
		bot.auto_ready.update(saved_auto_ready)

	async def split(self, ctx, group_size: int = None, sort_by_rating: bool = False):
		group_size = group_size or len(self.queue)//2
		if len(self.queue) < group_size or group_size < 2:
			raise bot.Exc.PubobotException(self.qc.gt("Not enough players to start the queue."))
		if sort_by_rating:
			ratings = {p['user_id']: p['rating'] for p in await ctx.qc.rating.get_players((p.id for p in self.queue))}
			self.queue = sorted(self.queue, key=lambda p: ratings[p.id], reverse=True)
		groups = [self.queue[i-group_size:i] for i in range(group_size, len(self.queue)+1, group_size)]
		for group in groups:
			dm_text = self.cfg.start_direct_msg or self.qc.gt("**{queue}** pickup has started @ {channel}!")
			await self.qc.queue_started(
				ctx,
				members=group,
				message=dm_text.format_map(SafeTemplateDict(
					queue=self.name,
					channel=ctx.channel.mention,
					server=self.cfg.server
				))
			)
			await bot.Match.new(ctx, self, group, team_size=group_size//2, **self._match_cfg())

	async def fake_ranked_match(self, ctx, winners, losers, draw=False):
		if not self.cfg.ranked:
			raise bot.Exc.ValueError("Specified queue is not ranked.")
		await bot.Match.fake_ranked_match(
			ctx, self, self.qc, winners, losers, draw=draw,
			team_names=self.cfg.team_names.split(" ") if self.cfg.team_names else None,
		)

	async def revert(self, ctx, not_ready, ready):
		old_players = list(self.queue)

		# Standby players (who added during check-in/draft) fill slots first
		standby_players = list(self.standby)
		self.standby = []

		self.queue = list(ready)
		if self.cfg.autostart:
			# 1) Pull from standby first (the whole point of this feature)
			while len(self.queue) < self.cfg.size and len(standby_players):
				self.queue.append(standby_players.pop(0))
			# 2) Then fall back to anyone else who was already in queue
			while len(self.queue) < self.cfg.size and len(old_players):
				self.queue.append(old_players.pop(0))

			if len(self.queue) >= self.cfg.size:
				await self.start(ctx)
				# Anyone left over goes back to a normal queued state
				self.queue = list(old_players) + list(standby_players)
			else:
				# Didn't fill — leftover standby + old_players stay queued
				self.queue.extend(standby_players)
				for p in ready:
					await self.qc.update_expire(p)
		else:
			self.queue = list(ready) + standby_players + old_players
			for p in ready:
				await self.qc.update_expire(p)

		await ctx.notice(self.qc.topic)
		if self not in bot.active_queues and self.length:
			bot.active_queues.append(self)
