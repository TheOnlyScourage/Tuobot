from __future__ import annotations

from typing import Callable  # noqa: UP035
from asyncio import wait_for, shield
from asyncio.exceptions import TimeoutError as aTimeoutError
from nextcord.errors import InteractionResponded
from nextcord import Interaction, SlashOption, Member, TextChannel
import traceback
import time

from core.client import dc
from core.utils import error_embed, ok_embed, parse_duration, get_nick
from core.console import log
from core.config import cfg

import bot

from . import SlashContext, autocomplete, groups


guild_kwargs = dict(guild_ids=cfg.DC_SLASH_SERVERS) if len(cfg.DC_SLASH_SERVERS) else dict()


def _parse_duration(ctx: SlashContext, s: str):
	try:
		return parse_duration(s)
	except ValueError:
		raise bot.Exc.SyntaxError(ctx.qc.gt("Invalid duration format. Syntax: 3h2m1s or 03:02:01."))


async def run_slash(coro: Callable, interaction: Interaction, **kwargs) -> None:
	passed_time = time.time() - (((int(interaction.id) >> 22) + 1420070400000) / 1000.0)

	if passed_time >= 3.0:
		log.error('Skipping an outdated interaction.')
		return

	if not bot.bot_ready:
		await interaction.response.send_message(
			embed=error_embed("Bot is under connection, please try agian later...", title="Error")
		)
		return
	qc = bot.queue_channels.get(interaction.channel_id)
	if qc is None:
		await interaction.response.send_message(embed=error_embed("Not in a queue channel.", title="Error"))
		return

	ctx = SlashContext(qc, interaction)
	try:
		await wait_for(shield(run_slash_coro(ctx, coro, **kwargs)), timeout=max(2.5 - passed_time, 0))
	except (TimeoutError, aTimeoutError):
		# If the inner coro hasn\'t responded yet, defer so Discord stops waiting.
		# If it already responded (e.g. ctx.notice fired mid-flight), just log.
		try:
			await interaction.response.defer()
			log.info('Deferred /slash command')
		except InteractionResponded:
			log.info('Slow /slash command finished mid-timeout (already responded)')


async def run_slash_coro(ctx: SlashContext, coro: Callable, **kwargs) -> None:
	log.command("{} | #{} | {}: /{} {}".format(
		ctx.channel.guild.name, ctx.channel.name, get_nick(ctx.author), coro.__name__, kwargs
	))

	try:
		await coro(ctx, **kwargs)
	except bot.Exc.PubobotException as e:
		await ctx.error(str(e), title=e.__class__.__name__)
	except Exception as e:
		await ctx.error(str(e), title="RuntimeError")
		log.error("\n".join([
			f"Error processing /slash command {coro.__name__}.",
			f"QC: {ctx.channel.guild.name}>#{ctx.channel.name} ({ctx.qc.id}).",
			f"Member: {ctx.author} ({ctx.author.id}).",
			f"Kwargs: {kwargs}.",
			f"Exception: {str(e)}. Traceback:\n{traceback.format_exc()}=========="
		]))


# ── admin/queue ──────────────────────────────────────────────────────────────

@groups.admin_queue.subcommand(name='create_pickup', description='Create new pickup queue.')
async def _create_pickup(
	interaction: Interaction,
	name: str = SlashOption(name="name", description="Queue name."),
	size: int = SlashOption(name="size", description="Queue size.", required=False, default=8)
) -> None: await run_slash(bot.commands.create_pickup, interaction=interaction, name=name, size=size)


@groups.admin_queue.subcommand(name='list', description='List all queues on the channel.')
async def _show_queues(
	interaction: Interaction
) -> None: await run_slash(bot.commands.show_queues, interaction=interaction)


@groups.admin_queue.subcommand(name='show', description='Show a queue configuration.')
async def _cfg_queue(
		interaction: Interaction,
		queue: str
) -> None: await run_slash(bot.commands.cfg_queue, interaction=interaction, queue=queue)
_cfg_queue.on_autocomplete("queue")(autocomplete.queues)


