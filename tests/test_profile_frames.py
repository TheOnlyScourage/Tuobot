# -*- coding: utf-8 -*-
"""The /profile milestone frames (bot/stats/profile_card.py + assets/frame_*.png).

CI has no Pillow, so this reads the module with ast and the PNG headers by hand:
- the frame tiers mirror bot/stats/milestones.MATCH_MILESTONES
- every tier's frame is in assets/ as a FRAME_CANVAS-square PNG with transparency
- the framed avatar fills the frame's opening and the frame fits on the card"""
import ast
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _literals(rel):
	"""Module-level `NAME = <literal>` assignments of a source file."""
	out = {}
	for node in ast.parse((ROOT / rel).read_text(encoding="utf-8")).body:
		if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
			try:
				out[node.targets[0].id] = ast.literal_eval(node.value)
			except ValueError:
				pass
	return out


CARD = _literals("bot/stats/profile_card.py")


def test_frame_tiers_match_the_milestones():
	milestones = _literals("bot/stats/milestones.py")["MATCH_MILESTONES"]
	assert sorted(t for t, _style in CARD["REGALIA_TIERS"]) == sorted(milestones)


def test_every_tier_has_its_frame_png():
	canvas = CARD["FRAME_CANVAS"]
	for _threshold, style in CARD["REGALIA_TIERS"]:
		path = ROOT / "assets" / f"frame_{style}.png"
		assert path.is_file(), f"missing assets/{path.name}: upload it to the assets folder"
		head = path.read_bytes()[:26]
		assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} isn't a PNG"
		width, height = struct.unpack(">II", head[16:24])
		assert (width, height) == (canvas, canvas), f"{path.name} is {width}x{height}, not {canvas}x{canvas}"
		assert head[25] == 6, f"{path.name} has no transparency (needs RGBA)"


def test_the_framed_avatar_fills_the_opening():
	opening = CARD["FRAME_OPENING"] * CARD["FRAME_SIZE"] / CARD["FRAME_CANVAS"]
	# a little bigger than the opening (its edge hides under the ring), never past the ring
	assert opening < CARD["FRAMED_AVATAR_R"] <= opening + 3
	assert CARD["FRAME_SIZE"] % 2 == 0, "an odd size puts the opening half a pixel off the avatar"
	cx, cy = CARD["AVATAR_CENTRE"]
	half = CARD["FRAME_SIZE"] // 2
	assert cx - half >= 0 and cy - half >= 0, "the frame would be pasted off the card"
