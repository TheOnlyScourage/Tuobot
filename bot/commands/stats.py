from __future__ import annotations

__all__ = ['last_game', 'stats', 'top', 'rank', 'profile', 'leaderboard', 'activity', 'season_leaderboard', 'season_end', 'season_start', 'house_points', 'house_points_reset']

import io
import asyncio
from time import time
import re
from nextcord import Member, Embed, Colour, File

from core.utils import get, find, seconds_to_str, get_nick, discord_table  # noqa: F401
from core.database import db
from core.console import log

import bot
from bot.commands.views import LeaderboardView
from bot.constants import HOUSE_ROLES, HOUSE_EMOJIS

# Custom rank emojis — must match match.py
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


def get_rank_emoji(rating: int) -> str:
	"""Return the rank emoji for a rating (highest threshold at or below it)."""
	emoji = RANK_EMOJIS[0][1]
	for threshold, e in RANK_EMOJIS:
		if rating >= threshold:
			emoji = e
	return emoji


# ── Leaderboard table helpers ─────────────────────────────────────────────────
_NON_ASCII = re.compile(r'[^\x20-\x7E]')  # matches emoji and non-ASCII

def _table_nick(nick: str, maxlen: int = 18) -> str:
	"""Strip emoji / non-ASCII chars and truncate for monospace table alignment."""
	return _NON_ASCII.sub('', nick).strip()[:maxlen]


def _lb_table(data: list, page: int, highlight_uid: int | None = None) -> str:
	"""Build the monospace leaderboard body (header + one row per player) for a page.

	`highlight_uid` marks that player's row (► prefix + bold) — used by the
	view's 🔍 Me button."""
	header = f"`{'No':>2}  {'Nickname':<18} {'W-L':<8} {'WR':>6}`"
	rows = []
	for i, row in enumerate(data):
		pos   = (page * 12) + i + 1
		nick  = _table_nick(row["nick"])
		w, losses = row["wins"], row["losses"]
		wr    = int(w * 100 / ((w + losses) or 1))
		wl    = f"{w}-{losses}"
		emoji = get_rank_emoji(row["rating"])
		text  = f"`{pos:>2}  {nick:<18} {wl:<8} ({wr:>3}%)`"
		if highlight_uid is not None and row.get("user_id") == highlight_uid:
			rows.append(f"► **{text}**  {emoji} **{row['rating']}**")
		else:
			rows.append(f"{text}  {emoji} {row['rating']}")
	return header + "\n\u2014\n" + "\n".join(rows)


def _lb_embed_maker(title_prefix: str):
	"""Return a make_embed(data, page, pages, highlight_uid) closure for
	LeaderboardView, so both leaderboard commands share one embed shape."""
	def make_embed(data: list, page: int, pages: int, highlight_uid: int | None = None) -> Embed:
		return Embed(
			title=f"🏆 {title_prefix} — page {page + 1} of {pages}",
			description=_lb_table(data, page, highlight_uid),
			colour=Colour(0x7289DA)
		)
	return make_embed