@groups.admin_queue.subcommand(name='set', description='Configure a queue variable.')
async def _set_queue(
		interaction: Interaction,
		queue: str,
		variable: str,
		value: str
) -> None: await run_slash(bot.commands.set_queue, interaction=interaction, queue=queue, variable=variable, value=value)
_set_queue.on_autocomplete("queue")(autocomplete.queues)
_set_queue.on_autocomplete("variable")(autocomplete.queue_variables)


@groups.admin_queue.subcommand(name='delete', description='Delete a queue.')
async def _delete_queue(
	interaction: Interaction,
	queue: str = SlashOption(name="queue", description="Queue name.")
) -> None: await run_slash(bot.commands.delete_queue, interaction=interaction, queue=queue)
_delete_queue.on_autocomplete("queue")(autocomplete.queues)


@groups.admin_queue.subcommand(name='add_player', description='Add a player to a queue.')
async def _add_player(
	interaction: Interaction,
	player: Member = SlashOption(name="player", description="Member to add to the queue", verify=False),
	queue: str = SlashOption(name="queue", description="Queue to add to.")
) -> None: await run_slash(bot.commands.add_player, interaction=interaction, player=player, queue=queue)


@groups.admin_queue.subcommand(name='remove_player', description='Remove a player from queues.')
async def _remove_player(
	interaction: Interaction,
	player: Member = SlashOption(name="player", description="Member to remove from the queues", verify=False),
	queues: str = SlashOption(name="queues", description="Queues to remove the player from.", required=False)
) -> None: await run_slash(bot.commands.remove_player, interaction=interaction, player=player, queues=queues)


@groups.admin_queue.subcommand(name='clear', description='Remove players from the queues.')
async def _reset(
		interaction: Interaction,
		queue: str = SlashOption(name="queue", description="Only clear this queue.", required=False)
) -> None: await run_slash(bot.commands.reset, interaction=interaction, queue=queue)
_reset.on_autocomplete("queue")(autocomplete.queues)


@groups.admin_queue.subcommand(name='start', description='Start the queue.')
async def _start_queue(
	interaction: Interaction,
	queue: str
) -> None: await run_slash(bot.commands.start, interaction=interaction, queue=queue)
_start_queue.on_autocomplete("queue")(autocomplete.queues)


# ── admin/channel ─────────────────────────────────────────────────────────────

@groups.admin_channel.subcommand(name='enable', description='Enable the bot on this channel.')
async def enable_channel(interaction: Interaction) -> None:
	if not isinstance(interaction.channel, TextChannel):
		return await interaction.response.send_message(embed=error_embed('Must be used on a text channel.'), ephemeral=True)
	if not interaction.user.guild_permissions.administrator:
		return await interaction.response.send_message(embed=error_embed('You must possess server administrator permissions.'), ephemeral=True)
	if bot.queue_channels.get(interaction.channel_id) is not None:
		return await interaction.response.send_message(embed=error_embed('This channel is already enabled.'), ephemeral=True)
	await interaction.response.send_message(embed=ok_embed('The bot has been enabled.'))
	bot.queue_channels[interaction.channel.id] = await bot.QueueChannel.create(interaction.channel)


@groups.admin_channel.subcommand(name='disable', description='Disable the bot on this channel.')
async def disable_channel(interaction: Interaction) -> None:
	if not interaction.user.guild_permissions.administrator:
		return await interaction.response.send_message(embed=error_embed('You must possess server administrator permissions.'), ephemeral=True)
	if (qc := bot.queue_channels.get(interaction.channel_id)) is None:
		return await interaction.response.send_message(embed=error_embed('This channel is not enabled.'), ephemeral=True)
	bot.queue_channels.pop(qc.id)
	await interaction.response.send_message(embed=ok_embed('The bot has been disabled.'))


