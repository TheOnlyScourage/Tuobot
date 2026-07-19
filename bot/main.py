# -*- coding: utf-8 -*-
import traceback  # noqa: F401
import json
from nextcord import Interaction  # noqa: F401
from core.console import log
from core.database import db
from core.config import cfg
from core.utils import error_embed, ok_embed, get  # noqa: F401
import bot


async def init_saved_state_table():
	"""Create the saved_state table in MySQL — survives Railway redeploys."""
	await db._ensure_table(dict(
		tname="saved_state",
		columns=[
			dict(cname="id",      ctype=db.types.int),
			dict(cname="payload", ctype=db.types.text),
		],
		primary_keys=["id"]
	))


async def enable_channel(message):
	if not (message.author.id == cfg.DC_OWNER_ID or message.channel.permissions_for(message.author).administrator):
		await message.channel.send(embed=error_embed(
			"One must posses the guild administrator permissions in order to use this command."
		))
		return
	if message.channel.id not in bot.queue_channels.keys():
		bot.queue_channels[message.channel.id] = await bot.QueueChannel.create(message.channel)
		await message.channel.send(embed=ok_embed("The bot has been enabled."))
	else:
		await message.channel.send(
			embed=error_embed("The bot is already enabled on this channel.")
		)


async def find_orphan_channel_configs() -> list[dict]:
	"""Channel configs whose Discord channel no longer exists — deleted from
	the server before anyone ran `/admin channel delete` inside it. A channel
	is only flagged when it is neither loaded (bot.queue_channels) nor
	visible to the client cache, so hidden-but-alive channels never match.
	Returns [{channel_id, queues: [names]}, ...]."""
	from core.client import dc
	orphans = []
	for channel_id in await bot.QueueChannel.cfg_factory.p_keys():
		if channel_id in bot.queue_channels:
			continue
		if dc.get_channel(channel_id) is not None:
			continue
		queue_rows = await db.select(['name'], 'pq_configs', where={'channel_id': channel_id})
		orphans.append(dict(
			channel_id=channel_id,
			queues=[r['name'] for r in queue_rows],
		))
	return orphans


async def purge_orphan_channel_configs() -> list[dict]:
	"""Delete every orphaned channel config and its queues' configs —
	mirroring exactly what `/admin channel delete` does for a live channel
	(the qc_configs row + that channel's pq_configs rows; cfg data is a
	single JSON row per config, no sub-tables). Match history is untouched.
	Returns the purged orphan list for reporting."""
	orphans = await find_orphan_channel_configs()
	for o in orphans:
		await db.delete('pq_configs', where={'channel_id': o['channel_id']})
		await db.delete('qc_configs', where={'channel_id': o['channel_id']})
	return orphans


async def disable_channel(message):
	if not (message.author.id == cfg.DC_OWNER_ID or message.channel.permissions_for(message.author).administrator):
		await message.channel.send(embed=error_embed(
			"One must posses the guild administrator permissions in order to use this command."
		))
		return
	qc = bot.queue_channels.get(message.channel.id)
	if qc:
		for queue in qc.queues:
			await queue.cfg.delete()
		await qc.cfg.delete()
		bot.queue_channels.pop(message.channel.id)
		await message.channel.send(embed=ok_embed("The bot has been disabled."))
	else:
		await message.channel.send(embed=error_embed("The bot is not enabled on this channel."))


def update_qc_lang(qc_cfg):
	bot.queue_channels[qc_cfg.p_key].update_lang()


def update_rating_system(qc_cfg):
	bot.queue_channels[qc_cfg.p_key].update_rating_system()


