#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import signal
import asyncio
import traceback
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

loop = asyncio.get_event_loop()
loop.run_until_complete(database.db.connect())

# One-time fix: set NULL deviations to 350 (prevents rating.py TypeError on match start)
async def _fix_null_deviations():
    await database.db.fetchall(
        "UPDATE qc_players SET deviation = 350 WHERE deviation IS NULL", []
    )
loop.run_until_complete(_fix_null_deviations())

# Initialize tracker tables before loading the bot.
from bot.stats.checkin_tracker import init_checkin_tracker_table
from bot.stats.season import init_season_table
loop.run_until_complete(init_checkin_tracker_table())
loop.run_until_complete(init_season_table())

# Load bot
import bot

# Load web server
from bot.web import start_web_server
web_runner = None

log = console.log


def _task_done_callback(task):
	if task.cancelled():
		return
	exc = task.exception()
	if exc is None:
		return
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


loop = asyncio.get_event_loop()
supervised_task(init_web(), name="web_server")
supervised_task(think(), name="think_loop")
supervised_task(dc.start(config.cfg.DC_BOT_TOKEN), name="discord_client")

log.info("Connecting to discord...")
loop.run_forever()
