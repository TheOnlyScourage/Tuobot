__all__ = [
	'show_matches', 'show_teams', 'set_ready', 'sub_me', 'sub_auto', 'sub_for', 'put',
	'sub_force', 'cap_me', 'cap_for', 'pick', 'report_admin', 'report', 'report_manual',
	'force_checkin', 'swap_players'
]

from nextcord import Member
from typing import List  # noqa: UP035
from functools import wraps

from core.utils import get, find, get_nick

import bot


def author_match(coro):
	@wraps(coro)
	async def wrapper(ctx, *args, **kwargs):
		if (match := find(lambda m: m.qc == ctx.qc and ctx.author in m.players, bot.active_matches)) is None:
			raise bot.Exc.NotFoundError(ctx.qc.gt("You are not in an active match."))
		return await coro(ctx, match, *args, **kwargs)
	return wrapper


async def show_matches(ctx):
	matches = [m for m in bot.active_matches if m.qc.id == ctx.qc.id]
	if len(matches):
		await ctx.reply("\n".join((m.print() for m in matches)))
	else:
		await ctx.reply(ctx.qc.gt("> no active matches"))


@author_match
async def show_teams(ctx, match: bot.Match):
	if match.state not in [bot.Match.DRAFT, bot.Match.WAITING_REPORT]:
		raise bot.Exc.MatchStateError('Match must be on draft or waiting report state.')
	await match.draft.print(ctx)


@author_match
async def set_ready(ctx, match: bot.Match, is_ready=True):
	await match.check_in.set_ready(ctx, ctx.author, is_ready)


@author_match
async def sub_me(ctx, match: bot.Match):
	await match.draft.sub_me(ctx, ctx.author)


@author_match
async def sub_auto(ctx, match: bot.Match):
	await match.draft.sub_auto(ctx, ctx.author)


async def sub_for(ctx, player: Member):
	if (match := find(lambda m: m.qc == ctx.qc and player in m.players, bot.active_matches)) is None:
		raise bot.Exc.NotInMatchError(ctx.qc.gt("Specified user is not in a match."))
	await ctx.qc.check_allowed_to_add(ctx, ctx.author, queue=match.queue)
	await match.draft.sub_for(ctx, player, ctx.author)


async def sub_force(ctx, player1: Member, player2: Member):
	"""Substitute player1 out for player2 in the active match.

	player2 is treated as a FILL-IN: if the team wins, player2 gets normal
	gains. If the team loses, the rating penalty is redirected to player1
	(the original who was subbed out), since they're the one who committed
	to the match.
	"""
	ctx.check_perms(ctx.Perms.MODERATOR)

	if (match := find(lambda m: m.qc == ctx.qc and player1 in m.players, bot.active_matches)) is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt(
			f"**{player1.display_name}** is not in any active match on this channel."
		))
	if any((player2 in m.players for m in bot.active_matches)):
		raise bot.Exc.InMatchError(ctx.qc.gt(
			f"**{player2.display_name}** is already in an active match."
		))

	# Capture player1\'s team index BEFORE the swap so we can redirect the
	# penalty to player1 if the team ends up losing.
	team_idx = next(
		(i for i, t in enumerate(match.teams[:2]) if player1 in t), None
	)

	await match.draft.sub_for(ctx, player1, player2, force=True)

	# Always register as fill-in: team loss → player1 takes the hit.
	if team_idx is not None:
		match.fill_subs[player2.id] = (player1.id, team_idx)


@author_match
async def cap_me(ctx, match: bot.Match):
	await match.draft.cap_me(ctx, ctx.author)


@author_match
async def cap_for(ctx, match: bot.Match, team_name: str):
	await match.draft.cap_for(ctx, ctx.author, team_name)


@author_match
async def pick(ctx, match: bot.Match, players: List[Member]):  # noqa: UP006
	await match.draft.pick(ctx, ctx.author, players)