async def last_game(ctx: bot.Context, queue: str | None = None, player: Member | None = None, match_id: int | None = None) -> None:
	"""Show the most recent match, optionally filtered by queue, player, or match id."""
	lg = None

	if match_id:
		lg = await db.select_one(
			['*'], "qc_matches", where=dict(channel_id=ctx.qc.id, match_id=match_id), order_by="match_id", limit=1
		)
	elif queue:
		if queue := find(lambda q: q.name.lower() == queue.lower(), ctx.qc.queues):
			lg = await db.select_one(
				['*'], "qc_matches", where=dict(channel_id=ctx.qc.id, queue_id=queue.id), order_by="match_id", limit=1
			)
	elif player and (member := await ctx.get_member(player)) is not None:
		if match := await db.select_one(
			['match_id'], "qc_player_matches", where=dict(channel_id=ctx.qc.id, user_id=member.id),
			order_by="match_id", limit=1
		):
			lg = await db.select_one(
				['*'], "qc_matches", where=dict(channel_id=ctx.qc.id, match_id=match['match_id'])
			)
	else:
		lg = await db.select_one(
			['*'], "qc_matches", where=dict(channel_id=ctx.qc.id), order_by="match_id", limit=1
		)

	if not lg:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Nothing found"))

	players = await db.select(
		['user_id', 'nick', 'team'], "qc_player_matches",
		where=dict(match_id=lg['match_id'])
	)
	embed = Embed(colour=Colour(0x50e3c2))
	embed.add_field(name=lg['queue_name'], value=seconds_to_str(int(time()) - lg['at']) + " ago")
	if len(team := [p['nick'] for p in players if p['team'] == 0]):
		embed.add_field(name=lg['alpha_name'], value="`" + ", ".join(team) + "`")
	if len(team := [p['nick'] for p in players if p['team'] == 1]):
		embed.add_field(name=lg['beta_name'], value="`" + ", ".join(team) + "`")
	if len(team := [p['nick'] for p in players if p['team'] is None]):
		embed.add_field(name=ctx.qc.gt("Players"), value="`" + ", ".join(team) + "`")
	if lg['ranked']:
		if lg['winner'] is None:
			winner = ctx.qc.gt('Draw')
		else:
			winner = [lg['alpha_name'], lg['beta_name']][lg['winner']]
		embed.add_field(name=ctx.qc.gt("Winner"), value=winner)
	await ctx.reply(embed=embed)


async def stats(ctx: bot.Context, player: Member | None = None) -> None:
	"""Show match-count stats for a player or the whole channel."""
	if player:
		if (member := await ctx.get_member(player)) is not None:
			data = await bot.stats.user_stats(ctx.qc.id, member.id)
			target = get_nick(member)
		else:
			raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))
	else:
		data = await bot.stats.qc_stats(ctx.qc.id)
		target = f"#{ctx.channel.name}"

	embed = Embed(
		title=ctx.qc.gt("Stats for __{target}__").format(target=target),
		colour=Colour(0x50e3c2),
		description=ctx.qc.gt("**Total matches: {count}**").format(count=data['total'])
	)
	for q in data['queues']:
		embed.add_field(name=q['queue_name'], value=str(q['count']), inline=True)
	await ctx.reply(embed=embed)


async def top(ctx: bot.Context, period: str | None = None) -> None:
	"""Show the top 10 players by matches played, optionally within a time period."""
	if period in ["day", ctx.qc.gt("day")]:
		time_gap = int(time()) - (60 * 60 * 24)
	elif period in ["week", ctx.qc.gt("week")]:
		time_gap = int(time()) - (60 * 60 * 24 * 7)
	elif period in ["month", ctx.qc.gt("month")]:
		time_gap = int(time()) - (60 * 60 * 24 * 30)
	elif period in ["year", ctx.qc.gt("year")]:
		time_gap = int(time()) - (60 * 60 * 24 * 365)
	else:
		time_gap = None

	data = await bot.stats.top(ctx.qc.id, time_gap=time_gap)
	embed = Embed(
		title=ctx.qc.gt("Top 10 players for __{target}__").format(target=f"#{ctx.channel.name}"),
		colour=Colour(0x50e3c2),
		description=ctx.qc.gt("**Total matches: {count}**").format(count=data['total'])
	)
	for p in data['players']:
		embed.add_field(name=p['nick'], value=str(p['count']), inline=True)
	await ctx.reply(embed=embed)


