"""Top-level slash command groups (parents for subcommands). Each is an empty
stub; the subcommands are attached in bot/context/slash/commands.py."""
from __future__ import annotations

from nextcord import Interaction

from core.client import dc
from core.config import cfg

guild_kwargs = dict(guild_ids=cfg.DC_SLASH_SERVERS) if len(cfg.DC_SLASH_SERVERS) else dict()


@dc.slash_command(name='channel', **guild_kwargs)
async def admin_channel(interaction: Interaction) -> None:
	"""Parent group for /channel subcommands."""
	pass


@dc.slash_command(name='queue', **guild_kwargs)
async def admin_queue(interaction: Interaction) -> None:
	"""Parent group for /queue subcommands."""
	pass


@dc.slash_command(name='match', **guild_kwargs)
async def admin_match(interaction: Interaction) -> None:
	"""Parent group for /match subcommands."""
	pass


@dc.slash_command(name='rating', **guild_kwargs)
async def admin_rating(interaction: Interaction) -> None:
	"""Parent group for /rating subcommands."""
	pass


@dc.slash_command(name='stats', **guild_kwargs)
async def admin_stats(interaction: Interaction) -> None:
	"""Parent group for /stats subcommands."""
	pass


@dc.slash_command(name='noadds', **guild_kwargs)
async def admin_noadds(interaction: Interaction) -> None:
	"""Parent group for /noadds subcommands."""
	pass


@dc.slash_command(name='phrases', **guild_kwargs)
async def admin_phrases(interaction: Interaction) -> None:
	"""Parent group for /phrases subcommands."""
	pass
