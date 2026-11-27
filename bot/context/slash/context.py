from __future__ import annotations

import asyncio

from nextcord import Interaction

from core.utils import ok_embed, error_embed

from bot import QueueChannel

from ..context import Context


class SlashContext(Context):
	"""Context for the slash commands, and for the buttons, which run them.

	Every FIRST response to the interaction (a reply, or run_slash's 2.5 s
	defer) goes through _first(), one at a time. nextcord only marks an
	interaction answered once Discord has replied, so a command answering
	while run_slash's defer was still on its way sent a second first-response.
	Discord refused it with 40060 "Interaction has already been acknowledged",
	and the bot posted that publicly as an HTTPException (a Join press,
	Nov 26). Now whichever goes second waits, sees the interaction answered,
	and sends a followup instead.
	"""

	def __init__(self, qc: QueueChannel, interaction: Interaction) -> None:
		self.interaction = interaction
		self._answering = asyncio.Lock()
		self._deferred = False  # answered by defer() alone: nothing shown yet
		super().__init__(qc, interaction.channel, interaction.user)

	async def _first(self, send) -> bool:
		"""Run `send` as the interaction's first response, unless something
		already answered it. True if `send` ran."""
		async with self._answering:
			if self.interaction.response.is_done():
				return False
			await send()
			return True

	async def defer(self) -> bool:
		"""Acknowledge without a message, unless something already answered.
		True if this call deferred it."""
		if await self._first(self.interaction.response.defer):
			self._deferred = True
			return True
		return False

	async def reply(self, *args, **kwargs) -> None:
		"""Reply to the interaction (initial response, or followup if already sent)."""
		if not await self._first(lambda: self.interaction.response.send_message(*args, **kwargs)):
			await self.interaction.followup.send(*args, **kwargs)

	async def reply_dm(self, *args, **kwargs) -> None:
		"""Reply ephemerally, or DM the user if the interaction was already answered."""
		if not await self._first(lambda: self.interaction.response.send_message(*args, **kwargs, ephemeral=True)):
			await self.interaction.user.send(*args, **kwargs)

	async def notice(self, *args, **kwargs) -> None:
		"""Send a channel-visible message (interaction response, or channel send)."""
		if not await self._first(lambda: self.interaction.response.send_message(*args, **kwargs)):
			await self.interaction.channel.send(*args, **kwargs)

	async def ignore(self, *args, **kwargs) -> None:
		"""Answer privately. Skipped if the command already answered with a
		message; after run_slash's defer (nothing shown yet) it goes out as a
		private followup, or the presser would get no answer at all."""
		sent = await self._first(lambda: self.interaction.response.send_message(*args, **kwargs, ephemeral=True))
		if not sent and self._deferred:
			await self.interaction.followup.send(*args, **kwargs, ephemeral=True)

	async def error(self, *args, **kwargs) -> None:
		"""Send an error embed only the person who ran the command sees."""
		embed = error_embed(*args, **kwargs)
		if not await self._first(lambda: self.interaction.response.send_message(embed=embed, ephemeral=True)):
			await self.interaction.followup.send(embed=embed, ephemeral=True)

	async def success(self, *args, **kwargs) -> None:
		"""Reply with a success embed."""
		await self.reply(embed=ok_embed(*args, **kwargs))