async def rank(ctx: bot.Context, player: Member | None = None) -> None:
	"""Show a rank card for a player: place, record, rating, and recent changes."""
	target = ctx.author if not player else await ctx.get_member(player)
	if not target:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Specified user not found."))

	lb_data = await ctx.qc.get_lb()
	if p := find(lambda i: i['user_id'] == target.id, lb_data):
		place = lb_data.index(p) + 1
	else:
		all_players = await db.select(
			['user_id', 'rating', 'deviation', 'channel_id', 'wins', 'losses', 'draws', 'is_hidden', 'streak'],
			"qc_players", where={'channel_id': ctx.qc.rating.channel_id}
		)
		p = find(lambda i: i['user_id'] == target.id, all_players)
		place = "?"

	if not p:
		raise bot.Exc.ValueError(ctx.qc.gt("No rating data found."))

	rank_emoji  = get_rank_emoji(p['rating']) if p['rating'] else "❓"
	total_games = p['wins'] + p['losses'] + p['draws']
	wr          = int(p['wins'] * 100 / (total_games or 1))

	embed = Embed(title=f"__{get_nick(target)}__", colour=Colour(0x7289DA))

	# Row 1: No | Matches | Rank (emoji only — matching Q6Bot Image 3)
	embed.add_field(name="No",      value=f"**{place}**",       inline=True)
	embed.add_field(name="Matches", value=f"**{total_games}**", inline=True)
	embed.add_field(name="Rank",    value=rank_emoji,            inline=True)

	# Row 2: Rating | W/L/D/S | Winrate
	embed.add_field(name="Rating",
		value=f"**{p['rating']}**" if p['rating'] else "**?**", inline=True)
	embed.add_field(name="W/L/D/S",
		value=f"**{p['wins']}**/**{p['losses']}**/**{p['draws']}**/**{p['streak']}**", inline=True)
	embed.add_field(name="Winrate",
		value=f"**{wr}%**\n\u200b", inline=True)

	if target.display_avatar:
		embed.set_thumbnail(url=target.display_avatar.url)

	# Last changes — format: `+14`  19:08:14 ago  6v6-RANKED(001854)
	changes = await db.select(
		('at', 'rating_change', 'match_id', 'reason'),
		'qc_rating_history',
		where=dict(user_id=target.id, channel_id=ctx.qc.rating.channel_id),
		order_by='id', limit=5
	)
	if changes:
		now   = int(time())
		lines = []
		for c in changes:
			sign = "+" if c['rating_change'] >= 0 else ""
			ago  = seconds_to_str(int(now - c['at']))
			ref  = (f"{c['reason']}({str(c['match_id']).zfill(6)})"
					if c['match_id'] else c['reason'] or "manual")
			lines.append(f"`{sign}{c['rating_change']}`  {ago} ago  {ref}")
		embed.add_field(name="Last changes:", value="\n".join(lines), inline=False)

	await ctx.reply(embed=embed)


_house_emblem_cache: dict[str, bytes] = {}


async def _get_house_emblem(ctx: bot.Context, house: str) -> bytes | None:
	"""Fetch (and memory-cache) the house's custom-emoji image for the card
	watermark. Primary: pull the art straight off Discord's CDN by emoji id —
	the same machinery avatar fetches use, needing no cache, no guild, and no
	Emoji object. Secondary: the client-wide emoji cache. Every failure LOGS
	its reason ('[profile] ...') — no more silent letter fallbacks."""
	if house in _house_emblem_cache:
		return _house_emblem_cache[house]
	m = re.search(r'<a?:\w+:(\d+)>', HOUSE_EMOJIS.get(house, ''))
	if not m:
		log.error(f"[profile] no emoji id parsed for house {house!r}")
		return None
	emoji_id = m.group(1)

	# The Discord client lives in core/client.py — the bot package exposes
	# no `dc` attribute (verified the hard way, in production logs).
	try:
		from core.client import dc
	except Exception as e:
		log.error(f"[profile] could not import discord client: {e}")
		return None

	data = None
	try:
		data = await dc.http.get_from_cdn(
			f"https://cdn.discordapp.com/emojis/{emoji_id}.png?size=256"
		)
	except Exception as e:
		log.error(f"[profile] CDN emblem fetch failed for {house}: {e}")

	if data is None:
		try:
			emoji = dc.get_emoji(int(emoji_id))
			reader = getattr(emoji, 'read', None)
			if emoji is not None and reader is not None:
				data = await reader()
		except Exception as e:
			log.error(f"[profile] emoji-cache emblem read failed for {house}: {e}")

	if data:
		_house_emblem_cache[house] = data
	return data


