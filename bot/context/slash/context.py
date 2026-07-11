from __future__ import annotations

from nextcord import Interaction

from core.utils import ok_embed, error_embed

from bot import QueueChannel

from ..context import Context


class SlashContext(Context):
	""" Context for the slash message commands """

	def __init__(self, qc: QueueChannel, interaction: Interaction) -> None:
		self.interaction = interaction
		super().__init__(qc, interaction.channel, interaction.user)

	async def reply(self, *args, **kwargs) -> None:
		"""Reply to the interaction (initial response, or followup if already sent)."""
		if not self.interaction.response.is_done():
			await self.interaction.response.send_message(*args, **kwargs)
		else:
			await self.interaction.followup.send(*args, **kwargs)

	async def reply_dm(self, *args, **kwargs) -> None:
		"""Reply ephemerally, or DM the user if the interaction was already answered."""
		if not self.interaction.response.is_done():
			await self.interaction.response.send_message(*args, **kwargs, ephemeral=True)
		else:
			await self.interaction.user.send(*args, **kwargs)

	async def notice(self, *args, **kwargs) -> None:
		"""Send a channel-visible message (interaction response, or channel send)."""
		if not self.interaction.response.is_done():
			await self.interaction.response.send_message(*args, **kwargs)
		else:
			await self.interaction.channel.send(*args, **kwargs)

	async def ignore(self, *args, **kwargs) -> None:
		"""Send an ephemeral acknowledgement (only if not yet responded)."""
		if not self.interaction.response.is_done():
			await self.interaction.response.send_message(*args, **kwargs, ephemeral=True)

	async def error(self, *args, **kwargs) -> None:
		"""Send an ephemeral error embed."""
		if not self.interaction.response.is_done():
			await self.interaction.response.send_message(embed=error_embed(*args, **kwargs), ephemeral=True)
		else:  # this probably should never happen
			await self.interaction.followup.send(embed=error_embed(*args, **kwargs))

	async def success(self, *args, **kwargs) -> None:
		"""Reply with a success embed."""
		await self.reply(embed=ok_embed(*args, **kwargs))