def save_state():
	"""Save state synchronously to local file AND schedule MySQL save.

	Called from signal handlers (must be sync). The MySQL save is fired
	via asyncio.ensure_future so it runs on the event loop without
	blocking the signal handler.
	"""
	import asyncio
	log.info("Saving state...")
	queues = []
	for qc in bot.queue_channels.values():
		for q in qc.queues:
			if q.length > 0:
				queues.append(q.serialize())
	matches = []
	for match in bot.active_matches:
		matches.append(match.serialize())

	payload = dict(
		queues=queues,
		matches=matches,
		allow_offline=bot.allow_offline,
		auto_ready={str(k): v for k, v in bot.auto_ready.items()},
		expire=bot.expire.serialize()
	)
	payload_json = json.dumps(payload)

	# Local file (survives restart, lost on Railway redeploy)
	try:
		with open("saved_state.json", 'w') as f:  # noqa: SIM115
			f.write(payload_json)
	except Exception as e:
		log.error(f"Local save_state failed: {e}")

	# MySQL (survives Railway redeploys)
	try:
		loop = asyncio.get_event_loop()
		if loop.is_running():
			asyncio.ensure_future(_save_state_db(payload_json))
		else:
			loop.run_until_complete(_save_state_db(payload_json))
	except Exception as e:
		log.error(f"DB save_state schedule failed: {e}")


async def _save_state_db(payload_json: str):
	"""Persist saved state to MySQL (single-row table, id=1)."""
	try:
		existing = await db.select_one(('id',), 'saved_state', where={'id': 1})
		if existing:
			await db.update('saved_state', {'payload': payload_json}, keys={'id': 1})
		else:
			await db.insert('saved_state', {'id': 1, 'payload': payload_json})
	except Exception as e:
		log.error(f"DB save_state failed: {e}\n{traceback.format_exc()}")


async def load_state():
	"""Load state from MySQL first; fall back to local file if DB has nothing."""
	data = None

	# 1) Try MySQL (survives redeploys)
	try:
		row = await db.select_one(('payload',), 'saved_state', where={'id': 1})
		if row and row.get('payload'):
			data = json.loads(row['payload'])
			log.info("Loaded state from MySQL.")
	except Exception as e:
		log.error(f"DB load_state failed, will try local file: {e}")

	# 2) Fall back to local file (covers fresh DBs / migration window)
	if data is None:
		try:
			with open("saved_state.json", "r") as f:
				data = json.loads(f.read())
				log.info("Loaded state from local saved_state.json.")
		except IOError:  # noqa: UP024
			return  # nothing to restore

	if data is None:
		return

	bot.allow_offline = list(data.get('allow_offline') or [])

	# auto_ready: keys may be strings from MySQL, ensure they're ints
	ar = data.get('auto_ready') or {}
	bot.auto_ready = {int(k): v for k, v in ar.items()}

	for qd in (data.get('queues') or []):
		if qd.get('queue_type') in ['PickupQueue', None]:
			try:
				await bot.PickupQueue.from_json(qd)
			except bot.Exc.ValueError as e:
				log.error(f"Failed to load queue state ({qd.get('queue_id')}): {str(e)}")
		else:
			log.error(f"Got unknown queue type '{qd.get('queue_type')}'.")

	for md in (data.get('matches') or []):
		try:
			await bot.Match.from_json(md)
		except bot.Exc.ValueError as e:
			log.error(f"Failed to load match {md['match_id']}: {str(e)}")

	if 'expire' in data and data['expire']:
		try:
			await bot.expire.load_json(data['expire'])
		except Exception as e:
			log.error(f"Failed to load expire state: {e}")


async def remove_players(*users, reason=None, exclude=None):
	"""Remove users from every active queue's channel.

	`exclude` is an optional set of user-IDs that must NOT be removed — used by
	the queue-priority system so players protected by a higher-priority queue
	are never pulled out (and never trigger the "removed from all queues"
	message). It's forwarded to each channel's remove_members.
	"""
	for qc in set((q.qc for q in bot.active_queues)):
		await qc.remove_members(*users, reason=reason, exclude=exclude)


async def expire_auto_ready(frame_time):
	for user_id, at in list(bot.auto_ready.items()):
		if at < frame_time:
			bot.auto_ready.pop(user_id)