async def profile(ctx: bot.Context, player: Member | None = None) -> None:
	"""Render the Q6 PNG profile card — ALL-TIME across seasons (unlike /rank,
	which is the current season): career W-L-D, peak rating, best-ever streak,
	a rating sparkline spanning seasons, most-teamed-with and nemesis. Current
	rating and streak show the player's live state."""
	target = ctx.author if not player else await ctx.get_member(player)
	if not target:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Specified user not found."))

	# Current season state (may be absent for a returning veteran who hasn't
	# played this season — that alone no longer blocks the all-time card).
	row = await db.select_one(
		('nick', 'rating', 'streak'),
		'qc_players',
		where={'channel_id': ctx.qc.rating.channel_id, 'user_id': target.id}
	)

	# Every match this player appeared in, chronologically — the source for
	# all-time W-L-D, best win streak, and the "Since <month>" footnote.
	result_rows = await db.fetchall(
		"SELECT m.winner, pm.team, m.ranked, m.at "
		"FROM qc_player_matches pm "
		"JOIN qc_matches m ON m.match_id = pm.match_id "
		"WHERE pm.channel_id = %s AND pm.user_id = %s AND pm.team IS NOT NULL "
		"ORDER BY pm.match_id",
		(ctx.qc.id, target.id)
	)
	if not result_rows and (not row or row['rating'] is None):
		raise bot.Exc.NotFoundError(ctx.qc.gt("No matches on record for this player yet — play a match first!"))

	# All-time rating trajectory. "ratings reset" rows are the artificial
	# season-boundary cliffs (rating -> init), so the sparkline and peak skip
	# them and show actual skill over time.
	hist_rows = await db.select(
		('rating_before', 'rating_change', 'reason'), 'qc_rating_history',
		where={'channel_id': ctx.qc.rating.channel_id, 'user_id': target.id},
		order_by='id', order_asc=True
	)
	hist_rows = [h for h in hist_rows if h['reason'] != 'ratings reset']
	history = []
	if hist_rows:
		history.append(hist_rows[0]['rating_before'] or 1500)
		for h in hist_rows:
			history.append((h['rating_before'] or 1500) + (h['rating_change'] or 0))
	peak = max(history) if history else None

	# Everyone this player has shared a lobby with, tagged same-team vs
	# opponent, with the match winner for the nemesis W-L.
	encounter_rows = await db.fetchall(
		"SELECT pm2.user_id AS other_id, pm2.nick AS other_nick, "
		"(pm2.team = pm1.team) AS same_team, m.winner, pm1.team AS my_team "
		"FROM qc_player_matches pm1 "
		"JOIN qc_player_matches pm2 ON pm2.match_id = pm1.match_id AND pm2.user_id != pm1.user_id "
		"JOIN qc_matches m ON m.match_id = pm1.match_id "
		"WHERE pm1.channel_id = %s AND pm1.user_id = %s "
		"AND pm1.team IS NOT NULL AND pm2.team IS NOT NULL "
		"ORDER BY pm1.match_id",
		(ctx.qc.id, target.id)
	)

	# Local imports: keep Pillow out of the module import path so a broken
	# image stack can only ever fail this command, not the bot.
	from bot.stats.profile_card import render_profile_card, aggregate_encounters, summarize_results
	from bot.match.captain_selection import get_quidditch_role

	career = summarize_results(result_rows)

	teammate, nemesis = aggregate_encounters(encounter_rows)
	if teammate:
		teammate = (_table_nick(teammate[0], 24) or "?", teammate[1])
	if nemesis:
		nemesis = (_table_nick(nemesis[0], 24) or "?", nemesis[1], nemesis[2])

	house = next((HOUSE_ROLES[r.id] for r in target.roles if r.id in HOUSE_ROLES), None)
	position = get_quidditch_role(target).capitalize()

	# Current rating headlines the card; a veteran without a rating this
	# season falls back to their all-time peak for the number and rank badge.
	rating = row['rating'] if row and row['rating'] is not None else (peak or 1500)
	streak = (row['streak'] or 0) if row else 0
	emoji = get_rank_emoji(rating)
	m = re.search(r'<:(?:Q6)?([A-Za-z]+):', emoji)
	rank_name = (m.group(1) if m else 'Unranked').capitalize()

	avatar_bytes = None
	try:
		avatar_bytes = await target.display_avatar.with_size(128).read()
	except Exception:
		pass

	emblem_bytes = await _get_house_emblem(ctx, house) if house else None

	footnote = None
	if career['first_at']:
		from datetime import datetime
		since = datetime.fromtimestamp(career['first_at']).strftime('%b %Y')
		ranked_total = career['wins'] + career['losses'] + career['draws']
		footnote = f"Since {since} • {ranked_total} ranked matches"

	png = render_profile_card(
		nick=_table_nick(get_nick(target), 32) or str(target.id),
		house=house, position=position, rank_name=rank_name,
		rating=rating, wins=career['wins'], losses=career['losses'],
		draws=career['draws'], streak=streak, peak=peak,
		best_streak=career['best_streak'], history=history,
		teammate=teammate, nemesis=nemesis,
		avatar_bytes=avatar_bytes, emblem_bytes=emblem_bytes, footnote=footnote,
	)
	await ctx.reply(file=File(io.BytesIO(png), filename=f"profile_{target.id}.png"))