async def put(ctx, match_id: int, player: Member, team_name: str):
	"""Put a player on Team A (teams[0]), Team B (teams[1]), or back to the unpicked pool.

	The slash command sends 'Team A', 'Team B', or 'Unpicked'. We translate
	into a team INDEX before forwarding so that duplicate Hogwarts house
	names (e.g. both teams renamed to Hufflepuff) can never be confused.
	"""
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (match := find(lambda m: m.qc == ctx.qc and m.id == match_id, bot.active_matches)) is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Could not find match with specified id. Check `/matches`."))

	# Resolve which target team / pool the player should end up in.
	if team_name == 'Team A':
		target_idx = 0
	elif team_name == 'Team B':
		target_idx = 1
	elif team_name.lower() == 'unpicked':
		target_idx = 2
	else:
		# Backwards-compat: try matching by name string
		target_idx = next(
			(i for i, t in enumerate(match.teams) if t.name.lower() == team_name.lower()),
			None
		)
		if target_idx is None:
			raise bot.Exc.SyntaxError(
				f"Unknown team '{team_name}'. Use Team A, Team B, or Unpicked."
			)

	# Direct manipulation — bypasses draft.put\'s name-lookup to guarantee
	# the right team is picked even when both teams share a Hogwarts name.
	target_team = match.teams[target_idx]

	# Player must already be in the match — check membership FIRST, then index,
	# otherwise .index() raises ValueError on teams the player isn\'t on.
	current_team = next((t for t in match.teams if player in t), None)
	if current_team is None:
		raise bot.Exc.NotFoundError(
			f"**{player.display_name}** is not in match {match_id}."
		)

	if current_team is target_team:
		await ctx.success(f"**{player.display_name}** is already in **{target_team.name}**.")
		return

	# Pull from old team, append to new team
	current_team.remove(player)
	target_team.append(player)

	# Refresh the draft / final embed so everyone sees the change
	if match.state == match.WAITING_REPORT:
		await ctx.notice(embed=match.embeds.final_message())
	elif match.state == match.DRAFT:
		await match.draft.print(ctx)

	label = 'Unpicked' if target_idx == 2 else f"Team {'AB'[target_idx]} ({target_team.name})"
	await ctx.success(f"Moved **{player.display_name}** to **{label}**.")


async def report_admin(ctx, match_id: int, winner_team=None, draw=False, abort=False):
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (match := find(lambda m: m.qc == ctx.qc and m.id == match_id, bot.active_matches)) is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Could not find match with specified id. Check `/matches`."))
	if winner_team is None and not draw and not abort:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Please specify a team name or draw."))
	if abort:
		await match.cancel(ctx)
	else:
		await match.report_win(ctx, winner_team, draw)


@author_match
async def report(ctx, match: bot.Match, result):
	if result == 'loss':
		await match.report_loss(ctx, ctx.author, draw_flag=False)
	elif result == 'draw':
		await match.report_loss(ctx, ctx.author, draw_flag=1)
	elif result == 'abort':
		await match.report_loss(ctx, ctx.author, draw_flag=2)
	else:
		raise bot.Exc.ValueError("Invalid result value.")


async def report_manual(ctx, queue: str, winners: List[Member], losers: List[Member], draw: bool = False):  # noqa: UP006
	ctx.check_perms(ctx.Perms.MODERATOR)
	if (q := find(lambda i: i.name.lower() == queue.lower(), ctx.qc.queues)) is None:
		raise bot.Exc.SyntaxError(f"Queue '{queue}' not found on the channel.")
	if any((winners.count(p) != 1 or p in losers for p in winners)):
		raise bot.Exc.ValueError(f"Teams can not contain duplicate players.")  # noqa: F541
	if any((losers.count(p) != 1 or p in winners for p in losers)):
		raise bot.Exc.ValueError(f"Teams can not contain duplicate players.")  # noqa: F541
	if not len(winners) or not len(losers):
		raise bot.Exc.ValueError(f"Teams can not be empty.")  # noqa: F541
	await q.fake_ranked_match(ctx, winners, losers, draw=draw)


async def force_checkin(ctx, match_id: int):
	"""Admin command: forcefully check in all players in a match's check-in phase."""
	ctx.check_perms(ctx.Perms.ADMIN)
	if (match := find(lambda m: m.qc == ctx.qc and m.id == match_id, bot.active_matches)) is None:
		raise bot.Exc.NotFoundError(ctx.qc.gt("Could not find match with specified id. Check `/matches`."))
	if match.state != bot.Match.CHECK_IN:
		raise bot.Exc.MatchStateError(ctx.qc.gt("Match is not in the check-in phase."))

	# Mark all players as ready and advance the match
	for player in match.players:
		match.check_in.ready_players.add(player)

	await match.check_in.refresh(bot.SystemContext(ctx.qc))
	await ctx.success(ctx.qc.gt("All players have been force checked in."))