@groups.admin_channel.subcommand(name='delete', description='Delete stats/configs and disable the bot on this channel.')
async def delete_channel(interaction: Interaction) -> None:
	if not interaction.user.guild_permissions.administrator:
		return await interaction.response.send_message(embed=error_embed('You must possess server administrator permissions.'), ephemeral=True)
	if (qc := bot.queue_channels.get(interaction.channel_id)) is None:
		return await interaction.response.send_message(embed=error_embed('This channel is not enabled.'), ephemeral=True)
	for queue in qc.queues:
		await queue.cfg.delete()
	await qc.cfg.delete()
	bot.queue_channels.pop(qc.id)
	await interaction.response.send_message(embed=ok_embed('The bot has been disabled.'))


@groups.admin_channel.subcommand(name='show', description='List channel configuration.')
async def cfg_qc(interaction: Interaction
) -> None: await run_slash(bot.commands.cfg_qc, interaction=interaction)


@groups.admin_channel.subcommand(name='set', description='Configure a channel variable.')
async def _set_qc(
		interaction: Interaction,
		variable: str,
		value: str
) -> None: await run_slash(bot.commands.set_qc, interaction=interaction, variable=variable, value=value)
_set_qc.on_autocomplete("variable")(autocomplete.qc_variables)


# ── admin/match ───────────────────────────────────────────────────────────────

@groups.admin_match.subcommand(name='report', description='Report a match result as a moderator.')
async def _report_admin(
		interaction: Interaction,
		match_id: int,
		winner_team: str = SlashOption(required=False),
		abort: bool = SlashOption(required=False, default=False, description='Record the match as aborted (no winner, no rating change).')
) -> None: await run_slash(bot.commands.report_admin, interaction=interaction, match_id=match_id, winner_team=winner_team, abort=abort)
_report_admin.on_autocomplete('winner_team')(autocomplete.teams_by_match_id)
_report_admin.on_autocomplete('match_id')(autocomplete.match_ids)


@groups.admin_match.subcommand(name='create', description='Report a rating match manually.')
async def _report_manual(
		interaction: Interaction,
		queue: str,
		winners: str = SlashOption(description="List of won team players separated by space."),
		losers: str = SlashOption(description="List of lost team players separated by space."),
		aborted: bool = SlashOption(required=False, description='Record as an aborted match (no winner).')
) -> None:
	async def _run(ctx, *args, _winners, _losers, **kwargs):
		_winners = [await ctx.get_member(i) for i in _winners.split(" ")]
		_losers = [await ctx.get_member(i) for i in _losers.split(" ")]
		if None in _winners or None in _losers:
			raise bot.Exc.ValueError("Failed to parse teams arguments.")
		await bot.commands.report_manual(ctx, *args, winners=_winners, losers=_losers, **kwargs)
	await run_slash(_run, interaction=interaction, queue=queue, _winners=winners, _losers=losers, aborted=aborted)


@groups.admin_match.subcommand(name='sub_player', description='Sub a player out. If their team loses, the penalty goes to the original.')
async def _sub_force(
		interaction: Interaction,
		player1: Member = SlashOption(name="player1", description="Player being subbed out (takes loss penalty if team loses).", verify=False),
		player2: Member = SlashOption(name="player2", description="Player coming in as the fill-in.", verify=False),
) -> None: await run_slash(bot.commands.sub_force, interaction=interaction, player1=player1, player2=player2)


@dc.slash_command(name='swap', description='Swap two players (within match, queue, or bring an outsider in).', **guild_kwargs)
async def _swap_players(
		interaction: Interaction,
		player1: Member = SlashOption(name="player1", description="First player.", verify=False),
		player2: Member = SlashOption(name="player2", description="Second player.", verify=False),
) -> None: await run_slash(bot.commands.swap_players, interaction=interaction, player1=player1, player2=player2)