async def leaderboard(ctx: bot.Context, page: int = 1) -> None:
	"""Show the rating leaderboard with button pagination (⏮ ◀ ▶ ⏭ + 🔍 Me)."""
	all_data = await ctx.qc.get_lb()
	if not len(all_data):
		raise bot.Exc.NotFoundError(ctx.qc.gt("Leaderboard is empty."))

	# `page` is just the starting page now; out-of-range values clamp instead
	# of erroring since the buttons make every page reachable anyway.
	view = LeaderboardView(
		data=all_data,
		make_embed=_lb_embed_maker("Leaderboard"),
		start_page=(page or 1) - 1,
	)
	await ctx.reply(embed=view.render(), view=view)
	await view.bind(ctx)

async def activity(ctx: bot.Context, player: Member | None = None) -> None:
	"""Render an activity heatmap (weekday x hour, last 28 days) for a player or the channel."""
	interaction = getattr(ctx, 'interaction', None)
	if interaction is not None and not interaction.response.is_done():
		await interaction.response.defer()

	target = None
	if player is not None and (target := await ctx.get_member(player)) is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))

	ts_from = int(time()) - 28 * 86400

	if target:
		rows = await db.fetchall(
			"""
			SELECT
				DAYOFWEEK(CONVERT_TZ(FROM_UNIXTIME(m.at), '+00:00', '+05:30')) AS dow,
				HOUR(CONVERT_TZ(FROM_UNIXTIME(m.at), '+00:00', '+05:30')) AS hr,
				COUNT(DISTINCT m.match_id) AS count
			FROM qc_matches m
			JOIN qc_player_matches pm ON pm.match_id = m.match_id AND pm.channel_id = m.channel_id
			WHERE m.channel_id = %s AND m.at >= %s AND pm.user_id = %s
			GROUP BY dow, hr
			""",
			[ctx.qc.id, ts_from, target.id]
		)
	else:
		rows = await db.fetchall(
			"""
			SELECT
				DAYOFWEEK(CONVERT_TZ(FROM_UNIXTIME(at), '+00:00', '+05:30')) AS dow,
				HOUR(CONVERT_TZ(FROM_UNIXTIME(at), '+00:00', '+05:30')) AS hr,
				COUNT(*) AS count
			FROM qc_matches
			WHERE channel_id = %s AND at >= %s
			GROUP BY dow, hr
			""",
			[ctx.qc.id, ts_from]
		)

	if not rows:
		raise bot.Exc.NotFoundError(ctx.qc.gt("No activity data yet."))

	def _to_idx(dow):
		return (int(dow) + 5) % 7

	grid = [[0] * 24 for _ in range(7)]
	for r in rows:
		grid[_to_idx(r['dow'])][int(r['hr'])] += int(r['count'])

	day_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

	def _render():
		from matplotlib.figure import Figure
		from matplotlib.backends.backend_agg import FigureCanvasAgg

		fig = Figure(figsize=(12, 4), dpi=120)
		FigureCanvasAgg(fig)
		ax = fig.add_subplot(111)
		im = ax.imshow(grid, aspect='auto', cmap='magma', origin='upper')
		ax.set_xticks(range(24))
		ax.set_xticklabels([f"{h:02d}" for h in range(24)], fontsize=8)
		ax.set_yticks(range(7))
		ax.set_yticklabels(day_labels)
		ax.set_xlabel('Hour of day (IST)')
		ax.set_ylabel('Day of week')
		scope = f" — {get_nick(target)}" if target else ""
		ax.set_title(f"Activity heatmap by weekday × hour (IST, last 28 days){scope}")
		max_v = max((max(row) for row in grid), default=0)
		threshold = max_v * 0.6
		for d in range(7):
			for h in range(24):
				v = grid[d][h]
				if v:
					ax.text(h, d, str(v), ha='center', va='center',
					        color='black' if v >= threshold else 'white', fontsize=6)
		fig.colorbar(im, ax=ax, label='Matches')
		fig.tight_layout()

		out = io.BytesIO()
		fig.savefig(out, format='png')
		out.seek(0)
		return out

	buf = await asyncio.to_thread(_render)
	await ctx.reply(file=File(fp=buf, filename='activity.png'))


