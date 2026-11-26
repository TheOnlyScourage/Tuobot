# -*- coding: utf-8 -*-
"""Tuobot's buttons and menus (Nov 2026):

  - Join / Leave under each queue update (same as /add and /remove)
  - Ready / Not ready on the check-in board, next to the ✅ / ⛔ reactions
  - the captain's pick menu on the draft board (same as /pick)
  - We lost / Abort on the match board (same as /report; We lost asks first)

All four are PERSISTENT (see bot/buttons_rules.py): fixed custom ids, and one
live instance of each view, added by register() when the bot starts. The copy
sent with each message is built with prevent_update=False, so nextcord never
stores it: every press reaches the registered instance, old buttons keep
working after a redeploy, and nothing piles up per message.

A press runs through run_slash() exactly like the matching slash command, so
permissions, error messages and logging are identical. The handlers find the
player's match the same way the commands do (by who pressed), never by which
message the button sits on."""
from __future__ import annotations

import nextcord

import bot
from bot import buttons_rules as ids
from core.console import log


async def _slash(interaction, coro, **kwargs) -> None:
	"""Run a bot.commands function for this press, like its slash command."""
	from bot.context.slash.commands import run_slash  # here, not at import time: avoids an import cycle
	await run_slash(coro, interaction=interaction, **kwargs)


class _Persistent(nextcord.ui.View):
	"""timeout=None + a fixed custom_id on every item = persistent."""

	def __init__(self):
		super().__init__(timeout=None, prevent_update=False)

	async def on_error(self, error, item, interaction) -> None:
		log.error(f"[buttons] {type(self).__name__} {getattr(item, 'custom_id', '?')}: {error!r}")


class QueueButtons(_Persistent):
	"""Under every queue update: Join = /add, Leave = /remove (all queues here)."""

	@nextcord.ui.button(label="Join", style=nextcord.ButtonStyle.success, custom_id=ids.QUEUE_JOIN)
	async def join(self, button, interaction) -> None:
		await _slash(interaction, bot.commands.add, queues=None)

	@nextcord.ui.button(label="Leave", style=nextcord.ButtonStyle.secondary, custom_id=ids.QUEUE_LEAVE)
	async def leave(self, button, interaction) -> None:
		await _slash(interaction, bot.commands.remove, queues=None)


class CheckInButtons(_Persistent):
	"""On the check-in board, next to the reactions: Ready = /ready, Not ready = /notready."""

	def __init__(self, allow_discard: bool = True):
		super().__init__()
		if not allow_discard:
			self.remove_item(self.not_ready)

	@nextcord.ui.button(label="Ready", emoji="✅", style=nextcord.ButtonStyle.success, custom_id=ids.CHECKIN_READY)
	async def ready(self, button, interaction) -> None:
		await _slash(interaction, bot.commands.set_ready, is_ready=True)

	@nextcord.ui.button(label="Not ready", emoji="⛔", style=nextcord.ButtonStyle.danger, custom_id=ids.CHECKIN_NOT_READY)
	async def not_ready(self, button, interaction) -> None:
		await _slash(interaction, bot.commands.set_ready, is_ready=False)


class DraftPickMenu(_Persistent):
	"""On the draft board: the captain on the clock picks from this menu."""

	def __init__(self, options: list[dict] | None = None, placeholder: str = "Captain: pick a player"):
		super().__init__()
		self.menu = nextcord.ui.StringSelect(
			custom_id=ids.DRAFT_PICK, placeholder=placeholder, min_values=1, max_values=1,
			# description is left out when empty: nextcord 3 would send it as null
			options=[
				nextcord.SelectOption(label=o["label"], value=o["value"], **({"description": o["description"]} if o.get("description") else {}))
				for o in options or []
			],
		)
		self.menu.callback = self._picked
		self.add_item(self.menu)

	async def _picked(self, interaction) -> None:
		# Read the choice from THIS interaction: the registered menu is shared
		# by every draft board, so its .values belong to whoever chose last.
		values = (interaction.data or {}).get("values") or []
		if values:
			await _slash(interaction, bot.commands.pick_by_id, user_id=int(values[0]))


class ReportButtons(_Persistent):
	"""On the match board while it waits for a result: We lost (asks first) and Abort."""

	@nextcord.ui.button(label="We lost", style=nextcord.ButtonStyle.danger, custom_id=ids.REPORT_LOSS)
	async def lost(self, button, interaction) -> None:
		await _slash(interaction, bot.commands.confirm_loss)

	@nextcord.ui.button(label="Abort", style=nextcord.ButtonStyle.secondary, custom_id=ids.REPORT_ABORT)
	async def abort(self, button, interaction) -> None:
		await _slash(interaction, bot.commands.report, result="abort")


class ConfirmLoss(nextcord.ui.View):
	"""The private "are you sure?" after We lost. Short-lived and NOT persistent:
	its buttons get random custom ids, so they can't clash with the ones above."""

	def __init__(self):
		super().__init__(timeout=60)

	@nextcord.ui.button(label="Yes, we lost", style=nextcord.ButtonStyle.danger)
	async def yes(self, button, interaction) -> None:
		self.stop()
		await _slash(interaction, bot.commands.report, result="loss")

	@nextcord.ui.button(label="Cancel", style=nextcord.ButtonStyle.secondary)
	async def cancel(self, button, interaction) -> None:
		self.stop()
		await interaction.response.edit_message(content="Cancelled — nothing was reported.", view=None)


def register(client) -> None:
	"""Add the one live instance of each persistent view. Call once, from inside
	the event loop (nextcord can only build a view while the loop runs)."""
	for view in (QueueButtons(), CheckInButtons(), DraftPickMenu(), ReportButtons()):
		client.add_view(view)


def maybe(view) -> dict:
	"""`view=` for a send, or nothing at all: interaction responses crash on an
	explicit view=None (nextcord calls methods on it)."""
	return {"view": view} if view is not None else {}
