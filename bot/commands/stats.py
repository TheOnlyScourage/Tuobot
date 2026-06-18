__all__ = ['last_game', 'stats', 'top', 'rank', 'leaderboard', 'activity', 'season_leaderboard', 'season_end', 'season_start']

import io
import asyncio
from time import time
import re
from math import ceil
from nextcord import Member, Embed, Colour, File

from core.utils import get, find, seconds_to_str, get_nick, discord_table  # noqa: F401
from core.database import db

import bot

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


def get_rank_emoji(rating):
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



async def last_game(ctx, queue: str = None, player: Member = None, match_id: int = None):
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


async def stats(ctx, player: Member = None):
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


async def top(ctx, period=None):
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


async def rank(ctx, player: Member = None):
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

async def leaderboard(ctx, page: int = 1):
	page = (page or 1) - 1

	all_data = await ctx.qc.get_lb()
	pages = ceil(len(all_data) / 12)
	data  = all_data[page * 12:(page + 1) * 12]
	if not len(data):
		raise bot.Exc.NotFoundError(ctx.qc.gt("Leaderboard is empty."))

	# Q6Bot-matching table: inline code spans for alignment + emoji outside
	header = f"`{'No':>2}  {'Nickname':<18} {'W-L':<8} {'WR':>6}`"
	rows   = []
	for i, row in enumerate(data):
		pos   = (page * 12) + i + 1
		nick  = _table_nick(row["nick"])
		w, l  = row["wins"], row["losses"]
		wr    = int(w * 100 / ((w + l) or 1))
		wl    = f"{w}-{l}"
		emoji = get_rank_emoji(row["rating"])
		text  = f"`{pos:>2}  {nick:<18} {wl:<8} ({wr:>3}%)`"
		rows.append(f"{text}  {emoji} {row['rating']}")

	embed = Embed(
		title=f"🏆 Leaderboard — page {page+1} of {max(pages, 1)}",
		description=header + "\n—\n" + "\n".join(rows),
		colour=Colour(0x7289DA)
	)
	await ctx.reply(embed=embed)

async def activity(ctx, player: Member = None):
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


async def season_leaderboard(ctx, page: int = 1, min_matches: int = 15):
	"""Leaderboard showing only players with min_matches or more games played."""
	page = (page or 1) - 1

	all_data  = await ctx.qc.get_lb()
	qualified = [
		r for r in all_data
		if (r['wins'] + r['losses'] + r['draws']) >= min_matches
	]

	pages = ceil(len(qualified) / 12)
	data  = qualified[page * 12:(page + 1) * 12]
	if not len(data):
		raise bot.Exc.NotFoundError(
			ctx.qc.gt(f"No players with {min_matches}+ matches found.")
		)

	# Q6Bot-matching table format
	header = f"`{'No':>2}  {'Nickname':<18} {'W-L':<8} {'WR':>6}`"
	rows   = []
	for i, row in enumerate(data):
		pos   = (page * 12) + i + 1
		nick  = _table_nick(row["nick"])
		w, l  = row["wins"], row["losses"]
		wr    = int(w * 100 / ((w + l) or 1))
		wl    = f"{w}-{l}"
		emoji = get_rank_emoji(row["rating"])
		text  = f"`{pos:>2}  {nick:<18} {wl:<8} ({wr:>3}%)`"
		rows.append(f"{text}  {emoji} {row['rating']}")

	embed = Embed(
		title=f"🏆 Season Leaderboard ({min_matches}+ games) — page {page+1} of {max(pages, 1)}",
		description=header + "\n—\n" + "\n".join(rows),
		colour=Colour(0x7289DA)
	)
	await ctx.reply(embed=embed)



SEASON_MEDALS = ['🥇', '🥈', '🥉']


async def season_end(ctx, min_matches: int = 15):
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

	# Total stats
	total_rated = len([r for r in all_data if r.get('rating') is not None])
	total_row = await db.fetchone(
		"SELECT COUNT(*) as cnt FROM qc_matches WHERE channel_id=%s",
		[ctx.qc.id]
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
			w, l = row['wins'], row['losses']
			lines.append(f"{pos} **{row['nick'].strip()}** — {emoji} {row['rating']} ({w}-{l})")
	else:
		lines.append(f"*No players with {min_matches}+ matches this season.*")

	if ranked_queues:
		lines.append("\n**Ranked Turned Off**")
		for q in ranked_queues:
			lines.append(f"• {q.name}")

	lines.append(
		f"\nRatings and stats have been reset. "
		f"MMR is now off until `/season_start` is used here."
	)

	embed = Embed(colour=Colour(0x7289DA), description="\n".join(lines))
	await ctx.reply(embed=embed)

	# Turn off ranked on all previously ranked queues
	for q in ranked_queues:
		try:
			await q.cfg.update({'ranked': '0'})
		except Exception:
			pass

	# Reset all ratings and match stats for this channel
	import bot
	await bot.stats.reset_channel(ctx.qc.id)

	# Record the season end
	await record_season_end(ctx.qc.id, season_num)


async def season_start(ctx):
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