async def season_leaderboard(ctx: bot.Context, page: int = 1, min_matches: int = 15) -> None:
	"""Button-paginated leaderboard showing only players with min_matches+ games."""
	all_data  = await ctx.qc.get_lb()
	qualified = [
		r for r in all_data
		if (r['wins'] + r['losses'] + r['draws']) >= min_matches
	]
	if not len(qualified):
		raise bot.Exc.NotFoundError(
			ctx.qc.gt(f"No players with {min_matches}+ matches found.")
		)

	view = LeaderboardView(
		data=qualified,
		make_embed=_lb_embed_maker(f"Season Leaderboard ({min_matches}+ games)"),
		start_page=(page or 1) - 1,
	)
	await ctx.reply(embed=view.render(), view=view)
	await view.bind(ctx)



SEASON_MEDALS = ['🥇', '🥈', '🥉']


async def season_end(ctx: bot.Context, min_matches: int = 15) -> None:
	"""End the current season: post standings, disable ranked, reset all stats."""
	ctx.check_perms(ctx.Perms.ADMIN)

	from bot.stats.season import get_current_season_number, record_season_end
	season_num = await get_current_season_number(ctx.qc.id)

	# Collect standings before reset
	all_data = await ctx.qc.get_lb()
	qualified = [
		r for r in all_data
		if (r['wins'] + r['losses'] + r['draws']) >= min_matches
	]

	# Total stats — filtered to THIS season: match history is permanent now,
	# so an unfiltered count would be all-time, not the season's.
	total_rated = len([r for r in all_data if r.get('rating') is not None])
	total_row = await db.fetchone(
		"SELECT COUNT(*) as cnt FROM qc_matches WHERE channel_id=%s AND season=%s",
		[ctx.qc.id, season_num]
	)
	total_matches = total_row['cnt'] if total_row else 0

	# Ranked queues that will be turned off
	ranked_queues = [q for q in ctx.qc.queues if q.cfg.ranked]

	# Build embed description
	lines = [
		f"**Season {season_num} — Final Standings**\n",
		f"{total_rated} rated players | {total_matches} total matches\n",
	]

	if qualified:
		lines.append(f"**Top {len(qualified)} ({min_matches}+ games)**")
		for i, row in enumerate(qualified):
			pos = SEASON_MEDALS[i] if i < 3 else f"{i + 1}."
			emoji = get_rank_emoji(row['rating'])
			w, losses = row['wins'], row['losses']
			lines.append(f"{pos} **{row['nick'].strip()}** — {emoji} {row['rating']} ({w}-{losses})")
	else:
		lines.append(f"*No players with {min_matches}+ matches this season.*")

	if ranked_queues:
		lines.append("\n**Ranked Turned Off**")
		for q in ranked_queues:
			lines.append(f"• {q.name}")

	lines.append(
		"\nRatings and stats have been reset. "
		"MMR is now off until `/season_start` is used here."
	)

	embed = Embed(colour=Colour(0x7289DA), description="\n".join(lines))
	await ctx.reply(embed=embed)

	# Turn off ranked on all previously ranked queues
	for q in ranked_queues:
		try:
			await q.cfg.update({'ranked': '0'})
		except Exception:
			pass

	# Build & post the highlights embed BEFORE the reset wipes the data
	try:
		from bot.stats.season_highlights import build_highlights_embed
		highlights = await build_highlights_embed(ctx, season_num)
		if highlights is not None:
			await ctx.channel.send(embed=highlights)
	except Exception as e:
		from core.console import log
		log.error(f"[season_highlights] failed to build/post embed: {e}")

	# Build & post the House Cup winner embed (also before the reset)
	try:
		from bot.stats.season_highlights import build_house_cup_embed
		house_cup = await build_house_cup_embed(season_num)
		if house_cup is not None:
			await ctx.channel.send(embed=house_cup)
	except Exception as e:
		from core.console import log
		log.error(f"[house_cup] failed to build/post embed: {e}")

	# Reset all ratings and match stats for this channel
	import bot
	await bot.stats.reset_channel(ctx.qc.id)

	# Reset house points for the new season (mirrors the MMR reset above)
	try:
		from bot.stats.house_points import reset_all
		await reset_all()
	except Exception as e:
		from core.console import log
		log.error(f"[house_points] season reset failed: {e}")

	# Record the season end
	await record_season_end(ctx.qc.id, season_num)


