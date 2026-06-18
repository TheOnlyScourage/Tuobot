from nextcord import Embed, Colour, Streaming, Member
from core.client import dc
from core.utils import get_nick, join_and

# Rank emoji lookup — duplicated here to avoid circular import with match.py
_RANK_EMOJIS = [
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


def _rank_emoji(rating: int) -> str:
	emoji = _RANK_EMOJIS[0][1]
	for threshold, e in _RANK_EMOJIS:
		if rating >= threshold:
			emoji = e
	return emoji


class Embeds:
	""" This class generates discord embeds for various match states """

	def __init__(self, match):
		self.m = match
		self.footer = dict(
			text=f"Match id: {str(self.m.id).zfill(6)}",
			icon_url=dc.user.avatar.with_size(32).url if dc.user.avatar else None
		)

	def _player_line(self, p: Member) -> str:
		"""Return '{mention} {rank_emoji} 〈{rating}〉' — Q6Bot style player line."""
		rating = self.m.ratings.get(p.id)
		if rating is not None and self.m.ranked:
			return f"{p.mention} {_rank_emoji(rating)} 〈{rating}〉"
		return p.mention

	def _ranked_nick(self, p: Member):
		if self.m.ranked:
			if self.m.qc.cfg.emoji_ranks:
				return f'{self.m.rank_str(p)}`{get_nick(p)}`'
			return f'`{self.m.rank_str(p)}{get_nick(p)}`'
		return f'`{get_nick(p)}`'

	def _ranked_mention(self, p: Member):
		if self.m.ranked:
			if self.m.qc.cfg.emoji_ranks:
				return f'{self.m.rank_str(p)}{p.mention}'
			return f'`{self.m.rank_str(p)}`{p.mention}'
		return p.mention

	def check_in(self, not_ready):
		embed = Embed(
			colour=Colour(0xf5d858),
			title=self.m.gt("__**{queue}** is now on the check-in stage!__").format(
				queue=self.m.queue.name[0].upper()+self.m.queue.name[1:]
			)
		)
		embed.add_field(
			name=self.m.gt("Waiting on:"),
			value="\n".join((f" \u200b {'❌ ' if p in self.m.check_in.discarded_players else ''}<@{p.id}>" for p in not_ready)),
			inline=False
		)
		if not len(self.m.check_in.maps):
			embed.add_field(
				name="—",
				value=self.m.gt(
					"Please react with {ready_emoji} to **check-in** or {not_ready_emoji} to **abort**!").format(
					ready_emoji=self.m.check_in.READY_EMOJI, not_ready_emoji=self.m.check_in.NOT_READY_EMOJI
				) + "\n\u200b",
				inline=False
			)
		else:
			embed.add_field(
				name="—",
				value="\n".join([
					self.m.gt("Please react with {ready_emoji} or vote for a map to **check-in**.").format(
						ready_emoji=self.m.check_in.READY_EMOJI
					),
					self.m.gt("React with {not_ready_emoji} to **abort**!").format(
						not_ready_emoji=self.m.check_in.NOT_READY_EMOJI
					) + "\n\u200b\nMaps:",
					"\n".join([
						f" \u200b \u200b {self.m.check_in.INT_EMOJIS[i]} \u200b {self.m.check_in.maps[i]}"
						for i in range(len(self.m.check_in.maps))
					])
				]),
				inline=False
			)
		embed.set_footer(**self.footer)
		return embed

	def draft(self):
		embed = Embed(
			colour=Colour(0x8758f5),
			title=self.m.gt("__**{queue}** is now on the draft stage!__").format(
				queue=self.m.queue.name[0].upper()+self.m.queue.name[1:]
			)
		)

		teams_names = [
			f"{t.emoji} \u200b **{t.name}**" +
			(f" \u200b `〈{sum((self.m.ratings[p.id] for p in t))//(len(t) or 1)}〉`" if self.m.ranked else "")
			for t in self.m.teams[:2]
		]
		team_players = [
			" \u200b ".join([
				self._ranked_nick(p) for p in t
			]) if len(t) else self.m.gt("empty")
			for t in self.m.teams[:2]
		]
		embed.add_field(name=teams_names[0], value=" \u200b ❲ \u200b " + team_players[0] + " \u200b ❳", inline=False)
		embed.add_field(name=teams_names[1], value=" \u200b ❲ \u200b " + team_players[1] + " \u200b ❳\n\u200b", inline=False)

		if len(self.m.teams[2]):
			embed.add_field(
				name=self.m.gt("Unpicked:"),
				value="\n".join((
					" \u200b " + self._ranked_nick(p)
				) for p in self.m.teams[2]),
				inline=False
			)

			if len(self.m.teams[0]) and len(self.m.teams[1]):
				msg = self.m.gt("Pick players with `/pick @player` command.")
				pick_step = len(self.m.teams[0]) + len(self.m.teams[1]) - 2
				picker_team = self.m.teams[self.m.draft.pick_order[pick_step]] if pick_step < len(self.m.draft.pick_order)-1 else None
				if picker_team:
					msg += "\n" + self.m.gt("{member}'s turn to pick!").format(member=f"<@{picker_team[0].id}>")
			else:
				msg = self.m.gt("Type {cmd} to become a captain and start picking teams.").format(
					cmd=f"`{self.m.qc.cfg.prefix}capfor {'/'.join((team.name.lower() for team in self.m.teams[:2]))}`"
				)

			embed.add_field(name="—", value=msg + "\n\u200b", inline=False)

		embed.set_footer(**self.footer)
		return embed

	def final_message(self):
		# ── Title: ALL CAPS queue name (Q6Bot style) ───────────────────────────
		embed = Embed(
			colour=Colour(0x27b75e),
			title=f"{self.m.queue.name.upper()} has started!"
		)

		if len(self.m.teams[0]) == 1 and len(self.m.teams[1]) == 1:
			# ── 1v1 ────────────────────────────────────────────────────────────
			p1, p2 = self.m.teams[0][0], self.m.teams[1][0]
			embed.add_field(
				name=self.m.gt("Players"),
				value=self._player_line(p1) + "\n" + self._player_line(p2),
				inline=False
			)

		elif len(self.m.teams[0]):
			# ── Team vs Team ───────────────────────────────────────────────────
			teams_names = [
				f"{t.emoji} \u200b **{t.name}**" +
				(f" \u200b 〈{sum((self.m.ratings[p.id] for p in t))//(len(t) or 1)}〉" if self.m.ranked else "")
				for t in self.m.teams[:2]
			]
			team_players = [
				"\n".join(self._player_line(p) for p in t)
				for t in self.m.teams[:2]
			]
			team_players[1] += "\n\u200b"

			embed.add_field(name=teams_names[0], value=team_players[0], inline=False)
			embed.add_field(name=teams_names[1], value=team_players[1], inline=False)

			if self.m.ranked or self.m.cfg['pick_captains']:
				embed.add_field(
					name=self.m.gt("Captains"),
					value=" \u200b " + join_and([self.m.teams[0][0].mention, self.m.teams[1][0].mention]),
					inline=False
				)

		else:
			# ── Players list (no teams) ────────────────────────────────────────
			embed.add_field(
				name=self.m.gt("Players"),
				value="\n".join(self._player_line(p) for p in self.m.players),
				inline=False
			)
			if len(self.m.captains) and len(self.m.players) > 2:
				embed.add_field(
					name=self.m.gt("Captains"),
					value=" \u200b " + join_and([m.mention for m in self.m.captains]),
					inline=False
				)

		# ── Maps / Server ──────────────────────────────────────────────────────
		if len(self.m.maps):
			embed.add_field(
				name=self.m.qc.gt("Map" if len(self.m.maps) == 1 else "Maps"),
				value="\n".join((f"**{i}**" for i in self.m.maps)),
				inline=True
			)
		if self.m.cfg['server']:
			embed.add_field(
				name=self.m.qc.gt("Server"),
				value=f"`{self.m.cfg['server']}`",
				inline=True
			)

		# ── start_msg (set via /set_queue start_msg) ───────────────────────────
		if self.m.cfg['start_msg']:
			embed.add_field(name="—", value=self.m.cfg['start_msg'] + "\n\u200b", inline=False)

		# ── Streamers ──────────────────────────────────────────────────────────
		if self.m.cfg['show_streamers']:
			if len(streamers := [p for p in self.m.players if isinstance(p.activity, Streaming)]):
				embed.add_field(
					name=self.m.qc.gt("Player streams"),
					inline=False,
					value="\n".join([f"{p.mention}: {p.activity.url}" for p in streamers]) + "\n\u200b"
				)

		embed.set_footer(**self.footer)
		return embed
