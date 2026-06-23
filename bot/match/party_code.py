# -*- coding: utf-8 -*-
"""
Handles the party-up phase and game code collection after draft ends.

Flow:
  1. PartyCode.start(ctx)   → posts the ✅ party-up embed (Image 2)
  2. Captain A reacts ✅    → their TEAM is prompted to type Game 1 lobby code
  3. Any teammate types code → Game 1 Code embed posted (Image 3)
                              Team B is told to reply with Game 2 code
  4. Captain B reacts ✅    → (can happen any time; code prompt fires when
                              both Game 1 is done AND B has reacted)
  5. Any teammate on B types code → Game 2 Code embed posted
  6. Same team types code   → Game 3 Code embed posted

Note: any member of the prompted team may submit the code, not only the
captain. The first valid 6-char code from any teammate is accepted.
"""
import re
import time
from bot.constants import HOUSE_EMOJIS
from nextcord import Embed, Colour, DiscordException
from core.client import dc
from core.utils import get_nick
from core.console import log

# House emblems — same as embeds.py (duplicated to avoid circular imports)
# HOUSE_EMOJIS centralized in bot/constants.py — imported below


def _team_display(team) -> str:
	"""Return '{house_emoji} **HOUSENAME (N)**' or '{emoji} **TeamName**'."""
	house_emoji = HOUSE_EMOJIS.get(team.name, '')
	if house_emoji:
		return f"{house_emoji} **{team.name} ({team.idx + 1})**"
	return f"{team.emoji} **{team.name}**"



# Module-level registry: {channel_id: PartyCode}
# Checked by events.py on_message to route team code inputs. Keyed by channel
# alone (not per-user) so ANY teammate on the pending team can submit the code;
# the PartyCode instance checks team membership itself in handle_code_input.
# Game codes must be exactly 6 characters, uppercase letters and digits only.
# We uppercase the input before checking so the captain can type either case.
CODE_PATTERN = re.compile(r"^[A-Z0-9]{6}$")

# Minimum seconds between two accepted game codes. Each game runs ~8 minutes,
# so the next game's code legitimately can't exist until the current game is
# done. This prevents the same code being pasted twice in quick succession
# from filling two game slots at once (Game 2 + Game 3 instant double-submit).
_CODE_COOLDOWN_SECONDS = 8 * 60


def _is_valid_code(text: str) -> bool:
	"""Return True if `text` (after strip+upper) is a valid 6-char code."""
	return bool(CODE_PATTERN.match(text.strip().upper()))


_waiting_codes: dict = {}


