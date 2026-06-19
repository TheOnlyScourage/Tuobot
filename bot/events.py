# -*- coding: utf-8 -*-
import csv
import os
import traceback
from nextcord import ChannelType, Activity, ActivityType

from core.client import dc
from core.database import db
from core.console import log
from core.config import cfg
import bot

from nextcord import Embed, Colour

# ── Q Ping specialty embed ────────────────────────────────────────────────────
_Q_PING_ROLE_ID = 1340717895449186324   # @Q ping role

_SEEKER_ROLE_ID = 1478503988562235595
_BEATER_ROLE_ID = 1478503991737585735
_KEEPER_ROLE_ID = 1478503986205036655
_SEEKERS_NEEDED = 2
_BEATERS_NEEDED = 2
_KEEPERS_NEEDED = 2


def _has_specialty(member, role_id: int) -> bool:
	return bool(role_id) and any(r.id == role_id for r in member.roles)


async def _send_q_ping_embed(message) -> None:
	"""Respond to an @Q ping mention with the specialty-position status embed."""
	qc = bot.queue_channels.get(message.channel.id)
	if qc is None:
		return

	# Largest active queue, or first queue if all empty
	q = next(iter(sorted(
		(q for q in qc.queues if q.length),
		key=lambda q: q.length, reverse=True
	)), qc.queues[0] if qc.queues else None)
	if q is None:
		return

	players_in     = len(q.queue)
	players_needed = max(q.cfg.size - players_in, 0)

	seekers = sum(1 for m in q.queue if _has_specialty(m, _SEEKER_ROLE_ID))
	beaters = sum(1 for m in q.queue if _has_specialty(m, _BEATER_ROLE_ID))
	keepers = sum(1 for m in q.queue if _has_specialty(m, _KEEPER_ROLE_ID))

	embed = Embed(
		colour=Colour(0x5865F2),
		description=(
			f"Please add to **{q.name}**, **{players_needed}** players left!\n\n"
			f"**Specialty Positions Needed:**\n"
			f"{seekers}/{_SEEKERS_NEEDED} Seekers\n"
			f"{beaters}/{_BEATERS_NEEDED} Beaters\n"
			f"{keepers}/{_KEEPERS_NEEDED} Keepers"
		)
	)
	await message.channel.send(embed=embed)

from bot.elo_sync import process_elo_sync
from bot.civ_sync import parse_lobby_embed, buffer_lobby_result, persist_lobby_civs
from bot.message_logger import log_channel_message, log_bot_message
from bot.match.party_code import handle_code_input


async def seed_ratings_from_csv():
	"""One-time bulk seed of player ratings from data/qc_players.csv into all queue channels."""
	csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'qc_players.csv')
	if not os.path.exists(csv_path):
		log.info("No data/qc_players.csv found, skipping rating seed.")
		return

	for qc in bot.queue_channels.values():
		dest_id = qc.rating.channel_id
		existing = await db.select(['user_id'], 'qc_players', where={'channel_id': dest_id})
		rated_existing = [p for p in existing if p.get('user_id')]
		if len(rated_existing) > 0:
			log.info(f"\tChannel {dest_id} already has {len(rated_existing)} players, skipping CSV seed.")
			continue

		with open(csv_path, newline='') as f:
			reader = csv.DictReader(f)
			rows = [r for r in reader if r.get('rating')]

		if not rows:
			continue

		to_insert = []
		for r in rows:
			to_insert.append({
				'channel_id': dest_id,
				'user_id': int(r['user_id']),
				'nick': r['nick'],
				'rating': int(r['rating']),
				'deviation': int(r['deviation']) if r.get('deviation') else 300,
				'wins': int(r.get('wins') or 0),
				'losses': int(r.get('losses') or 0),
				'draws': int(r.get('draws') or 0),
				'streak': int(r.get('streak') or 0),
			})

		await db.insert_many('qc_players', to_insert, on_dublicate='replace')
		log.info(f"\tSeeded {len(to_insert)} player ratings from CSV into channel {dest_id}.")


@dc.event
async def on_init():
	await bot.stats.check_match_id_counter()


_last_state_save = 0
_STATE_SAVE_INTERVAL = 30

last_tick_at = 0.0


@dc.event
async def on_think(frame_time):
	global _last_state_save, last_tick_at
	last_tick_at = frame_time

	for match in list(bot.active_matches):
		try:
			await match.think(frame_time)
		except Exception as e:
			log.error("\n".join([
				"Error at Match.think().",
				f"match_id: {match.id}).",
				f"{str(e)}. Traceback:\n{traceback.format_exc()}=========="
			]))
			if match in bot.active_matches:
				bot.active_matches.remove(match)
			continue
	await bot.expire.think(frame_time)
	await bot.noadds.think(frame_time)
	await bot.stats.jobs.think(frame_time)
	await bot.expire_auto_ready(frame_time)

	bot.waiting_reactions.sweep_expired(frame_time)

	if frame_time - _last_state_save >= _STATE_SAVE_INTERVAL:
		try:
			bot.save_state()
			_last_state_save = frame_time
		except Exception as e:
			log.error(f"Periodic save_state failed: {e}\n{traceback.format_exc()}")


