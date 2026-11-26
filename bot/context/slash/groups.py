"""Top-level slash command groups (parents for subcommands). Each is an empty
stub; the subcommands are attached in bot/context/slash/commands.py."""
from __future__ import annotations

from nextcord import Interaction, Permissions

from core.client import dc
from core.config import cfg

guild_kwargs = dict(guild_ids=cfg.DC_SLASH_SERVERS) if len(cfg.DC_SLASH_SERVERS) else dict()

# The mod/admin groups are hidden from the slash menu of members without
# Discord's Manage Messages permission (a server admin can show them to any
# role in Server Settings > Integrations > Tuobot). /stats stays visible:
# players use /stats show. This only hides commands; the permission checks
# inside each command still decide who may run what.
MOD_MENU = dict(default_member_permissions=Permissions(manage_messages=True))


@dc.slash_command(name='channel', **MOD_MENU, **guild_kwargs)
async def admin_channel(interaction: Interaction) -> None:
	"""Parent group for /channel subcommands."""
	pass


@dc.slash_command(name='queue', **MOD_MENU, **guild_kwargs)
async def admin_queue(interaction: Interaction) -> None:
	"""Parent group for /queue subcommands."""
	pass


@dc.slash_command(name='match', **MOD_MENU, **guild_kwargs)
async def admin_match(interaction: Interaction) -> None:
	"""Parent group for /match subcommands."""
	pass


@dc.slash_command(name='rating', **MOD_MENU, **guild_kwargs)
async def admin_rating(interaction: Interaction) -> None:
	"""Parent group for /rating subcommands."""
	pass


@dc.slash_command(name='stats', **guild_kwargs)
async def admin_stats(interaction: Interaction) -> None:
	"""Parent group for /stats subcommands."""
	pass


@dc.slash_command(name='noadds', **MOD_MENU, **guild_kwargs)
async def admin_noadds(interaction: Interaction) -> None:
	"""Parent group for /noadds subcommands."""
	pass


@dc.slash_command(name='phrases', **MOD_MENU, **guild_kwargs)
async def admin_phrases(interaction: Interaction) -> None:
	"""Parent group for /phrases subcommands."""
	pass
