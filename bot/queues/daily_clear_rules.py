# -*- coding: utf-8 -*-
"""Pure clock math for the daily queue clear (no bot/db/discord imports, so the
dependency-free test suite can load this by path). The glue that actually
empties queues lives in bot/queues/daily_clear.py."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def next_clear_after(now_utc: datetime, hour: int, tz_name: str) -> datetime:
	"""The first `hour`:00 local wall-clock time in `tz_name` STRICTLY after
	`now_utc`, returned as an aware UTC datetime.

	Computed on the local calendar (not by adding 24h) so a DST change
	between two clears still lands on 4:00 AM local, and a clear is never
	fired retroactively: a bot that boots at 10 AM waits until 4 AM tomorrow."""
	tz = ZoneInfo(tz_name)
	local_now = now_utc.astimezone(tz)
	candidate = local_now.replace(hour=hour, minute=0, second=0, microsecond=0)
	if candidate <= local_now:
		candidate = (candidate + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
	# Re-anchor through the local calendar so a DST shift can't skew the hour.
	candidate = datetime(candidate.year, candidate.month, candidate.day, hour, tzinfo=tz)
	return candidate.astimezone(ZoneInfo("UTC"))
