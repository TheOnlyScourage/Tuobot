#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import signal
import asyncio
import traceback
import warnings

# Silence MySQL "Duplicate entry ... for key qc_players.PRIMARY" warnings —
# these are emitted on every `INSERT ... ON DUPLICATE KEY IGNORE` even though
# the IGNORE clause means the duplicate is correctly skipped (no data lost).
# Pure log noise, hidden here to keep Railway logs readable.
warnings.filterwarnings("ignore", message=".*Duplicate entry.*for key.*", category=Warning)
warnings.filterwarnings("ignore", category=Warning, module=r"aiomysql.*")
from asyncio import sleep as asleep

_sentry_dsn = os.environ.get('SENTRY_DSN', '').strip()
if _sentry_dsn:
	import sentry_sdk
	sentry_sdk.init(
		dsn=_sentry_dsn,
		attach_stacktrace=True,
		traces_sample_rate=0.0,
		environment=os.environ.get('RAILWAY_ENVIRONMENT_NAME', 'local'),
		release=os.environ.get('RAILWAY_GIT_COMMIT_SHA', None),
	)
else:
	sentry_sdk = None

from core import config, console, database, cfg_factory
from core.client import dc

# Run everything on the Discord client's own loop. nextcord creates (and sets)
# it when `dc` is built at import time, and keeps using `dc.loop` internally,
# so a separately made loop would leave Discord events with nothing to run
# them. (Was asyncio.get_event_loop(), which returned the same loop but is
# deprecated when no loop is running.)
loop = dc.loop


def _connect_with_retry(attempts: int = 12, delay: float = 5.0) -> None:
	"""MySQL being briefly unreachable at boot (Railway restarting it, a network
	blip) used to crash the bot on the spot, and railway.toml gives up after 5
	failed restarts. Keep trying for about a minute first."""
	for attempt in range(1, attempts + 1):
		try:
			loop.run_until_complete(database.db.connect())
			return
		except Exception as e:
			if attempt == attempts:
				raise
			console.log.error(f"Database not reachable yet ({e!r}); retry {attempt}/{attempts - 1} in {delay:.0f}s")
			time.sleep(delay)


_connect_with_retry()

# Initialize all Tuobot-specific MySQL tables in one consolidated call.
# See bot/db_init.py for the full list and ordering.
from bot.db_init import init_all_tables
loop.run_until_complete(init_all_tables())


# One-time fix: set NULL deviations to 350 (prevents rating.py TypeError on
# match start). It runs AFTER init_all_tables: on an empty or freshly restored
# database qc_players doesn't exist until then, and this used to crash the boot.
async def _fix_null_deviations():
	await database.db.execute("UPDATE qc_players SET deviation = 350 WHERE deviation IS NULL")
loop.run_until_complete(_fix_null_deviations())

# Load bot
import bot

# Load web server
from bot.web import start_web_server
web_runner = None

log = console.log
_crashed = False  # set when a supervised task dies; decides the exit code


def _task_done_callback(task):
	global _crashed
	if task.cancelled():
		return
	exc = task.exception()
	if exc is None:
		return
	_crashed = True
	tb_text = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
	log.error(f"CRITICAL: supervised task '{task.get_name()}' crashed:\n{tb_text}")
	if sentry_sdk is not None:
		try:
			with sentry_sdk.push_scope() as scope:
				scope.set_tag("task_name", task.get_name())
				scope.set_tag("critical", "true")
				sentry_sdk.capture_exception(exc)
		except Exception as sentry_exc:
			log.error(f"Sentry capture failed during task crash: {sentry_exc}")
	try:
		bot.save_state()
	except Exception as save_exc:
		log.error(f"Failed to save state during crash: {save_exc}")
	log.error("Stopping event loop — process will exit, Railway will restart the container.")
	try:
		loop.stop()
	except RuntimeError:
		pass


def supervised_task(coro, name):
	task = loop.create_task(coro, name=name)
	task.add_done_callback(_task_done_callback)
	return task


original_SIGINT_handler = signal.getsignal(signal.SIGINT)
original_SIGTERM_handler = signal.getsignal(signal.SIGTERM)


def ctrl_c(sig, frame):
	log.info(f"Received signal {sig}, shutting down gracefully...")
	bot.save_state()
	console.terminate()
	signal.signal(signal.SIGINT, original_SIGINT_handler)
	signal.signal(signal.SIGTERM, original_SIGTERM_handler)


signal.signal(signal.SIGINT, ctrl_c)
signal.signal(signal.SIGTERM, ctrl_c)


async def think():
	for task in dc.events['on_init']:
		await task()

	while console.alive:
		frame_time = time.time()
		for task in dc.events['on_think']:
			try:
				await task(frame_time)
			except Exception as e:
				log.error('Error running background task from {}: {}\n{}'.format(task.__module__, str(e), traceback.format_exc()))
		await asleep(1)

	for task in dc.events['on_exit']:
		try:
			await task()
		except Exception as e:
			log.error('Error running exit task from {}: {}\n{}'.format(task.__module__, str(e), traceback.format_exc()))

	log.info("Waiting for connection to close...")
	await dc.close()

	log.info("Closing db.")
	await database.db.close()
	if web_runner:
		log.info("Closing web server.")
		await web_runner.cleanup()
	log.info("Closing log.")
	log.close()
	print("Exit now.")
	loop.stop()


async def init_web():
	global web_runner
	try:
		web_runner = await start_web_server()
	except Exception as e:
		log.error(f"Failed to start web server: {e}")


supervised_task(init_web(), name="web_server")
supervised_task(think(), name="think_loop")
supervised_task(dc.start(config.cfg.DC_BOT_TOKEN), name="discord_client")

log.info("Connecting to discord...")
loop.run_forever()

if _crashed:
	# railway.toml restarts the service only ON_FAILURE, i.e. on a non-zero exit
	# code. This used to exit 0 after a crash (Discord unreachable at boot, the
	# think loop dying), so Railway saw a clean stop and left the bot offline
	# until the next deploy. A normal shutdown (SIGTERM on redeploy) still exits 0.
	sys.exit(1)
