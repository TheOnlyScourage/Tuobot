# -*- coding: utf-8 -*-
import traceback
from nextcord import ChannelType, Activity, ActivityType

from core.client import dc
from core.console import log
from core.config import cfg
import bot

from nextcord import Embed, Colour

# ── Q Ping specialty embed ────────────────────────────────────────────────────
# IDs centralized in bot/constants.py
from bot.constants import (
	Q_PING_ROLE_ID    as _Q_PING_ROLE_ID,
	SEEKER_ROLE_ID    as _SEEKER_ROLE_ID,
	BEATER_ROLE_ID    as _BEATER_ROLE_ID,
	KEEPER_ROLE_ID    as _KEEPER_ROLE_ID,
	SEEKERS_NEEDED    as _SEEKERS_NEEDED,
	BEATERS_NEEDED    as _BEATERS_NEEDED,
	KEEPERS_NEEDED    as _KEEPERS_NEEDED,
)



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

# ── DonBot branch: vacation auto-responder ───────────────────────────────────
# Scourage is out for 5 days. Anyone who @s him gets one of seven rotating
# lines back — raw text, no embed, no email costume — with a per-pinger
# cooldown so nobody farms the whole set at once.
from time import time as _now

_VACATION_USER_ID = 310593959506477075   # Scourage
_VACATION_COOLDOWN = 120                 # seconds, per pinger
_vacation_last: dict[int, float] = {}
_vacation_cursor = 0                     # lines fire IN POSTED ORDER, wrapping

VACATION_LINES = [
	"Scourage is on vacation he'll get back to you after the<:pantsgrab:1529166555169165332>",
	"Scourage ain’t here lil bro<a:smush:1529166678053748868>",
	"<a:GetSome:1529166223420424202>",
	"Error 404: Scourage not found",
	"Your message has been received and carefully ignored. Thank you for your cooperation",
	"Scourage ain’t reading allat",
	"Scourage is currently in Evergreen Mode. The next scheduled maintenance is when he feels like it<a:mhm:1529166813248618628>",
]


async def _send_vacation_reply(message) -> None:
	"""Reply to a message that pinged Scourage with the NEXT vacation line,
	in the exact order Scourage posted them (wrapping after the last) — raw
	text, nothing else. The cursor only advances on a successful send, so a
	failed reply retries the same line next time. Best-effort: failures log
	and never touch the rest of on_message."""
	global _vacation_cursor
	last = _vacation_last.get(message.author.id, 0)
	if _now() - last < _VACATION_COOLDOWN:
		return
	_vacation_last[message.author.id] = _now()
	line = VACATION_LINES[_vacation_cursor % len(VACATION_LINES)]
	try:
		await message.reply(line)
		_vacation_cursor += 1
	except Exception as e:
		log.error(f"[vacation] reply failed: {e}")


# AoE2 civ/elo sync removed (NammaPUBobot leftover, not used for Q6 Drafts)
from bot.match.party_code import handle_code_input


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

	# Ignore the bot's own messages so its prompt embeds can't be parsed as codes.
	if message.author.id == dc.user.id:
		return

	# Party-up game code: if a teammate on the prompted team typed a valid
	# 6-char code, consume it here and stop. Returns False for normal chatter.
	if await handle_code_input(message):
		return

	# @Q ping role mention → specialty status embed
	if any(r.id == _Q_PING_ROLE_ID for r in message.role_mentions):
		await _send_q_ping_embed(message)

	# DonBot branch: @Scourage while he's on vacation → out-of-office reply
	if not message.author.bot and any(u.id == _VACATION_USER_ID for u in message.mentions):
		await _send_vacation_reply(message)

	if message.content == '!enable_tuobot':
		await bot.enable_channel(message)
	elif message.content == '!disable_tuobot':
		await bot.disable_channel(message)


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


@dc.event
async def on_member_remove(member):
	# NOTE: qc.id is the TEXT CHANNEL id; qc.guild_id is the guild. This used
	# to compare qc.id == guild.id, which never matched — so players who left
	# (or were kicked/banned from) the server silently stayed in queues.
	for qc in filter(lambda i: i.guild_id == member.guild.id, bot.queue_channels.values()):
		await qc.remove_members(member, reason="left guild")

# ── 41 Alert background task ──────────────────────────────────────────────────
from bot.alerts import think as _alerts_think
dc.events['on_think'].append(_alerts_think)