async def handle_code_input(message) -> bool:
	"""
	Called from events.py on_message.
	Returns True if the message was consumed as a game code, False otherwise.

	Only messages that match the strict 6-char A-Z/0-9 code format AND are
	authored by a member of the currently-prompted team are consumed.
	Anything else (random chatter, "one code pplease", etc.) is ignored so
	teammates can keep talking while waiting.
	"""
	pc = _waiting_codes.get(message.channel.id)
	if pc is None or not pc.active:
		return False

	# Only a member of the pending team may submit.
	if message.author.id not in pc.pending_team_ids:
		return False

	# Cooldown gate: if the next game's code window hasn't opened yet, silently
	# ignore the message (leave the listener in place so the next valid code
	# AFTER the window opens is still accepted). This stops a code pasted twice
	# in the same instant from filling two game slots at once.
	if time.time() < pc.next_code_allowed_at:
		return False

	if not _is_valid_code(message.content):
		return False  # not a code — leave the listener in place

	# Pop BEFORE handing off so a successful submission can't double-fire.
	_waiting_codes.pop(message.channel.id, None)
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
		self.pending_team_ids = set()     # user-ids allowed to submit right now
		self.next_code_allowed_at = 0.0   # epoch secs; codes ignored before this
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
		_waiting_codes.pop(self.m.qc.id, None)
		self.pending_captain = None
		self.pending_team_ids = set()

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
			# First captain ready → their team creates Game 1 lobby
			await self._prompt_code(user, game_num=1)
		else:
			# Second captain ready → their team creates Games 2 & 3
			self.second_captain = user
			# If Game 1 already submitted, immediately ask for Game 2
			if self.game1_done:
				await self._prompt_code(user, game_num=2)

	# ── Code prompting ────────────────────────────────────────────────────────

	def _team_of(self, captain):
		"""Return the team list that `captain` leads, or None."""
		return next((t for t in self.m.teams[:2] if t and captain in t), None)

	async def _prompt_code(self, captain, game_num: int):
		"""Ask the captain's team to type their lobby code in the channel."""
		self.pending_captain = captain
		self.pending_game    = game_num

		# Allow ANY member of this captain's team to submit the code.
		captain_team = self._team_of(captain)
		self.pending_team_ids = {p.id for p in captain_team} if captain_team else {captain.id}

		# Register message listener (keyed by channel — see _waiting_codes note).
		_waiting_codes[self.m.qc.id] = self

		channel = dc.get_channel(self.m.qc.id)
		if channel is None:
			log.error(f"PartyCode: could not find channel {self.m.qc.id}")
			return

		team_str = _team_display(captain_team) if captain_team else captain.display_name

		# If a cooldown is active (Game 3 decider), tell players when the code
		# window opens so a silently-ignored early paste doesn't look broken.
		wait_left = int(self.next_code_allowed_at - time.time())
		cooldown_note = ""
		if wait_left > 0:
			mins = max(1, round(wait_left / 60))
			cooldown_note = (
				f"\n\n*The Game {game_num} code can be submitted once Game "
				f"{game_num - 1} is done (~{mins} min). Codes sent before then "
				"are ignored.*"
			)

		try:
			if game_num == 1:
				embed = Embed(
					colour=Colour(0x2ecc71),
					description=(
						f"✅ {captain.mention}'s team ({team_str}) is ready!\n"
						f"Anyone on {team_str} can reply with the **Game 1 code**."
					)
				)
				await channel.send(embed=embed)
			else:
				embed = Embed(
					colour=Colour(0x2ecc71),
					description=(
						f"Anyone on {team_str} can reply with the **Game {game_num} code**."
						f"{cooldown_note}"
					)
				)
				await channel.send(embed=embed)
		except DiscordException as e:
			log.error(f"PartyCode: failed to send code prompt: {e}")

	# ── Code reception ────────────────────────────────────────────────────────

	async def _receive_code(self, message):
		"""Called by handle_code_input when a teammate types their code."""
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
		submitter = message.author          # actual person who typed the code
		self.pending_captain = None
		self.pending_team_ids = set()

		# Post the code embed, crediting the actual submitter.
		await self._post_code_embed(message.channel, code, game_num, submitter)

		if game_num == 1:
			self.game1_done = True
			# Always prompt the other team for Game 2 — whether the captain
			# reacted ✅ or not. The Game 1 embed already told them to reply.
			# No cooldown here: Game 1 and Game 2 lobbies are created up front.
			other_cap = next(
				(t[0] for t in self.m.teams[:2] if t and t[0] != captain), None
			)
			if other_cap:
				if self.second_captain is None:
					self.second_captain = other_cap
				await self._prompt_code(self.second_captain, game_num=2)
		elif game_num == 2:
			# Game 3 is the decider and only happens AFTER Games 1 & 2 are
			# actually played (~8 min each). Gate the Game 3 code window so a
			# code pasted twice in the same instant can't fill Game 3 off the
			# back of Game 2. Same team submits the decider code.
			self.next_code_allowed_at = time.time() + _CODE_COOLDOWN_SECONDS
			await self._prompt_code(self.second_captain or captain, game_num=3)
		# game_num == 3 → series over, no further prompts needed

	# ── Game Code embed (Image 3) ─────────────────────────────────────────────

	async def _post_code_embed(self, channel, code: str, game_num: int, submitter):
		captains      = [t[0] for t in self.m.teams[:2] if t]
		# Figure out which team the submitter belongs to, to address the other.
		submitter_team = next(
			(t for t in self.m.teams[:2] if t and submitter in t), None
		)
		submitter_cap = submitter_team[0] if submitter_team else submitter
		other_captain = next((c for c in captains if c != submitter_cap), None)

		if game_num == 1 and other_captain:
			next_line = (
				f"{other_captain.mention}, reply to this message when your team creates the **Game 2 code**."
			)
		elif game_num == 2:
			next_line = (
				f"{submitter_cap.mention}, your team will create the **Game 3 code**."
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
			text=f"Match {str(self.m.id).zfill(6)} · Code set by {get_nick(submitter)}"
		)

		try:
			await channel.send(embed=embed)
		except DiscordException as e:
			log.error(f"PartyCode: failed to post code embed: {e}")
