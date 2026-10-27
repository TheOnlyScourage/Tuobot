# -*- coding: utf-8 -*-
"""The daily queue clear: every queue in every queue channel is emptied at
constants.QUEUE_CLEAR_HOUR local time in constants.QUEUE_CLEAR_TIMEZONE
(4:00 AM Central, DST-aware), with a one-line notice in each channel where
someone was actually removed.

Ticked from events.on_think like the other jobs. The next firing is computed
through the local calendar (bot/queues/daily_clear_rules.next_clear_after) so
a restart never fires a clear retroactively and DST can't skew the hour. The
clear itself mirrors QueueChannel.remove_members' bookkeeping — pop from every
queue AND the standby list, cancel expire timers, drop emptied queues from
bot.active_queues — but deliberately posts its own single notice instead of
the per-player "removed from all queues" lines. Active matches are untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.console import log

import bot
from bot.constants import QUEUE_CLEAR_HOUR, QUEUE_CLEAR_TIMEZONE
from bot.queues.daily_clear_rules import next_clear_after


class DailyClear:
	def __init__(self):
		self.next_at: float | None = None  # unix seconds; armed lazily on the first tick

	def _arm(self, now_utc: datetime) -> None:
		self.next_at = next_clear_after(now_utc, QUEUE_CLEAR_HOUR, QUEUE_CLEAR_TIMEZONE).timestamp()
		log.info(f"[daily_clear] next queue clear at {datetime.fromtimestamp(self.next_at, timezone.utc)} UTC")

	async def think(self, frame_time: int) -> None:
		"""Frame tick: arm on first call; when the scheduled time passes, clear
		and re-arm for tomorrow. Best-effort by design rule — a failure logs
		and the job re-arms rather than retrying every second."""
		if self.next_at is None:
			self._arm(datetime.now(timezone.utc))
			return
		if frame_time < self.next_at:
			return
		# Re-arm from the instant we were SCHEDULED for, not the wall clock:
		# next_clear_after is "strictly after", so this is deterministically
		# tomorrow 4:00 AM regardless of tick jitter around the boundary.
		self._arm(datetime.fromtimestamp(self.next_at, timezone.utc))
		try:
			await self.clear_all()
		except Exception as e:
			log.error(f"[daily_clear] clear failed: {e}")

	async def clear_all(self) -> None:
		"""Empty every queue (and standby list) in every queue channel."""
		for qc in list(bot.queue_channels.values()):
			removed = self._clear_channel(qc)
			if not removed:
				continue
			log.info(f"[daily_clear] #{getattr(qc.channel, 'name', qc.id)}: removed {len(removed)} player(s)")
			try:
				ctx = bot.SystemContext(qc)
				await ctx.notice(qc.topic)
				await ctx.notice(qc.gt(
					"🧹 Daily {hour}:00 AM queue reset — {n} player(s) removed. Re-add to keep playing!"
				).format(hour=QUEUE_CLEAR_HOUR, n=len(removed)))
			except Exception as e:
				log.error(f"[daily_clear] notice failed for channel {qc.id}: {e}")

	@staticmethod
	def _clear_channel(qc) -> list:
		"""Pop everyone from this channel's queues; return the distinct members
		removed (empty when nothing was queued)."""
		members = {}
		for q in qc.queues:
			for m in list(q.queue) + list(q.standby):
				members.setdefault(m.id, m)
		if not members:
			return []
		for q in qc.queues:
			q.pop_members(*members.values())
			if q in bot.active_queues and not q.length:
				bot.active_queues.remove(q)
		for m in members.values():
			bot.expire.cancel(qc, m)
		return list(members.values())


daily_clear = DailyClear()
