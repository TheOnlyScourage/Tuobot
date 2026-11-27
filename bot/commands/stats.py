from __future__ import annotations

__all__ = ['last_game', 'stats', 'top', 'rank', 'profile', 'leaderboard', 'activity', 'season_leaderboard', 'season_end', 'season_start', 'house_points', 'house_points_reset']

import io
import asyncio
from datetime import datetime
from time import time
from zoneinfo import ZoneInfo
import re
from nextcord import Member, Embed, Colour, File

from core.utils import get, find, seconds_to_str, get_nick  # noqa: F401
from core.database import db
from core.console import log

import bot
from bot.commands.views import LeaderboardView
from bot.constants import HOUSE_ROLES, HOUSE_EMOJIS, get_rank_emoji, QUEUE_CLEAR_TIMEZONE

# The rank ladder lives ONLY in bot/constants.py (it used to be duplicated here
# and silently drifted from the real one).


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
			winner = ctx.qc.gt('Aborted')
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


def _top_board(players: list, total: int, span: str) -> str:
	"""Medalled monospace list with activity bars for /top — replaces the old
	wall of inline name/count fields. Bars scale to the busiest player."""
	medals = ['🥇', '🥈', '🥉']
	rows = players[:10]
	max_count = max((p['count'] for p in rows), default=1) or 1
	lines = [f"**{total}** total matches • {span}\n"]
	for i, p in enumerate(rows):
		rank = medals[i] if i < 3 else f"`{i + 1:>2}.`"
		bar = '▰' * max(1, round(p['count'] * 10 / max_count))
		lines.append(f"{rank} `{_table_nick(p['nick']):<18}` **{p['count']:>3}**  {bar}")
	return "\n".join(lines)


