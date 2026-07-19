# -*- coding: utf-8 -*-
"""
End-of-season embeds: interesting stats pulled from match history, plus the
standalone House Cup announcement.

Called from /admin stats season_end. Match history is PERMANENT (the season
reset only clears qc_players), so every query here filters on
qc_matches.season — an unfiltered query would silently become all-time.
build_highlights_embed() and build_house_cup_embed() each return an embed
that gets posted to the channel alongside the standings.

Each query runs independently (see _safe) so one failing query can't take
down the whole highlights embed.
"""

from collections import Counter, defaultdict
from nextcord import Embed, Colour
from core.console import log
from core.database import db
from bot.constants import HOUSE_EMOJIS, SPECIALTY_ROLES as _SPECIALTY_ROLES


# Quidditch specialty role IDs (must match values used elsewhere).
# Specialty roles centralized in bot/constants.py (imported above).


def _member_role_set(member):
	"""Return the set of specialty role names a member holds, or empty set."""
	if member is None:
		return set()
	return {
		_SPECIALTY_ROLES[r.id]
		for r in getattr(member, 'roles', []) or []
		if r.id in _SPECIALTY_ROLES
	}


async def _query_most_active(channel_id: int, season: int):
	"""User_id with the most matches played this season."""
	return await db.fetchall(
		"SELECT pm.user_id, MAX(pm.nick) AS nick, COUNT(*) as games "
		"FROM qc_player_matches pm "
		"JOIN qc_matches m ON pm.match_id = m.match_id "
		"WHERE pm.channel_id=%s AND m.season=%s "
		"GROUP BY pm.user_id ORDER BY games DESC LIMIT 5",
		(channel_id, season)
	)


async def _query_most_wins(channel_id: int, season: int):
	"""User_id with the most match wins this season."""
	return await db.fetchall(
		"SELECT pm.user_id, MAX(pm.nick) AS nick, COUNT(*) as wins "
		"FROM qc_player_matches pm "
		"JOIN qc_matches m ON pm.match_id = m.match_id "
		"WHERE pm.channel_id=%s AND m.season=%s AND m.ranked=1 AND m.winner = pm.team "
		"GROUP BY pm.user_id ORDER BY wins DESC LIMIT 5",
		(channel_id, season)
	)


async def _query_best_duo(channel_id: int, season: int):
	"""Pair of players who won the most matches together on the same team."""
	# Pull every (match_id, user_id, team) row for ranked wins
	rows = await db.fetchall(
		"SELECT pm.match_id, pm.user_id, pm.team, pm.nick "
		"FROM qc_player_matches pm "
		"JOIN qc_matches m ON pm.match_id = m.match_id "
		"WHERE pm.channel_id=%s AND m.season=%s AND m.ranked=1 AND m.winner = pm.team",
		(channel_id, season)
	)

	# Group by (match_id, team) so each set is one winning lineup
	by_team = defaultdict(list)  # {(match_id, team): [(user_id, nick), ...]}
	for r in rows:
		by_team[(r['match_id'], r['team'])].append((r['user_id'], r['nick']))

	# Count co-occurrence of unordered pairs
	pair_wins = Counter()
	pair_nicks = {}  # {(uid1, uid2): (nick1, nick2)}
	for team in by_team.values():
		for i in range(len(team)):
			for j in range(i + 1, len(team)):
				a, b = sorted([team[i][0], team[j][0]])
				pair_wins[(a, b)] += 1
				if (a, b) not in pair_nicks:
					pair_nicks[(a, b)] = (
						team[i][1] if team[i][0] == a else team[j][1],
						team[j][1] if team[j][0] == b else team[i][1],
					)

	if not pair_wins:
		return None
	top_pair, count = pair_wins.most_common(1)[0]
	nick_a, nick_b = pair_nicks[top_pair]
	return dict(nick_a=nick_a, nick_b=nick_b, wins=count)


