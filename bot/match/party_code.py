# -*- coding: utf-8 -*-
"""
Handles the party-up phase and game code collection after draft ends.

Flow:
  1. PartyCode.start(ctx)   → posts the ✅ party-up embed (Image 2)
  2. Captain A reacts ✅    → prompted to type Game 1 lobby code
  3. Captain A types code   → Game 1 Code embed posted (Image 3)
                              Captain B is told to reply with Game 2 code
  4. Captain B reacts ✅    → (can happen any time; code prompt fires when
                              both Game 1 is done AND B has reacted)
  5. Captain B types code   → Game 2 Code embed posted
  6. Captain B types code   → Game 3 Code embed posted
"""
import re
from nextcord import Embed, Colour, DiscordException
from core.client import dc
from core.utils import get_nick
from core.console import log

# House emblems — same as embeds.py (duplicated to avoid circular imports)
HOUSE_EMOJIS = {
	'Hufflepuff': '<:HUFFLEPUFF:1468806463026757663>',
	'Slytherin':  '<:SLYTHERIN:1468806412594446447>',
	'Gryffindor': '<:GRYFFINDOR:1468806447956492328>',
	'Ravenclaw':  '<:RAVENCLAW:1468806434320810027>',
}


def _team_display(team) -> str:
	"""Return '{house_emoji} **HOUSENAME (N)**' or '{emoji} **TeamName**'."""
	house_emoji = HOUSE_EMOJIS.get(team.name, '')
	if house_emoji:
		return f"{house_emoji} **{team.name} ({team.idx + 1})**"
	return f"{team.emoji} **{team.name}**"



# Module-level registry: {(channel_id, user_id): PartyCode}
# Checked by events.py on_message to route captain code inputs.
# Game codes must be exactly 6 characters, uppercase letters and digits only.
# We uppercase the input before checking so the captain can type either case.
CODE_PATTERN = re.compile(r"^[A-Z0-9]{6}$")


def _is_valid_code(text: str) -> bool:
	"""Return True if `text` (after strip+upper) is a valid 6-char code."""
	return bool(CODE_PATTERN.match(text.strip().upper()))


_waiting_codes: dict = {}


async def handle_code_input(message) -> bool:
	"""
	Called from events.py on_message.
	Returns True if the message was consumed as a game code, False otherwise.

	Only messages that match the strict 6-char A-Z/0-9 code format are
	consumed. Anything else (random chatter, "one code pplease", etc.)
	is ignored so the captain can keep talking while waiting.
	"""
	key = (message.channel.id, message.author.id)
	pc = _waiting_codes.get(key)
	if pc is None or not pc.active:
		return False

	if not _is_valid_code(message.content):
		return False  # not a code — leave the listener in place

	# Pop BEFORE handing off so a successful submission can\'t double-fire,
	# but the validation already happened above so this is safe.
	_waiting_codes.pop(key, None)
	await pc._receive_code(message)
	return True