async def top(ctx: bot.Context, period: str | None = None) -> None:
	"""Top 10 players by matches played — medal list with activity bars,
	optionally within a time period (day / week / month / year)."""
	if period in ["day", ctx.qc.gt("day")]:
		time_gap, span = int(time()) - (60 * 60 * 24), "past day"
	elif period in ["week", ctx.qc.gt("week")]:
		time_gap, span = int(time()) - (60 * 60 * 24 * 7), "past week"
	elif period in ["month", ctx.qc.gt("month")]:
		time_gap, span = int(time()) - (60 * 60 * 24 * 30), "past month"
	elif period in ["year", ctx.qc.gt("year")]:
		time_gap, span = int(time()) - (60 * 60 * 24 * 365), "past year"
	else:
		time_gap, span = None, "all-time"

	data = await bot.stats.top(ctx.qc.id, time_gap=time_gap)
	if not data['players']:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Nothing found"))
	embed = Embed(
		title="🏆 " + ctx.qc.gt("Top 10 players for __{target}__").format(target=f"#{ctx.channel.name}"),
		colour=Colour(0x7289DA),
		description=_top_board(data['players'], data['total'], span)
	)
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
	# Aborts aren't games anyone won or lost — /leaderboard and /profile use
	# W/(W+L) too; counting aborts here made /rank disagree with both.
	wr          = int(p['wins'] * 100 / ((p['wins'] + p['losses']) or 1))

	embed = Embed(title=f"__{get_nick(target)}__", colour=Colour(0x7289DA))

	# Row 1: No | Matches | Rank (emoji only — matching Q6Bot Image 3)
	embed.add_field(name="No",      value=f"**{place}**",       inline=True)
	embed.add_field(name="Matches", value=f"**{total_games}**", inline=True)
	embed.add_field(name="Rank",    value=rank_emoji,            inline=True)

	# Row 2: Rating | W/L/D/S | Winrate
	embed.add_field(name="Rating",
		value=f"**{p['rating']}**" if p['rating'] else "**?**", inline=True)
	embed.add_field(name="W/L/A/S",
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

	ranked_total = career['wins'] + career['losses'] + career['draws']
	footnote = None
	if career['first_at']:
		since = datetime.fromtimestamp(career['first_at']).strftime('%b %Y')
		footnote = f"Since {since} • {ranked_total} ranked matches"

	png = render_profile_card(
		nick=_table_nick(get_nick(target), 32) or str(target.id),
		house=house, position=position, rank_name=rank_name,
		rating=rating, wins=career['wins'], losses=career['losses'],
		draws=career['draws'], streak=streak, peak=peak,
		best_streak=career['best_streak'], history=history,
		teammate=teammate, nemesis=nemesis,
		avatar_bytes=avatar_bytes, emblem_bytes=emblem_bytes, footnote=footnote,
		# Drives the milestone regalia (ring + badge). Never passed before, so
		# the card always saw 0 matches and the art has never shown.
		career_matches=ranked_total,
	)
	await ctx.reply(file=File(io.BytesIO(png), filename=f"profile_{target.id}.png"))


LB_QUALIFY_MIN = 15


def _build_lb_view(all_data: list, min_matches: int, page: int, start_on_season: bool) -> LeaderboardView:
	"""One view, two boards: All Players plus the season-qualified board
	(min_matches+ games), flipped with the 🔁 toggle. The season board — and
	the toggle itself — only exists once somebody qualifies, so early-season
	boards are simply the plain paginator. `page` is 1-based and clamps."""
	qualified = [
		r for r in all_data
		if (r['wins'] + r['losses'] + r['draws']) >= min_matches
	]
	has_season = len(qualified) > 0
	return LeaderboardView(
		data=all_data,
		make_embed=_lb_embed_maker("Leaderboard"),
		alt_data=qualified if has_season else None,
		alt_make_embed=_lb_embed_maker(f"Season Leaderboard ({min_matches}+ games)") if has_season else None,
		toggle_to_alt_label=f"Season ({min_matches}+)",
		toggle_to_main_label="All Players",
		not_found_alt=(
			f"You're not on the season board yet — it takes {min_matches}+ ranked "
			f"matches. Flip to All Players to find yourself!"
		),
		start_on_alt=start_on_season and has_season,
		start_page=(page or 1) - 1,
	)


async def leaderboard(ctx: bot.Context, page: int = 1) -> None:
	"""The merged leaderboard: ⏮ ◀ ▶ ⏭ pages, 🔍 Me, and a 🔁 toggle that
	flips between All Players and the season-qualified board."""
	all_data = await ctx.qc.get_lb()
	if not len(all_data):
		raise bot.Exc.NotFoundError(ctx.qc.gt("Leaderboard is empty."))

	view = _build_lb_view(all_data, LB_QUALIFY_MIN, page, start_on_season=False)
	await ctx.reply(embed=view.render(), view=view)
	await view.bind(ctx)

# Heatmap colours, dark → purple → coral → pale gold (the old matplotlib
# "magma" look). Empty hours get their own muted tile.
_HEAT_STOPS = (
	(0.00, (40, 22, 88)), (0.35, (113, 31, 129)), (0.65, (222, 73, 104)),
	(0.85, (254, 159, 109)), (1.00, (252, 253, 191)),
)
_HEAT_EMPTY = (40, 42, 54)
_WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def _heat_colour(t: float) -> tuple:
	for (t0, c0), (t1, c1) in zip(_HEAT_STOPS, _HEAT_STOPS[1:]):
		if t <= t1:
			f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
			return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
	return _HEAT_STOPS[-1][1]


def _render_activity_heatmap(grid: list, title: str) -> bytes:
	"""7 weekdays × 24 hours heatmap → PNG bytes, drawn with Pillow (already
	used for /profile). It used to be matplotlib, and matplotlib + numpy were
	~96 MB of the bot's installed packages for this one chart."""
	from PIL import Image, ImageDraw
	from bot.stats.profile_card import _FONT_DIR, _fonts

	regular, bold = _fonts(_FONT_DIR)
	f_title, f_label, f_cell = bold(22), regular(15), bold(13)
	cell_w, cell_h, gap = 46, 40, 3
	left, top, right, bottom = 64, 64, 24, 64
	width, height = left + 24 * cell_w + right, top + 7 * cell_h + bottom

	img = Image.new("RGB", (width, height), (24, 25, 28))
	draw = ImageDraw.Draw(img)
	draw.text((left, 22), title, font=f_title, fill=(235, 235, 240))

	peak = max((max(row) for row in grid), default=0) or 1
	for d, row in enumerate(grid):
		y = top + d * cell_h
		draw.text((left - 12, y + (cell_h - gap) / 2), _WEEKDAYS[d], font=f_label, fill=(200, 200, 210), anchor="rm")
		for h, v in enumerate(row):
			x = left + h * cell_w
			colour = _heat_colour(v / peak) if v else _HEAT_EMPTY
			draw.rounded_rectangle((x, y, x + cell_w - gap, y + cell_h - gap), radius=5, fill=colour)
			if v:
				luminance = 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2]
				draw.text(
					(x + (cell_w - gap) / 2, y + (cell_h - gap) / 2), str(v), font=f_cell,
					fill=(20, 20, 24) if luminance > 140 else (245, 245, 250), anchor="mm",
				)
	for h in range(24):
		draw.text(
			(left + h * cell_w + (cell_w - gap) / 2, top + 7 * cell_h + 8), f"{h:02d}",
			font=f_label, fill=(170, 170, 180), anchor="mt",
		)
	draw.text(
		(width - right, height - 12), f"brighter = more matches  ·  busiest hour: {peak}",
		font=f_label, fill=(150, 150, 160), anchor="rs",
	)
	out = io.BytesIO()
	img.save(out, format="PNG", optimize=True)
	return out.getvalue()