async def _query_most_improved(channel_id: int, season: int):
	"""Player whose rating climbed the most across the season (best comeback).

	Compares each player's current rating to the rating_before of their EARLIEST
	match-linked history row OF THIS SEASON — the history table is permanent
	now, so the season boundary comes from joining each history row's match to
	qc_matches.season. The min-games and non-null filtering is done in Python
	to avoid a fragile HAVING-on-alias clause.
	"""
	rows = await db.fetchall(
		"""
		SELECT
			p.user_id,
			p.nick,
			p.rating AS current_rating,
			(
				SELECT h.rating_before
				FROM qc_rating_history h
				JOIN qc_matches hm ON hm.match_id = h.match_id
				WHERE h.channel_id = p.channel_id
				  AND h.user_id    = p.user_id
				  AND hm.season    = %s
				ORDER BY h.id ASC LIMIT 1
			) AS first_rating,
			(p.wins + p.losses + p.draws) AS games
		FROM qc_players p
		WHERE p.channel_id = %s AND p.rating IS NOT NULL
		""",
		(season, channel_id)
	)
	rows = [r for r in rows if r['first_rating'] is not None and r['games'] >= 5]
	rows.sort(key=lambda r: (r['current_rating'] - r['first_rating']), reverse=True)
	return rows[:5]


async def _query_streaks(channel_id: int, season: int):
	"""Longest win streak and longest losing streak this season (ranked matches).

	Reconstructs each player's ordered W/L/D sequence from match history and
	tracks the longest consecutive run of each. A draw (or unresolved winner)
	breaks both streaks.
	"""
	rows = await db.fetchall(
		"SELECT pm.user_id, pm.nick, pm.team, m.winner, m.at, m.match_id "
		"FROM qc_player_matches pm "
		"JOIN qc_matches m ON pm.match_id = m.match_id "
		"WHERE pm.channel_id=%s AND m.season=%s AND m.ranked=1 "
		"ORDER BY m.at ASC, m.match_id ASC",
		(channel_id, season)
	)

	seq = defaultdict(list)   # user_id -> ['W'/'L'/'D', ...] in chronological order
	nick_of = {}              # user_id -> most recent nick
	for r in rows:
		nick_of[r['user_id']] = r['nick']
		w = r['winner']
		# winner-NULL rows are ABORTS (Q6 has no draws) — invisible to streaks.
		if w is None:
			continue
		res = 'W' if w == r['team'] else 'L'
		seq[r['user_id']].append(res)

	best_win = None    # dict(nick, streak)
	best_loss = None   # dict(nick, streak)
	for uid, results in seq.items():
		cur_w = cur_l = max_w = max_l = 0
		for res in results:
			if res == 'W':
				cur_w += 1
				cur_l = 0
			elif res == 'L':
				cur_l += 1
				cur_w = 0
			else:
				cur_w = 0
				cur_l = 0
			max_w = max(max_w, cur_w)
			max_l = max(max_l, cur_l)
		if max_w and (best_win is None or max_w > best_win['streak']):
			best_win = dict(nick=nick_of[uid], streak=max_w)
		if max_l and (best_loss is None or max_l > best_loss['streak']):
			best_loss = dict(nick=nick_of[uid], streak=max_l)

	return dict(win=best_win, loss=best_loss)


async def _query_role_winners(channel_id: int, season: int, guild):
	"""For each specialty role (Seeker/Beater/Keeper), find the player with
	the most ranked wins who holds that role on Discord right now."""
	if guild is None:
		return {}

	# Pull win counts per user_id (ranked only)
	rows = await db.fetchall(
		"SELECT pm.user_id, MAX(pm.nick) AS nick, COUNT(*) as wins "
		"FROM qc_player_matches pm "
		"JOIN qc_matches m ON pm.match_id = m.match_id "
		"WHERE pm.channel_id=%s AND m.season=%s AND m.ranked=1 AND m.winner = pm.team "
		"GROUP BY pm.user_id ORDER BY wins DESC",
		(channel_id, season)
	)

	# Walk in descending order; first match per role wins
	result = {}
	for row in rows:
		member = guild.get_member(row['user_id'])
		if member is None:
			continue
		for role_name in _member_role_set(member):
			if role_name not in result:
				result[role_name] = dict(nick=row['nick'], wins=row['wins'])
		if len(result) >= 3:
			break
	return result


async def _safe(label, fn, *args):
	"""Run one highlight query, logging and swallowing its error so a single
	failing query can't take down the whole highlights embed."""
	try:
		return await fn(*args)
	except Exception as e:
		log.error(f"[season_highlights] {label} query failed: {e}")
		return None


