# -*- coding: utf-8 -*-
"""Minimal health-check web server for Railway.

Exposes two routes: `/health` (the liveness probe Railway hits via its
healthcheckPath) and `/` (a plain 200 so the root URL doesn't 404). The old
OAuth2 config dashboard was removed along with its front-end (web_page.html);
only the health endpoint is still needed.
"""

import os
import time

from aiohttp import web

from core.client import dc
import bot

# Process boot time for /health uptime_seconds
_boot_time = time.time()


async def handle_index(request):
	"""Root route — a plain 200 so Railway's root check never 404s."""
	return web.Response(text="Tuobot is running. See /health for status.", content_type='text/plain')


async def handle_health(request):
	"""Liveness probe used by Railway's healthcheckPath.

	Returns 200 only when the Discord client is connected AND the DB pool
	answers a trivial query. Returns 503 in every other state.
	"""
	import asyncio as _asyncio
	from core.database import db as _db
	from bot import events as _events

	discord_ok = bool(getattr(bot, 'bot_ready', False)) and dc.is_ready()

	db_ok = False
	try:
		await _asyncio.wait_for(_db.fetchone("SELECT 1 AS ok"), timeout=2.0)
		db_ok = True
	except Exception:
		db_ok = False

	now = time.time()
	last_tick = getattr(_events, 'last_tick_at', 0.0) or 0.0
	last_tick_age = int(now - last_tick) if last_tick > 0 else None

	healthy = discord_ok and db_ok
	payload = {
		"status": "ok" if healthy else "unhealthy",
		"discord_connected": discord_ok,
		"db_connected": db_ok,
		"bot_ready": bool(getattr(bot, 'bot_ready', False)),
		"active_matches": len(getattr(bot, 'active_matches', []) or []),
		"last_tick_age_seconds": last_tick_age,
		"uptime_seconds": int(now - _boot_time),
	}
	return web.json_response(payload, status=200 if healthy else 503)


def create_app():
	app = web.Application()
	app.router.add_get('/', handle_index)
	app.router.add_get('/health', handle_health)
	return app


async def start_web_server(port=None):
	if port is None:
		port = int(os.environ.get('PORT', 8080))
	app = create_app()
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, '0.0.0.0', port)
	await site.start()
	print(f"Web server started on port {port}")
	return runner
