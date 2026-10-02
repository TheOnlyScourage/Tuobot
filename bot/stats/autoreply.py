# -*- coding: utf-8 -*-
"""/autoreply — the owner-toggled out-of-office prank responder.

While ON, every non-bot message that @-mentions constants.AUTOREPLY_TARGET_ID
(reply-pings count, since Discord puts the replied-to user in `mentions`) is
answered with the NEXT entry of constants.AUTOREPLY_LINES, in order, wrapping
— a story the server reads one ping at a time. {pinger}/{mention} in a line
render the pinger's name / an @-ping. A per-pinger cooldown stops one person
farming the whole story in a minute.

State that must survive a restart (on/off + the story cursor) lives in the
single-row `autoreply` MySQL table — Railway can bounce the bot mid-vacation
and the bit carries on from the same line. The cooldown map is in-memory by
design. Init is registered as a bot/db_init.py step; events.on_ready loads it.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from core.console import log
from core.database import db
from core.utils import get_nick

from bot.constants import AUTOREPLY_LINES, AUTOREPLY_TARGET_ID
from bot.stats.autoreply_rules import advance, line_at, on_cooldown, render_line

if TYPE_CHECKING:
	from nextcord import Message

_ROW = 1  # the table holds exactly one row


async def init_autoreply_table() -> None:
	"""Create the autoreply state table if missing and seed its single row
	(OFF, cursor 0). Registered in bot/db_init.py — the only init path."""
	await db._ensure_table(dict(
		tname="autoreply",
		columns=[
			dict(cname="id",      ctype=db.types.int),
			dict(cname="enabled", ctype=db.types.bool, notnull=True, default=0),
			dict(cname="cursor",  ctype=db.types.int,  notnull=True, default=0),
		],
		primary_keys=["id"],
	))
	await db.insert("autoreply", dict(id=_ROW, enabled=0, cursor=0), on_dublicate="ignore")


class AutoReply:
	def __init__(self):
		self.enabled = False
		self.cursor = 0
		self.loaded = False
		self._last: dict[int, float] = {}  # pinger id → time of their last reply

	# ── persistence ──────────────────────────────────────────────────────────

	async def load(self) -> None:
		"""Restore on/off + cursor from MySQL (called once from on_ready)."""
		row = await db.select_one(['enabled', 'cursor'], 'autoreply', where=dict(id=_ROW))
		if row:
			self.enabled = bool(row['enabled'])
			self.cursor = int(row['cursor'] or 0) % len(AUTOREPLY_LINES)
		self.loaded = True
		if self.enabled:
			log.info(f"[autoreply] ON — next line {self.cursor + 1}/{len(AUTOREPLY_LINES)}")

	async def _save(self) -> None:
		await db.update('autoreply', dict(enabled=int(self.enabled), cursor=self.cursor), keys=dict(id=_ROW))

	async def set_enabled(self, on: bool) -> None:
		"""Turn the responder on (story restarts from line 1) or off."""
		if not self.loaded:
			await self.load()
		self.enabled = on
		if on:
			self.cursor = 0
			self._last.clear()
		await self._save()
		log.info(f"[autoreply] turned {'ON' if on else 'OFF'}")

	def status_text(self) -> str:
		n = len(AUTOREPLY_LINES)
		state = "**ON**" if self.enabled else "**OFF**"
		verb = "next line" if self.enabled else "would resume at line"
		return f"Auto-reply is {state} · {verb} {self.cursor + 1}/{n} · {len(self._last)} pinger(s) seen this run."

	# ── the responder ────────────────────────────────────────────────────────

	def wants(self, message: Message) -> bool:
		"""Cheap on_message gate: ON, human author, and the target is pinged."""
		return (
			self.enabled
			and not message.author.bot
			and any(u.id == AUTOREPLY_TARGET_ID for u in message.mentions)
		)

	async def handle(self, message: Message) -> None:
		"""Reply with the next story line (per-pinger cooldown applies). The
		cursor advances only after a successful send, so a failed reply retries
		the same line next time. Best-effort by design rule: failures log and
		never touch the rest of on_message."""
		now = time.time()
		if on_cooldown(self._last.get(message.author.id), now):
			return
		self._last[message.author.id] = now
		text = render_line(line_at(self.cursor), pinger=get_nick(message.author), mention=message.author.mention)
		try:
			await message.reply(text, mention_author=True)
		except Exception as e:
			log.error(f"[autoreply] reply failed: {e}")
			return
		self.cursor = advance(self.cursor)
		try:
			await self._save()
		except Exception as e:
			log.error(f"[autoreply] cursor save failed: {e}")


autoreply = AutoReply()
