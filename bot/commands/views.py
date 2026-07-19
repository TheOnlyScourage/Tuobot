# -*- coding: utf-8 -*-
"""
Interactive UI Views for commands.

First (and so far only) resident: LeaderboardView — button pagination for the
leaderboard embeds (⏮ ◀ ▶ ⏭ to flip pages, 🔍 Me to jump to and highlight
the presser's own row, and an optional 🔁 toggle that flips the whole view
between two boards — e.g. All Players ↔ the season-qualified board).

Design notes:
  - The View owns paging state and button wiring ONLY. It renders through an
    injected `make_embed(data_slice, page, pages, highlight_uid)` callable, so
    this module imports nothing from the bot package (no circular import with
    bot/commands/stats.py, and the View is unit-testable with just nextcord
    installed).
  - Data is a snapshot taken when the command runs. Flipping pages never
    re-queries the DB — a leaderboard that's a few seconds stale while someone
    browses is fine, and it keeps button presses instant.
  - Anyone in the channel may press the buttons (it's a shared board). The
    🔍 Me button acts on the PRESSER, not the original invoker.
  - After `timeout` seconds of no presses the buttons grey out (the message
    itself stays).
"""

from math import ceil

import nextcord


class LeaderboardView(nextcord.ui.View):

	def __init__(
		self, *, data: list, make_embed, per_page: int = 12, start_page: int = 0,
		timeout: float = 300, alt_data: list | None = None, alt_make_embed=None,
		toggle_to_alt_label: str = "Season", toggle_to_main_label: str = "All Players",
		not_found_alt: str | None = None, start_on_alt: bool = False,
	):
		"""
		Args:
		  data:        full leaderboard rows (each row dict carries 'user_id').
		  make_embed:  callable(data_slice, page, pages, highlight_uid) -> Embed.
		  per_page:    rows per page (the classic table fits 12).
		  start_page:  0-based page to open on; clamped into range.
		  timeout:     seconds of inactivity before the buttons disable.
		  alt_data / alt_make_embed: optional second board; providing BOTH adds
		    a 🔁 toggle button (on its own row) that flips the whole view
		    between the two boards.
		  toggle_to_alt_label / toggle_to_main_label: the toggle's label always
		    names the board pressing it switches TO.
		  not_found_alt: 🔍 Me's ephemeral message while on the alt board
		    (None -> the default message on both boards).
		  start_on_alt: open on the alt board (ignored when there is no alt).
		"""
		super().__init__(timeout=timeout)
		self.data = data
		self.make_embed = make_embed
		self.per_page = per_page
		self._alt_data = alt_data
		self._alt_make_embed = alt_make_embed
		self._to_alt_label = toggle_to_alt_label
		self._to_main_label = toggle_to_main_label
		self._not_found_alt = not_found_alt
		self._on_alt = False
		self._toggle_btn = None
		if alt_data is not None and alt_make_embed is not None:
			self._toggle_btn = nextcord.ui.Button(
				emoji="🔁", label=toggle_to_alt_label,
				style=nextcord.ButtonStyle.success, row=1,
			)
			self._toggle_btn.callback = self._toggle
			self.add_item(self._toggle_btn)
			if start_on_alt:
				self._swap_boards()
		self.pages = max(1, ceil(len(self.data) / per_page))
		self.page = min(max(start_page, 0), self.pages - 1)
		self.highlight_uid = None
		# Set by bind() after the initial reply; used to edit on timeout.
		self.message = None
		self._sync_buttons()

	# ── Rendering ─────────────────────────────────────────────────────────────

	def _slice(self):
		return self.data[self.page * self.per_page:(self.page + 1) * self.per_page]

	def render(self):
		return self.make_embed(self._slice(), self.page, self.pages, self.highlight_uid)

	def _sync_buttons(self):
		at_first = self.page <= 0
		at_last  = self.page >= self.pages - 1
		self.first_page.disabled = at_first
		self.prev_page.disabled  = at_first
		self.next_page.disabled  = at_last
		self.last_page.disabled  = at_last

	async def _goto(self, interaction: nextcord.Interaction, page: int):
		"""Jump to a page (clamped), clearing any find-me highlight."""
		self.page = min(max(page, 0), self.pages - 1)
		self.highlight_uid = None
		self._sync_buttons()
		await interaction.response.edit_message(embed=self.render(), view=self)

	def _swap_boards(self):
		"""Exchange the active and alternate boards (data + embed maker) and
		relabel the toggle to name the board it now switches TO."""
		self.data, self._alt_data = self._alt_data, self.data
		self.make_embed, self._alt_make_embed = self._alt_make_embed, self.make_embed
		self._on_alt = not self._on_alt
		if self._toggle_btn is not None:
			self._toggle_btn.label = self._to_main_label if self._on_alt else self._to_alt_label

	async def _toggle(self, interaction: nextcord.Interaction):
		"""🔁 — flip boards, landing on page one with highlights cleared."""
		self._swap_boards()
		self.pages = max(1, ceil(len(self.data) / self.per_page))
		self.page = 0
		self.highlight_uid = None
		self._sync_buttons()
		await interaction.response.edit_message(embed=self.render(), view=self)

	# ── Buttons ───────────────────────────────────────────────────────────────

	@nextcord.ui.button(emoji="⏮", style=nextcord.ButtonStyle.secondary)
	async def first_page(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
		await self._goto(interaction, 0)

	@nextcord.ui.button(emoji="◀", style=nextcord.ButtonStyle.secondary)
	async def prev_page(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
		await self._goto(interaction, self.page - 1)

	@nextcord.ui.button(emoji="▶", style=nextcord.ButtonStyle.secondary)
	async def next_page(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
		await self._goto(interaction, self.page + 1)

	@nextcord.ui.button(emoji="⏭", style=nextcord.ButtonStyle.secondary)
	async def last_page(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
		await self._goto(interaction, self.pages - 1)

	@nextcord.ui.button(emoji="🔍", label="Me", style=nextcord.ButtonStyle.primary)
	async def find_me(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
		"""Jump to the presser's row and highlight it."""
		uid = interaction.user.id
		idx = next((i for i, row in enumerate(self.data) if row.get('user_id') == uid), None)
		if idx is None:
			if self._on_alt and self._not_found_alt:
				msg = self._not_found_alt
			else:
				msg = "You're not on this leaderboard yet — play a ranked match to get placed!"
			await interaction.response.send_message(msg, ephemeral=True)
			return
		self.page = idx // self.per_page
		self.highlight_uid = uid
		self._sync_buttons()
		await interaction.response.edit_message(embed=self.render(), view=self)

	# ── Lifecycle ─────────────────────────────────────────────────────────────

	async def bind(self, ctx):
		"""Grab the sent message (slash contexts only) so on_timeout can edit it.

		Safe on any Context: if there's no interaction or the fetch fails we
		just skip — worst case the buttons visually stay enabled after timeout
		and presses do nothing.
		"""
		interaction = getattr(ctx, 'interaction', None)
		if interaction is None:
			return
		try:
			self.message = await interaction.original_message()
		except Exception:
			pass

	async def on_timeout(self):
		for child in self.children:
			child.disabled = True
		if self.message is not None:
			try:
				await self.message.edit(view=self)
			except Exception:
				pass