async def build_highlights_embed(ctx, season_num: int) -> Embed | None:
	"""Build and return the season-highlights embed. Returns None if every
	section is empty (or every query failed). The House Cup is posted
	separately via build_house_cup_embed()."""
	channel_id = ctx.qc.id
	guild      = ctx.channel.guild if hasattr(ctx, 'channel') else None

	active       = await _safe("most_active",   _query_most_active,   channel_id, season_num)
	winners      = await _safe("most_wins",     _query_most_wins,     channel_id, season_num)
	duo          = await _safe("best_duo",      _query_best_duo,      channel_id, season_num)
	improved     = await _safe("most_improved", _query_most_improved, channel_id, season_num)
	streaks      = await _safe("streaks",       _query_streaks,       channel_id, season_num)
	role_winners = await _safe("role_winners",  _query_role_winners,  channel_id, season_num, guild)

	lines = []

	if active:
		top = active[0]
		lines.append(f"\U0001f3c3 **Most Active** \u2014 `{top['nick']}` with **{top['games']}** matches")

	if winners:
		top = winners[0]
		lines.append(f"\U0001f3c6 **Most Wins** \u2014 `{top['nick']}` with **{top['wins']}** wins")

	if duo:
		lines.append(
			f"\U0001f465 **Best Duo** \u2014 `{duo['nick_a']}` & `{duo['nick_b']}` "
			f"won **{duo['wins']}** matches together"
		)

	if improved:
		top = improved[0]
		gain = top['current_rating'] - top['first_rating']
		if gain > 0:
			lines.append(
				f"\U0001f4c8 **Most Improved** \u2014 `{top['nick']}` "
				f"climbed **+{gain}** MMR ({top['first_rating']} \u2192 {top['current_rating']})"
			)

	if streaks and streaks.get('win') and streaks['win']['streak'] >= 3:
		w = streaks['win']
		lines.append(
			f"\U0001f525 **Longest Win Streak** \u2014 `{w['nick']}` "
			f"with **{w['streak']}** in a row"
		)

	if streaks and streaks.get('loss') and streaks['loss']['streak'] >= 3:
		losing = streaks['loss']
		lines.append(
			f"\U0001f9ca **Longest Losing Streak** \u2014 `{losing['nick']}` "
			f"with **{losing['streak']}** in a row"
		)

	if role_winners:
		role_icons = {'Seeker': '\U0001f441\ufe0f', 'Beater': '\U0001f3cf', 'Keeper': '\U0001f945'}
		for role in ('Seeker', 'Beater', 'Keeper'):
			if role in role_winners:
				w = role_winners[role]
				icon = role_icons.get(role, '\u2b50')
				lines.append(f"{icon} **Best {role}** \u2014 `{w['nick']}` with **{w['wins']}** wins")

	if not lines:
		return None

	return Embed(
		colour=Colour(0xe67e22),
		title=f"\u2728 Season {season_num} Highlights",
		description="\n".join(lines)
	)


async def build_house_cup_embed(season_num: int) -> Embed | None:
	"""Standalone House Cup announcement: the winning house with its emblem,
	plus the final standings of all four houses. Built from current totals, so
	it MUST be called before the season reset zeroes house points."""
	from bot.stats.house_points import get_standings
	try:
		standings = await get_standings()
	except Exception as e:
		log.error(f"[house_cup] get_standings failed: {e}")
		return None

	# Nothing to celebrate if no points were earned this season
	if not standings or all((h['points'] or 0) == 0 for h in standings):
		return None

	winner = standings[0]
	winner_emblem = HOUSE_EMOJIS.get(winner['house'], '')

	medals = ['\U0001f947', '\U0001f948', '\U0001f949']   # gold / silver / bronze
	rows = []
	for i, h in enumerate(standings):
		place = medals[i] if i < len(medals) else '\u2003'   # em-space aligns 4th place
		emblem = HOUSE_EMOJIS.get(h['house'], '')
		rows.append(f"{place} {emblem} **{h['house']}** \u2014 {h['points']} points")

	return Embed(
		colour=Colour(0xf1c40f),   # gold
		title=f"\U0001f3c6 The House Cup \u2014 Season {season_num}",
		description=(
			f"Congratulations to {winner_emblem} **{winner['house']}**, "
			f"champions of Season {season_num} with **{winner['points']}** points!\n\n"
			f"**Final Standings**\n" + "\n".join(rows)
		)
	)