async def swap_players(ctx, player1: Member, player2: Member):
	"""Swap two players. Auto-detects mode:

	Mode A — Both players in the same active match:
	   Swap their positions across teams (or move one in/out of unpicked pool).
	   No rating side-effects.

	Mode B — One player is in an active match, the other is NOT in any match
	   AND NOT in any queue:
	   Bring the outsider in, send the insider out. Clean swap, no penalty
	   redirect (use /match sub_player for the penalty-on-original semantics).

	Mode C — Neither is in any match (both are just queued/idle):
	   Swap their positions in the queue if both are queued, otherwise error.
	"""
	ctx.check_perms(ctx.Perms.MODERATOR)

	if player1.id == player2.id:
		raise bot.Exc.ValueError(ctx.qc.gt("Cannot swap a player with themselves."))

	# Resolve which active matches each player is in (channel-scoped)
	m1 = find(lambda m: m.qc == ctx.qc and player1 in m.players, bot.active_matches)
	m2 = find(lambda m: m.qc == ctx.qc and player2 in m.players, bot.active_matches)

	# ── Mode A: both in the same match → swap team positions ──────────────
	if m1 is not None and m2 is not None:
		if m1 is not m2:
			raise bot.Exc.ValueError(ctx.qc.gt(
				"Players are in different matches \u2014 swap not possible."
			))
		match = m1
		if match.state not in (match.DRAFT, match.WAITING_REPORT):
			raise bot.Exc.MatchStateError(ctx.qc.gt(
				"Swap is only available during the draft or waiting-report phase."
			))

		# Find each player\'s location across ALL team slots (incl. unpicked pool)
		loc1 = next(((t, t.index(player1)) for t in match.teams if player1 in t), None)
		loc2 = next(((t, t.index(player2)) for t in match.teams if player2 in t), None)
		if loc1 is None or loc2 is None:
			raise bot.Exc.NotFoundError(ctx.qc.gt(
				"Could not locate both players in the match."
			))
		if loc1[0] is loc2[0]:
			raise bot.Exc.ValueError(ctx.qc.gt(
				f"**{get_nick(player1)}** and **{get_nick(player2)}** are already on the same team."
			))

		t1, i1 = loc1
		t2, i2 = loc2
		t1[i1] = player2
		t2[i2] = player1

		if match.state == match.WAITING_REPORT:
			await ctx.notice(embed=match.embeds.final_message())
		else:
			await match.draft.print(ctx)

		await ctx.success(
			f"Swapped **{get_nick(player1)}** \u2194 **{get_nick(player2)}** within the match."
		)
		return

	# ── Mode B: one in a match, the other free → bring outsider in ────────
	if (m1 is None) != (m2 is None):
		match    = m1 or m2
		insider  = player1 if m1 is not None else player2
		outsider = player2 if m1 is not None else player1

		# Outsider must NOT be in any other match
		if any(outsider in m.players for m in bot.active_matches):
			raise bot.Exc.InMatchError(ctx.qc.gt(
				f"**{outsider.display_name}** is already in an active match."
			))

		# Pull outsider out of any queues so they don\'t double-add
		for q in ctx.qc.queues:
			if q.is_added(outsider):
				q.pop_members(outsider)

		# Find insider\'s slot in the match and replace
		loc = next(((t, t.index(insider)) for t in match.teams if insider in t), None)
		if loc is None:
			raise bot.Exc.NotFoundError(ctx.qc.gt(
				f"Could not locate **{get_nick(insider)}** in the match."
			))
		team_obj, idx = loc
		team_obj[idx] = outsider

		# Keep match.players in sync
		if insider in match.players:
			match.players[match.players.index(insider)] = outsider

		# Add the outsider\'s rating into the match dict so embeds don\'t KeyError
		if outsider.id not in match.ratings:
			try:
				rows = await match.qc.rating.get_players([outsider.id])
				if rows:
					match.ratings[outsider.id] = rows[0]['rating'] or 1500
			except Exception:
				match.ratings.setdefault(outsider.id, 1500)

		if match.state == match.WAITING_REPORT:
			await ctx.notice(embed=match.embeds.final_message())
		elif match.state == match.DRAFT:
			await match.draft.print(ctx)

		await ctx.success(
			f"Swapped **{get_nick(insider)}** \u2194 **{get_nick(outsider)}** "
			"(outside player brought in, no penalty redirect)."
		)
		return

	# ── Mode C: neither in a match → swap queue positions ─────────────────
	swapped_in_queue = False
	for q in ctx.qc.queues:
		if q.is_added(player1) and q.is_added(player2):
			i1 = q.queue.index(player1)
			i2 = q.queue.index(player2)
			q.queue[i1], q.queue[i2] = q.queue[i2], q.queue[i1]
			swapped_in_queue = True

	if swapped_in_queue:
		await ctx.success(
			f"Swapped queue positions of **{get_nick(player1)}** \u2194 **{get_nick(player2)}**."
		)
		return

	raise bot.Exc.NotFoundError(ctx.qc.gt(
		"Neither player is in an active match, and they\u2019re not both queued together. "
		"Nothing to swap."
	))
