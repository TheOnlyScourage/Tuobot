# -*- coding: utf-8 -*-
"""
PNG profile card renderer for /profile — Mee6-style rank card, Q6 flavour.

Pure functions of their inputs: this module imports only Pillow + stdlib and
touches no Discord or database objects, so it's testable standalone (same
philosophy as mmr_engine / captain_selection). The /profile command in
bot/commands/stats.py gathers the data and calls render_profile_card();
aggregate_encounters() shapes the teammate/nemesis inputs from the joined
match rows.

Card anatomy (900x300):
  - house-themed gradient + accent bar + ghosted house-emblem watermark
    (the server's emoji art; big-initial fallback)
  - circular avatar (or initials disc) with accent ring
  - nick, "{House} • {Position}" subtitle
  - right block: current rating (big), rank name in rank colour, all-time peak
  - stats row: all-time W-L-D, win rate, current streak (+ best-ever note)
  - rating sparkline (last ~20 history points, spanning seasons)
  - most-teamed-with + nemesis footer, "Since <month>" footnote
  - career milestone regalia: winged badge above the avatar (shield -> wings
    -> stars -> crown at 1000; assets/badges/tier_N.png overrides the art)
    + DBD-style ornament around the avatar (ring -> studs -> star wreath ->
    radiating-spike engulf at 1000)

Rendering note: Pillow's ImageDraw does NOT alpha-blend — drawing an RGBA
colour writes that alpha into the pixels instead of compositing. Every
translucent element (watermark, sparkline fill, panel border) therefore goes
on a separate overlay that gets alpha_composite()d onto the card; only fully
opaque strokes are drawn directly.

Fonts: DejaVu Sans (regular + bold) bundled in assets/fonts/ — stick to
basic-latin glyphs here, DejaVu has no colour emoji.
"""

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CARD_W, CARD_H = 900, 300

# ── Career milestone regalia ─────────────────────────────────────────────────
# The winged badge above the avatar and the ornament around it upgrade as the
# all-time ranked-match count crosses each milestone. Thresholds
# MIRROR bot/stats/milestones.MATCH_MILESTONES — a committed test
# (test_profile_frame_tiers_match_milestones) keeps the two in sync via ast,
# since CI can't import this module (no PIL there).
REGALIA_TIERS = (
	(1000, 'champion'),
	(500,  'gold_double'),
	(250,  'gold'),
	(100,  'silver'),
	(50,   'bronze'),
)

# Avatar regalia per tier (DBD-portrait style): ornament grows AROUND the
# circular avatar — metal ring, then studs, then a bottom star wreath, and a
# full radiating-spike engulf at the summit. The top arc stays clear so the
# winged badge above the avatar caps the composition.
AVATAR_REGALIA = {
	'bronze':      dict(studs=0,  ring_stars=0, spikes=0),
	'silver':      dict(studs=8,  ring_stars=0, spikes=0),
	'gold':        dict(studs=8,  ring_stars=3, spikes=0),
	'gold_double': dict(studs=12, ring_stars=5, spikes=0),
	'champion':    dict(studs=12, ring_stars=5, spikes=18),
}

# Badge regalia per tier: wings appear and grow, stars join, and the crown
# arrives only at the summit — shield -> +wings -> +stars -> +crown.
BADGE_REGALIA = {
	'bronze':      dict(feathers=0, side_stars=0, crown=False, arc_stars=0),
	'silver':      dict(feathers=1, side_stars=0, crown=False, arc_stars=0),
	'gold':        dict(feathers=2, side_stars=0, crown=False, arc_stars=0),
	'gold_double': dict(feathers=3, side_stars=1, crown=False, arc_stars=0),
	'champion':    dict(feathers=3, side_stars=1, crown=True,  arc_stars=3),
}
BADGE_PALETTES = {  # (dark, base, light) metal shades per style
	'bronze':      ((94, 56, 30),   (176, 108, 62),  (222, 158, 104)),
	'silver':      ((104, 110, 122), (198, 203, 212), (241, 244, 249)),
	'gold':        ((140, 108, 26), (222, 184, 62),  (255, 226, 122)),
	'gold_double': ((140, 108, 26), (222, 184, 62),  (255, 226, 122)),
	'champion':    ((140, 108, 26), (222, 184, 62),  (255, 232, 140)),
}

