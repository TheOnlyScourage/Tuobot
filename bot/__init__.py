# -*- coding: utf-8 -*-
"""
Consolidated database table initialization.

PUBobot2.py used to have six separate `loop.run_until_complete(init_*())`
calls scattered after the bot setup. They're all now in one place:
call `await init_all_tables()` once at startup and every Tuobot-specific
table is created if missing.
"""

from core.console import log


async def init_all_tables():
	"""Initialize every Tuobot-specific MySQL table.

	Safe to call repeatedly — each underlying `_ensure_table` is a CREATE
	TABLE IF NOT EXISTS. Logs progress so startup issues are easy to spot
	in Railway logs.
	"""
	from bot.stats.checkin_tracker import init_checkin_tracker_table
	from bot.stats.season         import init_season_table
	from bot.stats.house_points   import init_house_points_table
	from bot.stats.captain_streak import init_captain_streak_table
	from bot.main                 import init_saved_state_table

	steps = [
		('checkin_violations', init_checkin_tracker_table),
		('season_info',        init_season_table),
		('saved_state',        init_saved_state_table),
		('house_points',       init_house_points_table),
		('captain_streak',     init_captain_streak_table),
	]

	for name, fn in steps:
		try:
			await fn()
			log.info(f"[db_init] {name} ready")
		except Exception as exc:
			log.error(f"[db_init] {name} FAILED: {exc}")
			# Re-raise so a fundamental DB issue doesn't silently corrupt state
			raise
