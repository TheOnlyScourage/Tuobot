# -*- coding: utf-8 -*-
"""
Slash-command autocomplete helpers.

Each function takes the current `interaction` plus the user's partial input
and returns a list (or dict) of choices that Discord renders as a dropdown.

The functions are wired into specific slash command parameters in
bot/context/slash/commands.py via the `.on_autocomplete()` decorator.
"""

from typing import List, Dict  # noqa: UP035
from nextcord import Interaction
from core.utils import find, get
import bot


# ── Original helpers (unchanged) ─────────────────────────────────────────────

async def queues(interaction: Interaction, queue: str) -> List[str]:  # noqa: UP006
	if (qc := bot.queue_channels.get(interaction.channel_id)) is not None:
		return [q.name for q in qc.queues if q.name.startswith(queue)]
	else:
		return []


async def qc_variables(interaction: Interaction, variable: str) -> List[str]:  # noqa: UP006
	return sorted([v for v in bot.QueueChannel.cfg_factory.variables.keys() if v.startswith(variable)])[:10]


async def queue_variables(interaction: Interaction, variable: str) -> List[str]:  # noqa: UP006
	if (qc := bot.queue_channels.get(interaction.channel_id)) is None:
		return []
	interaction_queue = find(lambda i: i['name'] == 'queue', interaction.data['options'][0]['options'])
	if interaction_queue and (queue := get(qc.queues, name=interaction_queue['value'])):
		return sorted([v for v in queue.cfg_factory.variables.keys() if v.startswith(variable)])[:10]
	return []


async def match_ids(interaction: Interaction, match_id: str) -> List[int]:  # noqa: UP006
	if (qc := bot.queue_channels.get(interaction.channel_id)) is None:
		return []
	return [m.id for m in bot.active_matches if m.qc == qc]


async def teams_by_author(interaction: Interaction, name: str) -> List[str]:  # noqa: UP006
	if (match := find(lambda m: interaction.user in m.players, bot.active_matches)) is not None:
		return [team.name for team in match.teams[:2] if team.name.startswith(name)]
	return ['active match not found']


async def teams_by_match_id(interaction: Interaction, name: str) -> List[str]:  # noqa: UP006
	interaction_match = find(lambda i: i['name'] == 'match_id', interaction.data['options'][0]['options'])
	if interaction_match and (match := get(bot.active_matches, id=interaction_match['value'])):
		return [team.name for team in match.teams[:2] if team.name.startswith(name)]
	return ['incorrect match_id supplied']


# ── New helpers for Q6 features ──────────────────────────────────────────────

async def unpicked_players(interaction: Interaction, current: str) -> Dict[str, str]:  # noqa: UP006
	"""Autocomplete the unpicked pool for `/pick` on the active draft.

	Returns `{display_name: user_id_string}` so the slash handler receives
	the user_id back as the parameter value. Capped at the Discord limit.
	"""
	from bot.constants import MAX_AUTOCOMPLETE_OPTS
	try:
		qc = bot.queue_channels.get(interaction.channel_id)
		if qc is None:
			return {}

		candidates = []
		for m in bot.active_matches:
			if m.qc != qc or m.state != m.DRAFT:
				continue
			if len(m.teams) > 2 and m.teams[2]:
				candidates = list(m.teams[2])
				break

		cur = (current or "").lower().strip()
		out: Dict[str, str] = {}
		for p in candidates:
			name = p.display_name[:100]
			if cur and cur not in name.lower():
				continue
			out[name] = str(p.id)
			if len(out) >= MAX_AUTOCOMPLETE_OPTS:
				break
		return out
	except Exception as exc:
		from core.console import log
		log.error(f"[autocomplete.unpicked_players] {exc}")
		return {}


async def players_in_match(interaction: Interaction, current: str) -> Dict[str, str]:  # noqa: UP006
	"""Autocomplete every player in an active match on this channel.

	Used by admin commands like /admin match sub_player and /swap where
	the moderator is targeting somebody who's actively playing.
	"""
	from bot.constants import MAX_AUTOCOMPLETE_OPTS
	try:
		qc = bot.queue_channels.get(interaction.channel_id)
		if qc is None:
			return {}

		seen: Dict[int, str] = {}  # user_id → display_name (dedupe across matches)
		for m in bot.active_matches:
			if m.qc != qc:
				continue
			for p in m.players:
				seen.setdefault(p.id, p.display_name)

		cur = (current or "").lower().strip()
		out: Dict[str, str] = {}
		for uid, name in seen.items():
			label = name[:100]
			if cur and cur not in label.lower():
				continue
			out[label] = str(uid)
			if len(out) >= MAX_AUTOCOMPLETE_OPTS:
				break
		return out
	except Exception as exc:
		from core.console import log
		log.error(f"[autocomplete.players_in_match] {exc}")
		return {}