# Optional pro-art override: drop transparent PNGs at
# assets/badges/tier_50.png ... tier_1000.png (any size; scaled to fit) and
# they replace the procedural badge entirely — same pattern as the bundled
# fonts. Missing/broken files silently fall back to the drawn badge.
_BADGE_DIR = Path(__file__).resolve().parents[2] / 'assets' / 'badges'


def _star_points(cx: float, cy: float, r_out: float, r_in: float, points: int = 5):
	"""Vertex list for a classic star polygon centred on (cx, cy)."""
	import math
	verts = []
	for i in range(points * 2):
		r = r_out if i % 2 == 0 else r_in
		a = math.pi / 2 * -1 + i * math.pi / points
		verts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
	return verts


def _paste_milestone_badge(card, style: str, tier: int) -> None:
	"""Compose the career badge (centred above the avatar): a metal shield
	with a star, gaining wings, flanking stars, and finally a crown as the
	tier climbs. No number — the regalia IS the tier. Uses an
	assets/badges/tier_N.png override when present."""
	layer = Image.new('RGBA', (176, 92), (0, 0, 0, 0))

	asset = None
	try:
		p = _BADGE_DIR / f'tier_{tier}.png'
		if p.exists():
			asset = Image.open(p).convert('RGBA')
	except Exception:
		asset = None

	if asset is not None:
		h = 78
		w = max(1, round(asset.width * h / asset.height))
		asset = asset.resize((min(w, 172), h))
		layer.alpha_composite(asset, ((176 - asset.width) // 2, 2))
	else:
		_draw_badge(layer, style)

	card.alpha_composite(layer, (14, 8))


def _draw_badge(layer, style: str) -> None:
	"""Procedural badge art. Deliberately stylized (flat game-icon look) —
	the asset override exists for anyone wanting painted-art fidelity."""
	d = ImageDraw.Draw(layer)
	dark, base, light = BADGE_PALETTES[style]
	regalia = BADGE_REGALIA[style]
	cx = 88

	# wings: layered feather quads fanning out and down from the shoulders
	for side in (-1, 1):
		for i in range(regalia['feathers']):
			spread = 20 + i * 15
			drop = 6 + i * 7
			length = 26 + i * 9
			shoulder = (cx + side * 20, 30 + drop - 4)
			tip = (cx + side * (20 + spread + length), 20 + drop + i * 4)
			d.polygon([
				shoulder,
				(cx + side * (20 + spread), 24 + drop),
				tip,
				(cx + side * (20 + spread * 0.8), 40 + drop),
			], fill=(light if i % 2 == 0 else base), outline=dark)

	# flanking stars at the wing tips
	if regalia['side_stars']:
		for side in (-1, 1):
			d.polygon(_star_points(cx + side * 74, 30, 9, 3.6), fill=light, outline=dark)

	# shield: pointed-bottom escutcheon, two-tone metal with an outline
	top, bot = 26, 82
	shield = [
		(cx - 24, top), (cx + 24, top), (cx + 26, top + 8),
		(cx + 22, bot - 20), (cx, bot), (cx - 22, bot - 20), (cx - 26, top + 8),
	]
	d.polygon(shield, fill=base, outline=dark)
	# upper sheen
	d.polygon([
		(cx - 24, top), (cx + 24, top), (cx + 25, top + 6),
		(cx + 18, top + 22), (cx - 18, top + 22), (cx - 25, top + 6),
	], fill=light)
	d.line([(cx - 24, top), (cx + 24, top)], fill=light, width=2)

	# centre star
	d.polygon(_star_points(cx, top + 27, 14, 5.6), fill=light, outline=dark)
	d.polygon(_star_points(cx, top + 27, 7, 2.8), fill=base)

	# crown — the summit only
	if regalia['crown']:
		bx0, bx1, by = cx - 20, cx + 20, top - 2
		d.polygon([
			(bx0, by), (bx0, by - 12), (cx - 10, by - 5), (cx, by - 15),
			(cx + 10, by - 5), (bx1, by - 12), (bx1, by),
		], fill=(255, 214, 74), outline=dark)
		d.ellipse((cx - 3, by - 8, cx + 3, by - 2), fill=(206, 44, 62), outline=dark)

	# arc of stars above everything
	for i in range(regalia['arc_stars']):
		off = (i - (regalia['arc_stars'] - 1) / 2)
		sx = cx + off * 26
		sy = 8 + abs(off) * 3
		r = 6 if off == 0 else 4.5
		d.polygon(_star_points(sx, sy, r, r * 0.42), fill=(255, 226, 122), outline=dark)


def _draw_avatar_regalia(card, style: str) -> None:
	"""DBD-style portrait ornament around the circular avatar (centre
	(100,150), ring r≈60). Grows with the tier: metal ring -> studs -> a
	bottom star wreath -> a full radiating-spike engulf at champion. Every
	element skips the top arc (±55° from straight up) so the badge above the
	avatar stays unobstructed — the composition reads badge-crowns-portrait."""
	import math
	d = ImageDraw.Draw(card)
	dark, base, light = BADGE_PALETTES[style]
	spec = AVATAR_REGALIA[style]
	cx, cy = 100, 150

	def on_circle(r: float, deg: float):
		a = math.radians(deg - 90)  # 0° = straight up, clockwise
		return (cx + r * math.cos(a), cy + r * math.sin(a))

	def top_gap(deg: float) -> bool:
		return min(deg % 360, 360 - deg % 360) < 55

	# radiating spikes (champion) — behind the ring, alternating lengths
	if spec['spikes']:
		for i in range(spec['spikes']):
			deg = i * 360 / spec['spikes']
			if top_gap(deg):
				continue
			tip = 86 if i % 2 == 0 else 77
			d.polygon([
				on_circle(64, deg - 5), on_circle(tip, deg), on_circle(64, deg + 5),
			], fill=(base if i % 2 == 0 else dark), outline=dark)

	# the metal ring itself (every tier from bronze up)
	d.ellipse((cx - 68, cy - 68, cx + 68, cy + 68), outline=dark, width=6)
	d.ellipse((cx - 67, cy - 67, cx + 67, cy + 67), outline=base, width=4)

	# studs: small outward triangles around the ring
	if spec['studs']:
		for i in range(spec['studs']):
			deg = i * 360 / spec['studs']
			if top_gap(deg):
				continue
			d.polygon([
				on_circle(68, deg - 4), on_circle(77, deg), on_circle(68, deg + 4),
			], fill=light, outline=dark)

	# bottom star wreath (gold and up), centred under the portrait
	if spec['ring_stars']:
		n = spec['ring_stars']
		for i in range(n):
			deg = 180 + (i - (n - 1) / 2) * 26
			sx, sy = on_circle(72, deg)
			r = 9 if i == (n - 1) // 2 else 6.5
			d.polygon(_star_points(sx, sy, r, r * 0.42), fill=light, outline=dark)


def regalia_style(career_matches: int) -> str | None:
	"""Regalia style earned by an all-time ranked-match count (None below the
	first milestone — a bare card is itself the starting tier)."""
	for threshold, style in REGALIA_TIERS:
		if career_matches >= threshold:
			return style
	return None

# Default font location: <repo root>/assets/fonts
_FONT_DIR = Path(__file__).resolve().parents[2] / 'assets' / 'fonts'

# house → (bg_top, bg_bottom, accent) — dark crest palettes that keep white
# text readable; None = houseless neutral (Tuobot blurple accent).
HOUSE_THEMES = {
	'Gryffindor': ((74, 10, 12),  (38, 4, 5),    (211, 166, 37)),
	'Slytherin':  ((15, 46, 28),  (7, 26, 16),   (192, 201, 196)),
	'Ravenclaw':  ((14, 26, 64),  (7, 14, 36),   (148, 107, 45)),
	'Hufflepuff': ((55, 46, 41),  (32, 27, 24),  (236, 185, 57)),
	None:         ((43, 45, 49),  (30, 31, 34),  (114, 137, 218)),
}

# rank name (parsed from the RANK_EMOJIS names) → badge colour.
RANK_COLOURS = {
	'Chad':     (96, 125, 139),
	'Wood':     (139, 90, 43),
	'Iron':     (150, 156, 160),
	'Bronze':   (205, 127, 50),
	'Silver':   (192, 192, 192),
	'Gold':     (255, 215, 0),
	'Diamond':  (78, 226, 236),
	'Champion': (224, 17, 95),
	'Star':     (255, 241, 118),
}

WHITE = (255, 255, 255)
GREY  = (148, 155, 164)
GREEN = (87, 242, 135)
RED   = (237, 66, 69)

SPARK_PANEL = (190, 232, 560, 284)
SPARK_POINTS = 20


# ══════════════════════════════════════════════════════════════════════════════
#  Pure data shaping (card inputs from joined match rows)
# ══════════════════════════════════════════════════════════════════════════════

def aggregate_encounters(rows) -> tuple[tuple | None, tuple | None]:
	"""Reduce joined player-match rows into the (teammate, nemesis) card inputs.

	`rows`: dicts with other_id, other_nick, same_team (truthy), winner
	(0/1/None) and my_team (0/1), ordered chronologically so the latest nick
	for a player wins.

	teammate = most games together on the same team → (nick, count)
	nemesis  = the opponent who has beaten us the most → (nick, my_wins,
	my_losses); ties broken by worst net record. None until somebody has
	actually beaten us. Draws and unranked matches (winner NULL) count for
	the teammate tally but not for the nemesis W-L.
	"""
	mates: dict[int, list] = {}   # other_id -> [nick, games_together]
	foes:  dict[int, list] = {}   # other_id -> [nick, my_wins_vs, my_losses_vs]
	for r in rows:
		if r['same_team']:
			entry = mates.setdefault(r['other_id'], [r['other_nick'], 0])
			entry[0] = r['other_nick']
			entry[1] += 1
		else:
			entry = foes.setdefault(r['other_id'], [r['other_nick'], 0, 0])
			entry[0] = r['other_nick']
			if r['winner'] is not None:
				if r['winner'] == r['my_team']:
					entry[1] += 1
				else:
					entry[2] += 1

	teammate = None
	if mates:
		nick, count = max(mates.values(), key=lambda v: v[1])
		teammate = (nick, count)

	nemesis = None
	beat_us = [v for v in foes.values() if v[2] > 0]
	if beat_us:
		nick, w, losses = max(beat_us, key=lambda v: (v[2], v[2] - v[1]))
		nemesis = (nick, w, losses)

	return teammate, nemesis


def summarize_results(rows) -> dict:
	"""Reduce a player's chronological match rows into all-time card numbers.

	`rows`: dicts with winner (0/1/None), team (0/1), ranked (truthy) and at
	(unix ts), ordered chronologically.

	Only RANKED rows count toward the record and the best win streak;
	unranked rows are skipped entirely. Winner-NULL ranked rows are ABORTS
	(Q6 has no draws): they're tallied in the third record slot (kept under
	the `draws` name for schema continuity) but are INVISIBLE to streaks —
	a cancelled match doesn't break momentum. `first_at` is the earliest
	appearance of ANY kind — "playing since" includes unranked days.

	Returns dict(wins, losses, draws, best_streak, first_at) with first_at
	None when there are no rows at all; `draws` is the ABORT count.
	"""
	wins = losses = draws = 0
	best_streak = cur_streak = 0
	first_at = None
	for r in rows:
		at = r.get('at')
		if at is not None and (first_at is None or at < first_at):
			first_at = at
		if not r.get('ranked'):
			continue
		winner = r['winner']
		if winner is None:
			draws += 1
		elif winner == r['team']:
			wins += 1
			cur_streak += 1
			best_streak = max(best_streak, cur_streak)
		else:
			losses += 1
			cur_streak = 0
	return dict(wins=wins, losses=losses, draws=draws, best_streak=best_streak, first_at=first_at)


# ══════════════════════════════════════════════════════════════════════════════
#  Rendering helpers
# ══════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=32)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
	return ImageFont.truetype(path, size)


def _fonts(font_dir: Path):
	reg  = str(font_dir / 'DejaVuSans.ttf')
	bold = str(font_dir / 'DejaVuSans-Bold.ttf')
	return (
		lambda size: _font(reg, size),
		lambda size: _font(bold, size),
	)


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
	if draw.textlength(text, font=font) <= max_w:
		return text
	while text and draw.textlength(text + '…', font=font) > max_w:
		text = text[:-1]
	return text + '…'


def _gradient(top, bottom) -> Image.Image:
	"""Vertical two-colour gradient base."""
	base = Image.new('RGB', (CARD_W, CARD_H))
	for y in range(CARD_H):
		t = y / (CARD_H - 1)
		base.paste(
			tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
			(0, y, CARD_W, y + 1)
		)
	return base.convert('RGBA')


def _spark_coords(history) -> list[tuple[float, float]] | None:
	"""Panel-space polyline coordinates for the last SPARK_POINTS ratings,
	or None when there aren't at least two points to connect."""
	points = list(history)[-SPARK_POINTS:]
	if len(points) < 2:
		return None
	x0, y0, x1, y1 = SPARK_PANEL
	lo, hi = min(points), max(points)
	span = (hi - lo) or 1
	pad_x, pad_y = 10, 8
	w = (x1 - x0) - 2 * pad_x
	h = (y1 - y0) - 2 * pad_y
	return [
		(
			x0 + pad_x + (w * i / (len(points) - 1)),
			y1 - pad_y - (h * (val - lo) / span),
		)
		for i, val in enumerate(points)
	]


def _paste_avatar(card, draw, avatar_bytes, nick, accent, bold):
	"""Circular avatar at (40,90)-(160,210), or an initials disc fallback."""
	box = (40, 90, 160, 210)
	size = box[2] - box[0]
	if avatar_bytes:
		try:
			av = Image.open(io.BytesIO(avatar_bytes)).convert('RGBA').resize((size, size))
			mask = Image.new('L', (size, size), 0)
			ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
			card.paste(av, box[:2], mask)
		except Exception:
			avatar_bytes = None
	if not avatar_bytes:
		draw.ellipse(box, fill=tuple(c // 2 for c in accent))
		initials = (nick[:2] or '??').upper()
		f = bold(40)
		tw = draw.textlength(initials, font=f)
		draw.text((box[0] + (size - tw) / 2, box[1] + size / 2 - 26), initials, font=f, fill=WHITE)
	draw.ellipse(box, outline=accent, width=4)


# ══════════════════════════════════════════════════════════════════════════════
#  The card
# ══════════════════════════════════════════════════════════════════════════════

def render_profile_card(
	*, nick: str, house: str | None, position: str, rank_name: str, rating: int,
	wins: int, losses: int, draws: int, streak: int,
	peak: int | None = None, best_streak: int | None = None, history=(),
	teammate=None, nemesis=None, avatar_bytes: bytes | None = None,
	emblem_bytes: bytes | None = None, career_matches: int = 0,
	footnote: str | None = None, font_dir: Path | None = None,
) -> bytes:
	"""Render the card; returns PNG bytes. All stats are ALL-TIME (across
	seasons) except `rating`/`streak`, which are the player's current state.
	`draws` is the ABORT count (Q6 has no draws; the name matches the schema).

	peak:        all-time highest rating (small line under the rank name)
	best_streak: all-time longest win streak (tiny note under STREAK)
	teammate:    (nick, games_together) or None
	nemesis:     (nick, my_wins_vs, my_losses_vs) or None
	history:     chronological rating values (sparkline uses the last 20)
	career_matches: all-time ranked count — drives the milestone regalia:
	             the BADGE above the avatar (bronze shield 50 → wings 100 →
	             bigger wings 250 → wings+stars 500 → crowned champion 1000;
	             assets/badges/tier_N.png replaces the drawn art) and the
	             AVATAR ornament (metal ring 50 → studs 100 → star wreath
	             250/500 → radiating spikes at 1000)
	footnote:    bottom-left stamp, e.g. "Since Mar 2026 • 214 ranked matches"
	"""
	font_dir = Path(font_dir) if font_dir else _FONT_DIR
	regular, bold = _fonts(font_dir)
	bg_top, bg_bottom, accent = HOUSE_THEMES.get(house, HOUSE_THEMES[None])
	rank_col = RANK_COLOURS.get(rank_name, accent)
	spark = _spark_coords(history)

	card = _gradient(bg_top, bg_bottom)

	# ── translucent layer: watermark, sparkline panel + fill ──────────────────
	overlay = Image.new('RGBA', (CARD_W, CARD_H), (0, 0, 0, 0))
	odraw = ImageDraw.Draw(overlay)

	# Watermark: the house EMBLEM (the server's custom-emoji art, fetched by
	# the command and passed as bytes) ghosted on the right in its own
	# colours. Falls back to the big house initial (or Q6) when no emblem is
	# available or the bytes don't decode.
	emblem_drawn = False
	if emblem_bytes:
		try:
			em = Image.open(io.BytesIO(emblem_bytes)).convert('RGBA')
			h = 240
			w = max(1, round(em.width * h / em.height))
			em = em.resize((w, h))
			# keep the emblem's colours; soften by scaling its alpha channel.
			# NOTE: paste WITHOUT a mask — the alpha is already in `em`, and
			# passing it again as the mask applies the ghosting twice (0.42²)
			# while premultiplying the colours toward black (the watermark
			# came out invisible). Verbatim paste + the final
			# alpha_composite = one true 42% ghost.
			em.putalpha(em.getchannel('A').point(lambda a: int(a * 0.42)))
			overlay.paste(em, (CARD_W - w - 34, 28))
			emblem_drawn = True
		except Exception:
			emblem_drawn = False
	if not emblem_drawn:
		mark = house[0].upper() if house else 'Q6'
		f_mark = bold(230)
		mw = odraw.textlength(mark, font=f_mark)
		odraw.text((CARD_W - mw - 36, 8), mark, font=f_mark, fill=accent + (26,))

	odraw.rounded_rectangle(SPARK_PANEL, radius=8, outline=(255, 255, 255, 36), width=1)
	if spark:
		x0, _, x1, y1 = SPARK_PANEL
		fill_poly = spark + [(spark[-1][0], y1 - 2), (spark[0][0], y1 - 2)]
		odraw.polygon(fill_poly, fill=accent + (36,))

	card = Image.alpha_composite(card, overlay)
	draw = ImageDraw.Draw(card)

	# ── opaque layer ──────────────────────────────────────────────────────────
	draw.rectangle((0, 0, CARD_W, 6), fill=accent)

	_paste_avatar(card, draw, avatar_bytes, nick, accent, bold)

	# identity
	name = _truncate(draw, nick, bold(42), 380)
	draw.text((190, 58), name, font=bold(42), fill=WHITE)
	subtitle = f"{house} • {position}" if house else position
	draw.text((192, 114), subtitle, font=regular(24), fill=accent)

	# right block: rating / rank / place
	f_rating = bold(54)
	r_text = str(rating)
	draw.text((860 - draw.textlength(r_text, font=f_rating), 40), r_text, font=f_rating, fill=WHITE)
	f_rank = bold(26)
	rank_text = rank_name.upper()
	draw.text((860 - draw.textlength(rank_text, font=f_rank), 104), rank_text, font=f_rank, fill=rank_col)
	if peak:
		p_text = f"Peak {peak}"
		f_peak = regular(16)
		draw.text((860 - draw.textlength(p_text, font=f_peak), 140), p_text, font=f_peak, fill=GREY)

	# stats row
	total = wins + losses
	wr = int(wins * 100 / total) if total else 0
	if streak > 0:
		streak_text, streak_col = f"W{streak}", GREEN
	elif streak < 0:
		streak_text, streak_col = f"L{-streak}", RED
	else:
		streak_text, streak_col = "—", GREY
	stats = [
		("RECORD", f"{wins}-{losses}-{draws}", WHITE),
		("WIN RATE", f"{wr}%", WHITE),
		("STREAK", streak_text, streak_col),
	]
	# Column stops are FIXED so cards align when compared side by side; the
	# RECORD value alone grows with a career ("612-380-12"), so each value
	# steps its font down until it fits the lane instead of spilling into
	# the next column (readability floor: 16).
	stops = (190, 345, 480)
	lane = stops[1] - stops[0] - 16
	for (label, value, col), x in zip(stats, stops):
		draw.text((x, 166), label, font=regular(15), fill=GREY)
		size = 27
		while size > 16 and draw.textlength(value, font=bold(size)) > lane:
			size -= 1
		draw.text((x, 186 + (27 - size)), value, font=bold(size), fill=col)
	if best_streak and best_streak >= 1:
		draw.text((stops[2], 218), f"best W{best_streak}", font=regular(13), fill=GREY)

	# sparkline stroke (fill already composited underneath)
	if spark:
		draw.line(spark, fill=accent, width=2)
		for px, py in (spark[0], spark[-1]):
			draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=accent)
	else:
		draw.text(
			(SPARK_PANEL[0] + 12, (SPARK_PANEL[1] + SPARK_PANEL[3]) // 2 - 8),
			"no rating history yet", font=regular(14), fill=GREY
		)

	# teammate / nemesis footer
	fx = 590
	draw.text((fx, 176), "MOST TEAMED", font=regular(15), fill=GREY)
	mate = f"{teammate[0]}  ({teammate[1]}x)" if teammate else "—"
	draw.text((fx, 196), _truncate(draw, mate, regular(20), 280), font=regular(20), fill=WHITE)
	draw.text((fx, 232), "NEMESIS", font=regular(15), fill=GREY)
	foe = f"{nemesis[0]}  ({nemesis[1]}-{nemesis[2]})" if nemesis else "—"
	draw.text((fx, 252), _truncate(draw, foe, regular(20), 280), font=regular(20), fill=WHITE)

	# footnote (bottom-left): "Since Mar 2026 • 214 ranked matches"
	if footnote:
		draw.text((16, 280), footnote, font=regular(13), fill=GREY)

	# ── career milestone regalia (drawn last, over everything): the winged
	# badge crowning the avatar + the DBD-style ornament around the portrait ──
	style = regalia_style(career_matches)
	if style:
		_draw_avatar_regalia(card, style)
		tier = next(t for t, _s in REGALIA_TIERS if career_matches >= t)
		_paste_milestone_badge(card, style, tier)

	buf = io.BytesIO()
	card.convert('RGB').save(buf, format='PNG')
	return buf.getvalue()
