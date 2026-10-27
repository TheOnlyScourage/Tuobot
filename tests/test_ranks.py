# -*- coding: utf-8 -*-
"""The rank ladder (Oct 2026: Platinum added at 1600, lower tiers shifted) and
the guard that keeps it defined in exactly one place. bot/commands/stats.py
used to carry a private copy that silently drifted from constants — the
leaderboard, /top and /profile read THAT copy, so a constants-only edit would
have left them on the old ladder."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = [
	(0, "CHAD"), (600, "Q6Wood"), (800, "Q6Iron"), (1000, "Q6Bronze"), (1200, "Q6Silver"),
	(1400, "Q6Gold"), (1600, "Q6Platinum"), (1800, "Q6Diamond"), (2000, "Q6Champion"), (2200, "Q6Star"),
]


def test_ladder_thresholds_and_order(constants):
	got = [(t, re.match(r"<:(\w+):", e).group(1)) for t, e in constants.RANK_EMOJIS]
	assert got == EXPECTED
	thresholds = [t for t, _ in constants.RANK_EMOJIS]
	assert thresholds == sorted(thresholds)


def test_get_rank_emoji_boundaries(constants):
	name = lambda r: re.match(r"<:(\w+):", constants.get_rank_emoji(r)).group(1)  # noqa: E731
	assert name(599) == "CHAD" and name(600) == "Q6Wood"
	assert name(1399) == "Q6Silver" and name(1400) == "Q6Gold"
	assert name(1599) == "Q6Gold" and name(1600) == "Q6Platinum"
	assert name(1799) == "Q6Platinum" and name(1800) == "Q6Diamond"
	assert name(2199) == "Q6Champion" and name(2200) == "Q6Star" and name(3000) == "Q6Star"
	assert name(None) == "CHAD" and name(-5) == "CHAD"


def test_emoji_codes_well_formed(constants):
	for _, e in constants.RANK_EMOJIS:
		assert re.fullmatch(r"<:\w+:\d+>", e), e


def test_ladder_is_defined_exactly_once():
	hits = []
	for path in (ROOT / "bot").rglob("*.py"):
		for line in path.read_text(encoding="utf-8").splitlines():
			if re.match(r"\s*RANK_EMOJIS\s*=\s*\[", line):
				hits.append(path.relative_to(ROOT).as_posix())
	assert hits == ["bot/constants.py"], hits


def test_profile_colours_cover_every_rank(constants):
	src = (ROOT / "bot" / "stats" / "profile_card.py").read_text(encoding="utf-8")
	for _, e in constants.RANK_EMOJIS:
		name = re.match(r"<:(?:Q6)?([A-Za-z]+):", e).group(1).capitalize()
		assert f"'{name}':" in src, f"RANK_COLOURS lacks {name}"