@groups.admin_match.subcommand(name='put', description='Put a player on Team A, Team B, or back to the unpicked pool.')
async def _put(
		interaction: Interaction,
		match_id: int,
		player: Member,
		team: str = SlashOption(
			name='team',
			description='Team A (1) / Team B (2) / Unpicked',
			choices=['Team A', 'Team B', 'Unpicked'],
		)
) -> None: await run_slash(bot.commands.put, interaction=interaction, match_id=match_id, player=player, team_name=team)
_put.on_autocomplete('match_id')(autocomplete.match_ids)


@groups.admin_match.subcommand(name='force_checkin', description='Force check in all players in a match (admin only).')
async def _force_checkin(
		interaction: Interaction,
		match_id: int = SlashOption(description="Match ID to force check in.")
) -> None: await run_slash(bot.commands.force_checkin, interaction=interaction, match_id=match_id)
_force_checkin.on_autocomplete('match_id')(autocomplete.match_ids)


# ── admin/noadds ──────────────────────────────────────────────────────────────

@groups.admin_noadds.subcommand(name='list', description='Show noadds list.')
async def _noadds(interaction: Interaction
) -> None: await run_slash(bot.commands.noadds, interaction=interaction)


@groups.admin_noadds.subcommand(name='add', description='Ban a player from participating in the queues.')
async def _noadd(
		interaction: Interaction,
		player: Member = SlashOption(verify=False),
		duration: str = SlashOption(required=False),
		reason: str = SlashOption(required=False)
) -> None:
	async def _run(ctx, *args, _duration=None, **kwargs):
		if _duration:
			_duration = _parse_duration(ctx, _duration)
		await bot.commands.noadd(ctx, *args, duration=_duration, **kwargs)
	await run_slash(_run, interaction=interaction, player=player, _duration=duration, reason=reason)


@groups.admin_noadds.subcommand(name='remove', description='Remove a player from the noadds list.')
async def _forgive(
		interaction: Interaction,
		player: Member = SlashOption(verify=False)
) -> None: await run_slash(bot.commands.forgive, interaction=interaction, player=player)


# ── admin/phrases ─────────────────────────────────────────────────────────────

@groups.admin_phrases.subcommand(name='add', description='Add a player phrase.')
async def _phrases_add(
		interaction: Interaction,
		player: Member = SlashOption(verify=False),
		phrase: str = SlashOption()
) -> None: await run_slash(bot.commands.phrases_add, interaction=interaction, player=player, phrase=phrase)


@groups.admin_phrases.subcommand(name='clear', description='Clear player phrases.')
async def _phrases_clear(
		interaction: Interaction,
		player: Member = SlashOption(verify=False),
) -> None: await run_slash(bot.commands.phrases_clear, interaction=interaction, player=player)


# ── admin/rating ──────────────────────────────────────────────────────────────

@groups.admin_rating.subcommand(name='seed', description='Set player rating and deviation')
async def _rating_seed(
		interaction: Interaction,
		player: str = SlashOption(verify=False),
		rating: int = SlashOption(),
		deviation: int = SlashOption(required=False)
) -> None: await run_slash(bot.commands.rating_seed, interaction=interaction, player=player, rating=rating, deviation=deviation)


@groups.admin_rating.subcommand(name='penality', description='Subtract points from player rating.')
async def _rating_penality(
		interaction: Interaction,
		player: str = SlashOption(verify=False),
		points: int = SlashOption(),
		reason: str = SlashOption(required=False)
) -> None: await run_slash(bot.commands.rating_penality, interaction=interaction, player=player, penality=points, reason=reason)


@groups.admin_rating.subcommand(name='hide_player', description='Hide player from the leaderboard.')
async def _rating_hide(
		interaction: Interaction,
		player: str = SlashOption(),
) -> None: await run_slash(bot.commands.rating_hide, interaction=interaction, player=player, hide=True)


@groups.admin_rating.subcommand(name='unhide_player', description='Unhide player from the leaderboard.')
async def _rating_unhide(
		interaction: Interaction,
		player: str = SlashOption(verify=False)
) -> None: await run_slash(bot.commands.rating_hide, interaction=interaction, player=player, hide=False)