async def season_start(ctx: bot.Context) -> None:
	"""Start a new season: enable ranked on all queues and announce."""
	ctx.check_perms(ctx.Perms.ADMIN)

	from bot.stats.season import get_current_season_number
	season_num = await get_current_season_number(ctx.qc.id)

	# Turn ranked on for all queues in this channel
	enabled = []
	for q in ctx.qc.queues:
		if not q.cfg.ranked:
			try:
				await q.cfg.update({'ranked': '1'})
				enabled.append(q.name)
			except Exception:
				pass

	lines = [f"🏆 **Season {season_num} has started!**\n"]
	if enabled:
		lines.append("**Ranked Turned On**")
		for name in enabled:
			lines.append(f"• {name}")
	lines.append("\nGood luck to all players! MMR is now active.")

	embed = Embed(colour=Colour(0x27b75e), description="\n".join(lines))
	await ctx.reply(embed=embed)

async def house_points(ctx: bot.Context) -> None:
	"""Show the Hogwarts house standings."""
	from bot.stats.house_points import get_standings
	from bot.match.embeds import HOUSE_EMOJIS

	standings = await get_standings()

	medals = ["\U0001f947", "\U0001f948", "\U0001f949", "\u20074."]
	lines = []
	for i, row in enumerate(standings):
		rank = medals[i] if i < len(medals) else f"{i+1}."
		emoji = HOUSE_EMOJIS.get(row['house'], "")
		lines.append(f"{rank} {emoji} **{row['house']}** \u2014 **{row['points']}** points")

	embed = Embed(
		colour=Colour(0xf1c40f),
		title="\U0001f4dc Hogwarts House Cup Standings",
		description="\n".join(lines)
	)
	await ctx.reply(embed=embed)


async def house_points_reset(ctx: bot.Context) -> None:
	"""Admin: reset every house's points to zero."""
	ctx.check_perms(ctx.Perms.ADMIN)
	from bot.stats.house_points import reset_all
	await reset_all()
	await ctx.success("All house point totals have been reset to 0.", title="House Points Reset")