async def activity(ctx: bot.Context, player: Member | None = None) -> None:
	"""Render an activity heatmap (weekday × hour, last 28 days) for a player
	or the channel, on the community's clock — Central time, the same zone as
	the daily 4 AM clear. (It used to be India time, +05:30: a leftover from
	the original Indian bot.)"""
	# The heatmap takes a moment to draw: acknowledge first. Through ctx, so
	# it takes turns with run_slash's own 2.5 s defer (see SlashContext).
	if hasattr(ctx, 'defer'):
		await ctx.defer()

	target = None
	if player is not None and (target := await ctx.get_member(player)) is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Specified user not found."))

	ts_from = int(time()) - 28 * 86400
	# Raw timestamps, bucketed with zoneinfo below: MySQL's named-zone
	# CONVERT_TZ needs time-zone tables the server may not have, and a fixed
	# offset would be an hour off for half the year.
	if target:
		rows = await db.fetchall(
			"SELECT DISTINCT m.match_id, m.at FROM qc_matches m "
			"JOIN qc_player_matches pm ON pm.match_id = m.match_id AND pm.channel_id = m.channel_id "
			"WHERE m.channel_id = %s AND m.at >= %s AND pm.user_id = %s",
			[ctx.qc.id, ts_from, target.id]
		)
	else:
		rows = await db.fetchall(
			"SELECT at FROM qc_matches WHERE channel_id = %s AND at >= %s",
			[ctx.qc.id, ts_from]
		)
	if not rows:
		raise bot.Exc.NotFoundError(ctx.qc.gt("No activity data yet."))

	tz = ZoneInfo(QUEUE_CLEAR_TIMEZONE)
	grid = [[0] * 24 for _ in range(7)]  # [Mon..Sun][hour]
	for r in rows:
		local = datetime.fromtimestamp(int(r['at']), tz)
		grid[local.weekday()][local.hour] += 1

	scope = f" — {_table_nick(get_nick(target), 24)}" if target else ""
	title = f"Activity by weekday × hour (Central time, last 28 days){scope}"
	png = await asyncio.to_thread(_render_activity_heatmap, grid, title)
	await ctx.reply(file=File(fp=io.BytesIO(png), filename='activity.png'))


async def season_leaderboard(ctx: bot.Context, page: int = 1, min_matches: int = 15) -> None:
	"""The same merged board as /leaderboard, just opened on the Season view
	(min_matches+ games) — the 🔁 toggle flips back to All Players."""
	all_data = await ctx.qc.get_lb()
	if not any(
		(r['wins'] + r['losses'] + r['draws']) >= min_matches for r in all_data
	):
		raise bot.Exc.NotFoundError(
			ctx.qc.gt(f"No players with {min_matches}+ matches found.")
		)

	view = _build_lb_view(all_data, min_matches, page, start_on_season=True)
	await ctx.reply(embed=view.render(), view=view)
	await view.bind(ctx)