@groups.admin_rating.subcommand(name='reset', description='Reset rating data on the channel.')
async def _rating_reset(interaction: Interaction
) -> None: await run_slash(bot.commands.rating_reset, interaction=interaction)


@groups.admin_rating.subcommand(name='snap', description='Snap players ratings to rank values.')
async def _rating_snap(interaction: Interaction
) -> None: await run_slash(bot.commands.rating_snap, interaction=interaction)


# ── admin/stats ───────────────────────────────────────────────────────────────

@groups.admin_stats.subcommand(name='show', description='Show channel or player stats.')
async def _stats(
		interaction: Interaction,
		player: Member = SlashOption(required=False, verify=False),
) -> None: await run_slash(bot.commands.stats, interaction=interaction, player=player)


@groups.admin_stats.subcommand(name='nuclear_option', description='☢️ FULL wipe: ratings AND all-time history. Owner-only. NO undo.')
async def _stats_nuclear_option(interaction: Interaction
) -> None: await run_slash(bot.commands.stats_nuclear_option, interaction=interaction)


@groups.admin_stats.subcommand(name='reset_player', description='Reset player stats.')
async def _stats_reset_player(
		interaction: Interaction,
		player: str = SlashOption(verify=False)
) -> None: await run_slash(bot.commands.stats_reset_player, interaction=interaction, player=player)


@groups.admin_stats.subcommand(name='stats_replace_player', description='Replace player1 with player2.')
async def _stats_replace_player(
		interaction: Interaction,
		player1: str = SlashOption(verify=False),
		player2: str = SlashOption(verify=False)
) -> None: await run_slash(bot.commands.stats_replace_player, interaction=interaction, player1=player1, player2=player2)


@groups.admin_stats.subcommand(name='undo_match', description='Undo a finished match.')
async def _stats_undo_match(
		interaction: Interaction,
		match_id: int
) -> None: await run_slash(bot.commands.undo_match, interaction=interaction, match_id=match_id)


# ── root commands ─────────────────────────────────────────────────────────────

@dc.slash_command(name='add', description='Add yourself to the channel queues.', **guild_kwargs)
async def _add(
	interaction: Interaction,
	queues: str = SlashOption(name="queues", description="Queues you want to add to.", required=False)
) -> None: await run_slash(bot.commands.add, interaction=interaction, queues=queues)
_add.on_autocomplete("queues")(autocomplete.queues)


@dc.slash_command(name='remove', description='Remove yourself from the channel queues.', **guild_kwargs)
async def _remove(
	interaction: Interaction,
	queues: str = SlashOption(name="queues", description="Queues you want to remove from.", required=False)
) -> None: await run_slash(bot.commands.remove, interaction=interaction, queues=queues)
_remove.on_autocomplete("queues")(autocomplete.queues)



@dc.slash_command(name='remove_all', description='Remove yourself (or a player) from all queues on this server.', **guild_kwargs)
async def _remove_all(
	interaction: Interaction,
	player: Member = SlashOption(required=False, verify=False, description="Player to remove (moderator only).")
) -> None: await run_slash(bot.commands.remove_all, interaction=interaction, player=player)


@dc.slash_command(name='remove_after', description='Auto-remove yourself from the channel queues after a set time.', **guild_kwargs)
async def _remove_after(
	interaction: Interaction,
	time: str = SlashOption(name="time", description="How long to stay in queue, e.g. 30m, 1h, 90s, 1:30:00.")
) -> None:
	async def _run(ctx, _duration=None):
		if _duration:
			_duration = _parse_duration(ctx, _duration)
		await bot.commands.expire(ctx, duration=_duration)
	await run_slash(_run, interaction=interaction, _duration=time)

@dc.slash_command(name='who', description='List added players.', **guild_kwargs)
async def _who(
	interaction: Interaction,
	queues: str = SlashOption(name="queues", description="Specify queues to list.", required=False)
) -> None: await run_slash(bot.commands.who, interaction=interaction, queues=queues)
_who.on_autocomplete("queues")(autocomplete.queues)