@dc.event
async def on_message(message):
	if message.channel.type == ChannelType.private and message.author.id != dc.user.id:
		await message.channel.send(cfg.HELP)

	if message.channel.type != ChannelType.text:
		return

	# @Q ping role mention → specialty status embed
	if any(r.id == _Q_PING_ROLE_ID for r in message.role_mentions):
		await _send_q_ping_embed(message)

	if message.content == '!enable_pubobot':
		await bot.enable_channel(message)
	elif message.content == '!disable_pubobot':
		await bot.disable_channel(message)

	# ++ / -- shorthand add/remove
	if message.content in ('++', '--'):
		if (qc := bot.queue_channels.get(message.channel.id)) is not None and bot.bot_ready:
			from bot.context.message import MessageContext
			ctx = MessageContext(qc, message)
			try:
				if message.content == '++':
					await bot.commands.add(ctx)
				else:
					await bot.commands.remove(ctx)
			except bot.Exc.PubobotException as e:
				await ctx.error(str(e), title=e.__class__.__name__)
			except Exception as e:
				log.error(f"Error processing '{message.content}': {e}\n{traceback.format_exc()}")
		return

	# ── Party code collection ─────────────────────────────────────────────────
	# Check if this message is a captain typing their lobby code.
	# Must happen before ELO sync checks to avoid conflicts.
	if (
		not message.author.bot
		and message.channel.id in bot.queue_channels
		and bot.bot_ready
	):
		try:
			if await handle_code_input(message):
				return
		except Exception as e:
			log.error(f"PartyCode handle_code_input error: {e}\n{traceback.format_exc()}")

	# Sync ELO from original Pubobot
	pubobot_id = getattr(cfg, 'PUBOBOT_USER_ID', None)
	if (pubobot_id
		and message.author.id == pubobot_id
		and message.author.bot
		and '```markdown' in message.content
		and 'results' in message.content):
		try:
			log_bot_message(message, 'Pubobot')
			await process_elo_sync(message)
		except Exception as e:
			log.error(f"ELO sync error: {e}\n{traceback.format_exc()}")

	# Buffer AOE2LobbyBOT match results for civ sync
	lobbybot_id = getattr(cfg, 'LOBBYBOT_USER_ID', None)
	if (lobbybot_id
		and message.author.id == lobbybot_id
		and message.author.bot
		and message.embeds):
		try:
			log_bot_message(message, 'AOE2LobbyBOT')
			parsed = parse_lobby_embed(message)
			if parsed:
				buffer_lobby_result(parsed)
				await persist_lobby_civs(message.channel.id, parsed)
		except Exception as e:
			log.error(f"Civ sync buffer error: {e}\n{traceback.format_exc()}")

	# Log all channel messages in queue channels
	if message.channel.id in bot.queue_channels:
		try:
			log_channel_message(message)
		except Exception:
			pass


@dc.event
async def on_reaction_add(reaction, user):
	if user.id != dc.user.id and reaction.message.id in bot.waiting_reactions:
		await bot.waiting_reactions[reaction.message.id](reaction, user)


@dc.event
async def on_raw_reaction_remove(payload):
	if payload.user_id == dc.user.id:
		return
	if payload.message_id not in bot.waiting_reactions:
		return
	guild = dc.get_guild(payload.guild_id) if payload.guild_id else None
	if guild is None:
		return
	member = guild.get_member(payload.user_id)
	if member is None:
		return
	try:
		await bot.waiting_reactions[payload.message_id](payload.emoji, member, remove=True)
	except Exception as e:
		log.error(f"on_raw_reaction_remove callback error: {e}\n{traceback.format_exc()}")


@dc.event
async def on_ready():
	await dc.change_presence(activity=Activity(type=ActivityType.watching, name=cfg.STATUS))
	if not bot.bot_was_ready:
		log.info(f"Logged in discord as '{dc.user.name}#{dc.user.discriminator}'.")
		log.info("Loading queue channels...")
		for channel_id in await bot.QueueChannel.cfg_factory.p_keys():
			channel = dc.get_channel(channel_id)
			if channel:
				bot.queue_channels[channel_id] = await bot.QueueChannel.create(channel)
				await bot.queue_channels[channel_id].update_info(channel)
				log.info(f"\tInit channel {channel.guild.name}>#{channel.name} successful.")
			else:
				log.info(f"\tCould not reach a text channel with id {channel_id}.")

		await seed_ratings_from_csv()
		await bot.load_state()
		bot.bot_was_ready = True
		bot.bot_ready = True
		log.info("Done.")
	else:
		bot.bot_ready = True
		log.info("Reconnected to discord.")


@dc.event
async def on_disconnect():
	log.info("Connection to discord is lost.")
	bot.bot_ready = False


@dc.event
async def on_resumed():
	log.info("Connection to discord is resumed.")
	if bot.bot_was_ready:
		bot.bot_ready = True


@dc.event
async def on_presence_update(before, after):
	if after.raw_status not in ['idle', 'offline']:
		return
	if after.id in bot.allow_offline:
		return

	for qc in filter(lambda i: i.guild_id == after.guild.id, bot.queue_channels.values()):
		if after.raw_status == "offline" and qc.cfg.remove_offline:
			await qc.remove_members(after, reason="offline")
		if after.raw_status == "idle" and qc.cfg.remove_afk and bot.expire.get(qc, after) is None:
			await qc.remove_members(after, reason="afk", highlight=True)


@dc.event
async def on_member_remove(member):
	for qc in filter(lambda i: i.id == member.guild.id, bot.queue_channels.values()):
		await qc.remove_members(member, reason="left guild")

# ── 41 Alert background task ──────────────────────────────────────────────────
from bot.alert import think as _alerts_think
dc.events['on_think'].append(_alerts_think)