SEASON_MEDALS = ['🥇', '🥈', '🥉']


async def season_end(ctx: bot.Context, min_matches: int = 15) -> None:
	"""End the current season: post standings, disable ranked, reset all stats."""
	ctx.check_perms(ctx.Perms.ADMIN)

	from bot.stats.season import get_current_season_number, record_season_end
	season_num = await get_current_season_number(ctx.qc.id)

	# Ranked queues that will be turned off. None left means this season has
	# already been ended: a second run used to post an empty "Season N+1"
	# summary and record N+1 as over, so the next /season_start announced N+2.
	ranked_queues = [q for q in ctx.qc.queues if q.cfg.ranked]
	if not ranked_queues:
		raise bot.Exc.MatchStateError(ctx.qc.gt(
			"No queue here is ranked, so this season has already ended. "
			"Use `/stats season_start` to begin the next one."
		))

	# Standings straight from the season board. They used to come from the
	# leaderboard (get_lb), which also hides anyone inactive for 30 days and
	# anyone under the leaderboard's own minimum (40 by default) — so
	# qualified players were missing from the final standings.
	board = await db.select(
		['user_id', 'nick', 'rating', 'wins', 'losses', 'draws', 'is_hidden'],
		'qc_players', where={'channel_id': ctx.qc.rating.channel_id}, order_by='rating'
	)
	board = [r for r in board if r['rating'] is not None and not r['is_hidden']]
	qualified = [
		r for r in board
		if (r['wins'] + r['losses'] + r['draws']) >= min_matches
	]

	# Total stats — filtered to THIS season: match history is permanent now,
	# so an unfiltered count would be all-time, not the season's.
	total_rated = len(board)
	total_row = await db.fetchone(
		"SELECT COUNT(*) as cnt FROM qc_matches WHERE channel_id=%s AND season=%s",
		[ctx.qc.id, season_num]
	)
	total_matches = total_row['cnt'] if total_row else 0

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
			lines.append(f"{pos} **{(row['nick'] or '?').strip()}** — {emoji} {row['rating']} ({w}-{losses})")
	else:
		lines.append(f"*No players with {min_matches}+ matches this season.*")

	if ranked_queues:
		lines.append("\n**Ranked Turned Off**")
		for q in ranked_queues:
			lines.append(f"• {q.name}")

	lines.append(
		"\nRatings and stats have been reset. "
		"MMR is now off until `/stats season_start` is used here."
	)

	embed = Embed(colour=Colour(0x7289DA), description="\n".join(lines))
	await ctx.reply(embed=embed)

	# Turn off ranked on all previously ranked queues — loudly if one fails,
	# or a queue would quietly stay ranked into the off-season.
	failed = []
	for q in ranked_queues:
		try:
			await q.cfg.update({'ranked': '0'})
		except Exception as e:
			log.error(f"[season_end] could not turn off ranked on {q.name}: {e}")
			failed.append(q.name)
	if failed:
		await ctx.channel.send(ctx.qc.gt("⚠️ Couldn't turn ranked off on: {queues}. Check with `/queue show`.").format(
			queues=", ".join(failed)
		))

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
	enabled, failed = [], []
	for q in ctx.qc.queues:
		if not q.cfg.ranked:
			try:
				await q.cfg.update({'ranked': '1'})
				enabled.append(q.name)
			except Exception as e:
				log.error(f"[season_start] could not turn on ranked for {q.name}: {e}")
				failed.append(q.name)

	lines = [f"🏆 **Season {season_num} has started!**\n"]
	if enabled:
		lines.append("**Ranked Turned On**")
		for name in enabled:
			lines.append(f"• {name}")
	if failed:
		lines.append("\n⚠️ **Couldn't turn ranked on:** " + ", ".join(failed))
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