@dc.slash_command(name='server', description='Show queue server.', **guild_kwargs)
async def server(
		interaction: Interaction,
		queue: str
) -> None: await run_slash(bot.commands.server, interaction=interaction, queue=queue)
server.on_autocomplete("queue")(autocomplete.queues)


@dc.slash_command(name='matches', description='Show active matches on the channel.', **guild_kwargs)
async def _matches(interaction: Interaction
) -> None: await run_slash(bot.commands.show_matches, interaction=interaction)


@dc.slash_command(name='teams', description='Show teams on your current match.', **guild_kwargs)
async def _teams(interaction: Interaction
) -> None: await run_slash(bot.commands.show_teams, interaction=interaction)


@dc.slash_command(name='ready', description='Confirm participation during the check-in stage.', **guild_kwargs)
async def _ready(interaction: Interaction
) -> None: await run_slash(bot.commands.set_ready, interaction=interaction, is_ready=True)


@dc.slash_command(name='notready', description='Abort participation during the check-in stage.', **guild_kwargs)
async def _not_ready(interaction: Interaction
) -> None: await run_slash(bot.commands.set_ready, interaction=interaction, is_ready=False)


@dc.slash_command(name='subme', description='Request a substitute', **guild_kwargs)
async def _sub_me(interaction: Interaction
) -> None: await run_slash(bot.commands.sub_me, interaction=interaction)


@dc.slash_command(name='subauto', description='Replace yourself with the next player in queue and rebalance teams by ELO', **guild_kwargs)
async def _sub_auto(interaction: Interaction
) -> None: await run_slash(bot.commands.sub_auto, interaction=interaction)


@dc.slash_command(name='subfor', description='Become a substitute', **guild_kwargs)
async def _sub_for(
		interaction: Interaction,
		player: Member = SlashOption(name="player", description="The player to substitute for.", verify=False)
) -> None: await run_slash(bot.commands.sub_for, interaction=interaction, player=player)


@dc.slash_command(name='capme', description="Leave captain's position.")
async def _cap_me(interaction: Interaction,
) -> None: await run_slash(bot.commands.cap_me, interaction=interaction)


@dc.slash_command(name='capfor', description='Become a captain', **guild_kwargs)
async def _cap_for(
		interaction: Interaction,
		team: str
) -> None: await run_slash(bot.commands.cap_for, interaction=interaction, team_name=team)
_cap_for.on_autocomplete('team')(autocomplete.teams_by_author)


@dc.slash_command(name='pick', description='Pick a player from the unpicked pool.', **guild_kwargs)
async def _pick(
		interaction: Interaction,
		player: str = SlashOption(name="player", description="Pick from the unpicked pool.", required=True),
) -> None:
	# Resolve the player string (formatted as "<user_id>") back to a Member
	guild = interaction.guild
	member = None
	try:
		uid = int(player)
		member = guild.get_member(uid) if guild else None
	except (ValueError, TypeError):
		member = None

	if member is None:
		# Fall back to name-match against the active match\'s unpicked pool
		qc = bot.queue_channels.get(interaction.channel_id)
		if qc is not None:
			matches = [m for m in bot.active_matches if m.qc == qc and m.state == m.DRAFT]
			for m in matches:
				if len(m.teams) > 2:
					for p in m.teams[2]:
						if p.display_name.lower() == player.lower() or p.name.lower() == player.lower():
							member = p
							break
				if member:
					break

	if member is None:
		await interaction.response.send_message(
			embed=error_embed(f"Could not find player '{player}' in the unpicked pool."),
			ephemeral=True,
		)
		return

	await run_slash(bot.commands.pick, interaction=interaction, players=[member])


# Hook the new autocomplete helper for /pick — unpicked players only.
@_pick.on_autocomplete("player")
async def _pick_autocomplete(interaction: Interaction, current: str) -> None:
	choices = await autocomplete.unpicked_players(interaction, current)
	try:
		await interaction.response.send_autocomplete(choices)
	except Exception:
		pass


