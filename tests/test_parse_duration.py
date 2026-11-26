# -*- coding: utf-8 -*-
"""core/utils.parse_duration: valid units, the 'Y' fix, and the freeze guard.

core/utils.py imports nextcord, so the function (and its length cap) are lifted
out with ast and run on their own; they only need `re` and `timedelta`.

Nov 2026: the old pattern `^(\\d+\\w ?)+$` backtracked exponentially on a run
of digits (`\\w` matches digits too). About 50 digits froze the whole bot for
~20 minutes, and any player could reach it through /remove_after. The timing
tests below make sure it can't come back."""
import ast
import re
import time
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _namespace():
	tree = ast.parse((ROOT / "core" / "utils.py").read_text(encoding="utf-8"))
	keep = [
		n for n in tree.body
		if (isinstance(n, ast.FunctionDef) and n.name == "parse_duration")
		or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "_MAX_DURATION_LEN" for t in n.targets))
	]
	ns = {"re": re, "timedelta": timedelta}
	exec(compile(ast.Module(body=keep, type_ignores=[]), "core/utils.py", "exec"), ns)
	return ns


parse_duration = _namespace()["parse_duration"]


@pytest.mark.parametrize("text, seconds", [
	("30s", 30), ("10m", 600), ("2h30m", 9000), ("1h 30m", 5400), ("1d", 86400),
	("2W", 2 * 7 * 86400), ("1M", 30 * 86400), ("01:30:00", 5400), ("12h30m15s", 45015),
])
def test_valid_durations(text, seconds):
	assert parse_duration(text) == timedelta(seconds=seconds)


def test_year_is_365_days():
	# was 365*24*60 seconds (about 6 days): `/noadds add duration:1Y` banned for under a week
	assert parse_duration("1Y") == timedelta(days=365)
	assert parse_duration("2Y") == timedelta(days=730)


def test_inf_keeps_its_old_meaning():
	assert parse_duration("inf") == 0  # /noadds treats falsy as its 2-hour default


@pytest.mark.parametrize("bad", ["", "abc", "12", "5x", "1h!", "h1", "1.5h", "-1h", "1h,2m"])
def test_invalid_raises_valueerror(bad):
	with pytest.raises(ValueError):
		parse_duration(bad)


def test_too_large_for_a_timedelta_is_a_clean_error():
	with pytest.raises(ValueError):  # used to escape as OverflowError ("RuntimeError" in Discord)
		parse_duration("9" * 31 + "Y")


# Hostile inputs stay at most 36 digits long ON PURPOSE: the old pattern needs
# ~1.3 s for 36 digits (a clear failure against the limits below), but it would
# never finish on thousands of digits, so a regression must fail, not hang CI.
@pytest.mark.parametrize("hostile", ["1" * 31 + "!", "1" * 36 + "!", "1a" * 16 + "!", "1h" * 20, "9" * 40])
def test_hostile_input_returns_instantly(hostile):
	start = time.perf_counter()
	with pytest.raises(ValueError):
		parse_duration(hostile)
	assert time.perf_counter() - start < 0.05


def test_the_pattern_itself_is_linear_without_the_length_cap():
	"""Belt and braces: even with the length cap lifted, the 36-digit run that
	took the old pattern over a second must be instant."""
	ns = _namespace()
	ns["_MAX_DURATION_LEN"] = 10 ** 9
	start = time.perf_counter()
	with pytest.raises(ValueError):
		ns["parse_duration"]("1" * 36 + "!")
	assert time.perf_counter() - start < 0.1