class PartyCode:
	"""Manages the post-draft party-up and lobby code collection for a match."""

	READY_EMOJI = "✅"

	def __init__(self, match):
		self.m = match
		self.ready_order   = []           # captains in the order they reacted ✅
		self.second_captain = None        # the captain who handles Games 2 & 3
		self.pending_captain = None       # captain we're currently waiting on
		self.pending_game  = 0
		self.party_message = None         # the ✅ reaction message
		self.game1_done    = False        # True after Game 1 code is submitted
		self.active        = True         # False after match cancel/finish

	# ── Public API ───────────────────────────────────────────────────────────

	async def start(self, ctx):
		"""Post the party-up embed and register the ✅ reaction callback."""
		import bot
		embed = self._party_embed()
		self.party_message = await ctx.channel.send(embed=embed)
		try:
			await self.party_message.add_reaction(self.READY_EMOJI)
		except DiscordException:
			pass
		bot.waiting_reactions[self.party_message.id] = self._process_reaction

	def cancel(self):
		"""Clean up all listeners when the match is cancelled."""
		import bot
		self.active = False
		if self.party_message:
			bot.waiting_reactions.pop(self.party_message.id, None)
		if self.pending_captain:
			_waiting_codes.pop((self.m.qc.id, self.pending_captain.id), None)
			self.pending_captain = None

	# ── Party-up embed (Image 2) ──────────────────────────────────────────────

	def _party_embed(self):
		cap_a     = self.m.teams[0][0] if self.m.teams[0] else None
		cap_b     = self.m.teams[1][0] if self.m.teams[1] else None
		team_size = self.m.cfg.get('team_size', 6)

		lines = [
			f"**Captains**, react with {self.READY_EMOJI} once your team of **{team_size}** is partied up!\n",
		]
		if cap_a:
			lines.append(f"{cap_a.mention} — {_team_display(self.m.teams[0])}")
		if cap_b:
			lines.append(f"{cap_b.mention} — {_team_display(self.m.teams[1])}")
		lines += [
			"",
			"The **first team ready** will create the **Game 1 code**.",
			"The **second team** will create the **Game 2 (and Game 3) code**.",
		]

		embed = Embed(colour=Colour(0x2ecc71), description="\n".join(lines))
		embed.set_footer(text=f"Match {str(self.m.id).zfill(6)} · Best of 3")
		return embed

	# ── Reaction handler ──────────────────────────────────────────────────────

	async def _process_reaction(self, reaction, user, remove=False):
		if not self.active or str(reaction) != self.READY_EMOJI or remove:
			return

		# Only captains may react
		captains = [t[0] for t in self.m.teams[:2] if t]
		if user not in captains or user in self.ready_order:
			return

		self.ready_order.append(user)

		if len(self.ready_order) == 1:
			# First captain ready → they create Game 1 lobby
			await self._prompt_code(user, game_num=1)
		else:
			# Second captain ready → they create Games 2 & 3
			self.second_captain = user
			# If Game 1 already submitted, immediately ask for Game 2
			if self.game1_done:
				await self._prompt_code(user, game_num=2)

	# ── Code prompting ────────────────────────────────────────────────────────

	async def _prompt_code(self, captain, game_num: int):
		"""Ask the captain to type their lobby code in the channel."""
		self.pending_captain = captain
		self.pending_game    = game_num

		# Register message listener
		_waiting_codes[(self.m.qc.id, captain.id)] = self

		channel = dc.get_channel(self.m.qc.id)
		if channel is None:
			log.error(f"PartyCode: could not find channel {self.m.qc.id}")
			return

		# Determine which team this captain leads for the display string
		captain_team = next((t for t in self.m.teams[:2] if t and captain in t), None)
		team_str = _team_display(captain_team) if captain_team else captain.display_name

		try:
			if game_num == 1:
				embed = Embed(
					colour=Colour(0x2ecc71),
					description=(
						f"✅ {captain.mention}'s team ({team_str}) is ready!\n"
						f"{captain.mention}, reply to this message with the **Game 1 code**."
					)
				)
				await channel.send(embed=embed)
			else:
				embed = Embed(
					colour=Colour(0x2ecc71),
					description=f"{captain.mention}, reply to this message with the **Game {game_num} code**."
				)
				await channel.send(embed=embed)
		except DiscordException as e:
			log.error(f"PartyCode: failed to send code prompt: {e}")

	# ── Code reception ────────────────────────────────────────────────────────

	async def _receive_code(self, message):
		"""Called by handle_code_input when a captain types their code."""
		if not self.active:
			return

		code = message.content.strip().upper()

		# Defensive re-check — handle_code_input filters out non-matching
		# messages, but if somehow an invalid one gets through, bail without
		# clearing the pending captain so they can retry.
		if not _is_valid_code(code):
			try:
				await message.channel.send(
					f"{message.author.mention} that code doesn\u2019t look right. "
					"Codes must be exactly **6 characters** using "
					"**A-Z and 0-9** only (e.g. `AB12C3`)."
				)
			except Exception:
				pass
			return
		game_num  = self.pending_game
		captain   = self.pending_captain
		self.pending_captain = None

		# Post the code embed
		await self._post_code_embed(message.channel, code, game_num, captain)

		if game_num == 1:
			self.game1_done = True
			# Always prompt the other captain for Game 2 — whether they reacted ✅ or not.
			# The Game 1 embed already told them to reply, so the listener must be ready.
			other_cap = next(
				(t[0] for t in self.m.teams[:2] if t and t[0] != captain), None
			)
			if other_cap:
				if self.second_captain is None:
					self.second_captain = other_cap
				await self._prompt_code(self.second_captain, game_num=2)
		elif game_num == 2:
			# Same second captain submits Game 3
			await self._prompt_code(captain, game_num=3)
		# game_num == 3 → no further prompts needed

	# ── Game Code embed (Image 3) ─────────────────────────────────────────────

	async def _post_code_embed(self, channel, code: str, game_num: int, captain):
		captains      = [t[0] for t in self.m.teams[:2] if t]
		other_captain = next((c for c in captains if c != captain), None)

		if game_num == 1 and other_captain:
			next_line = (
				f"{other_captain.mention}, reply to this message when you create the **Game 2 code**."
			)
		elif game_num == 2:
			next_line = (
				f"{captain.mention}, reply to this message when you create the **Game 3 code**."
			)
		else:
			next_line = ""

		# Use a heading for the large code display (Discord renders # as large text in embeds)
		description = f"# {code.upper()}"
		if next_line:
			description += f"\n\n{next_line}"

		embed = Embed(
			title=f"Game {game_num} Code",
			description=description,
			colour=Colour(0x992d22),
		)
		embed.set_footer(
			text=f"Match {str(self.m.id).zfill(6)} · Code set by {get_nick(captain)}"
		)

		try:
			await channel.send(embed=embed)
		except DiscordException as e:
			log.error(f"PartyCode: failed to post code embed: {e}")