@dc.slash_command(name='report', description='Report match result.', **guild_kwargs)
async def _report(
		interaction: Interaction,
		result: str = SlashOption(choices=['loss', 'abort'])
) -> None: await run_slash(bot.commands.report, interaction=interaction, result=result)


@dc.slash_command(name='lastgame', description='Show last game details.', **guild_kwargs)
async def _last_game(
		interaction: Interaction,
		queue: str = SlashOption(required=False),
		player: Member = SlashOption(required=False, verify=False),
		match_id: int = SlashOption(required=False)
) -> None: await run_slash(bot.commands.last_game, interaction=interaction, queue=queue, player=player, match_id=match_id)
_last_game.on_autocomplete("queue")(autocomplete.queues)


@dc.slash_command(name='top', description='Show top players on the channel.', **guild_kwargs)
async def _top(
		interaction: Interaction,
		period: str = SlashOption(required=False, choices=['day', 'week', 'month', 'year']),
) -> None: await run_slash(bot.commands.top, interaction=interaction, period=period)


@dc.slash_command(name='rank', description='Show rating profile.', **guild_kwargs)
async def _rank(
		interaction: Interaction,
		player: Member = SlashOption(required=False, verify=False),
) -> None: await run_slash(bot.commands.rank, interaction=interaction, player=player)


@dc.slash_command(name='profile', description='Show a Q6 profile card for you or another player.', **guild_kwargs)
async def _profile(
		interaction: Interaction,
		player: Member = SlashOption(required=False, verify=False),
) -> None: await run_slash(bot.commands.profile, interaction=interaction, player=player)


@dc.slash_command(name='leaderboard', description='The leaderboard — 🔁 flips All Players ↔ Season (15+).', **guild_kwargs)
async def _leaderboard(
		interaction: Interaction,
		page: int = SlashOption(required=False, description="Page to open on (buttons flip pages)."),
) -> None: await run_slash(bot.commands.leaderboard, interaction=interaction, page=page)


@dc.slash_command(name='season_leaderboard', description='The merged leaderboard, opened on the Season (15+) view.', **guild_kwargs)
async def _season_leaderboard(
		interaction: Interaction,
		page: int = SlashOption(required=False, description="Page to open on (buttons flip pages)."),
		min_matches: int = SlashOption(required=False, default=15, description="Minimum matches to qualify (default 15).")
) -> None: await run_slash(bot.commands.season_leaderboard, interaction=interaction, page=page, min_matches=min_matches)



@groups.admin_stats.subcommand(name='season_end', description='End the season: post standings, disable ranked, reset stats.')
async def _season_end(
		interaction: Interaction,
		min_matches: int = SlashOption(required=False, default=15, description="Min matches to appear in standings (default 15).")
) -> None: await run_slash(bot.commands.season_end, interaction=interaction, min_matches=min_matches)


@groups.admin_stats.subcommand(name='season_start', description='Start a new season: enable ranked on all queues.')
async def _season_start(
		interaction: Interaction
) -> None: await run_slash(bot.commands.season_start, interaction=interaction)



@dc.slash_command(name='don', description='Don.', **guild_kwargs)
async def _don(interaction: Interaction) -> None:
	await interaction.response.send_message(
		"<@303565637786009603> <:L_Don:1517566869576355941>"
	)




@dc.slash_command(name='house_points', description='Show the Hogwarts House Cup standings.', **guild_kwargs)
async def _house_points(
		interaction: Interaction,
) -> None: await run_slash(bot.commands.house_points, interaction=interaction)


@groups.admin_stats.subcommand(name='house_points_reset', description='Reset all house point totals to zero.')
async def _house_points_reset(
		interaction: Interaction,
) -> None: await run_slash(bot.commands.house_points_reset, interaction=interaction)

# ── misc ──────────────────────────────────────────────────────────────────────

@dc.slash_command(name='activity', description='Show an activity heatmap (weekday × hour, IST).', **guild_kwargs)
async def _activity(
		interaction: Interaction,
		player: Member = SlashOption(required=False, verify=False)
) -> None: await run_slash(bot.commands.activity, interaction=interaction, player=player)


@dc.slash_command(name='auto_ready', description='Confirm next match check-in automatically.', **guild_kwargs)
async def _auto_ready(
		interaction: Interaction,
		duration: str = SlashOption(required=False, description="Duration e.g. 10m, 1h. Default is 10 minutes."),
) -> None:
	"""Ephemeral — only the user who ran it can see the response."""
	if not bot.bot_ready:
		return await interaction.response.send_message(
			embed=error_embed("Bot is under connection, please try again later."), ephemeral=True
		)
	qc = bot.queue_channels.get(interaction.channel_id)
	if qc is None:
		return await interaction.response.send_message(
			embed=error_embed("Not in a queue channel."), ephemeral=True
		)

	from datetime import timedelta as _td
	DEFAULT_SECS = 10 * 60  # 10 minutes

	if duration:
		try:
			dur_secs = parse_duration(duration).total_seconds()
		except ValueError:
			return await interaction.response.send_message(
				embed=error_embed("Invalid duration format. Try: 10m, 1h, 01:30:00."), ephemeral=True
			)
	else:
		dur_secs = DEFAULT_SECS

	max_ar = qc.cfg.max_auto_ready if qc.cfg.max_auto_ready else None
	if max_ar and dur_secs > max_ar:
		return await interaction.response.send_message(
			embed=error_embed(f"Auto ready limit is {str(_td(seconds=int(max_ar)))}. Use a shorter duration."),
			ephemeral=True
		)

	bot.auto_ready[interaction.user.id] = time.time() + dur_secs
	dur_str = str(_td(seconds=int(dur_secs)))
	await interaction.response.send_message(
		embed=ok_embed(f"During next **{dur_str}** your match participation will be confirmed automatically."),
		ephemeral=True
	)


@dc.slash_command(name='allow_offline', description='Switch your offline status immunity.', **guild_kwargs)
async def _allow_offline(interaction: Interaction) -> None:
	"""Ephemeral — only the user who ran it can see the response."""
	if not bot.bot_ready:
		return await interaction.response.send_message(
			embed=error_embed("Bot is under connection, please try again later."), ephemeral=True
		)
	qc = bot.queue_channels.get(interaction.channel_id)
	if qc is None:
		return await interaction.response.send_message(
			embed=error_embed("Not in a queue channel."), ephemeral=True
		)

	user_id = interaction.user.id
	if user_id in bot.allow_offline:
		bot.allow_offline.remove(user_id)
		msg = "Your offline immunity is **off**."
	else:
		bot.allow_offline.append(user_id)
		msg = "Your offline immunity is **on** until the next match."

	await interaction.response.send_message(embed=ok_embed(msg), ephemeral=True)


@dc.slash_command(name='switch_dms', description='Toggles DMs on queue start.', **guild_kwargs)
async def _switch_dms(interaction: Interaction,
) -> None: await run_slash(bot.commands.switch_dms, interaction=interaction)


@dc.slash_command(name='cointoss', description='Toss a coin.', **guild_kwargs)
async def _cointoss(
		interaction: Interaction,
		side: str = SlashOption(choices=['heads', 'tails'], required=False)
) -> None: await run_slash(bot.commands.cointoss, interaction=interaction, side=side)


@dc.slash_command(name='commands', description='Show commands list.', **guild_kwargs)
async def _commands(interaction: Interaction,
) -> None: await interaction.response.send_message(cfg.COMMANDS_URL, ephemeral=True)


@dc.slash_command(name='nick', description='Change your nickname with the rating prefix.', **guild_kwargs)
async def _nick(
		interaction: Interaction,
		nick: str
) -> None: await run_slash(bot.commands.set_nick, interaction=interaction, nick=nick)
