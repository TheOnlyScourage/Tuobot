# -*- coding: utf-8 -*-
"""The daily queue clear's clock (pure) plus wiring guards.

bot/queues/daily_clear_rules.py is stdlib-only (zoneinfo) and loads by path.
The clock must: fire at 4:00 AM Central strictly after `now`, never
retroactively on boot, and stay on 4:00 AM local across both DST changes."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("daily_clear_rules", ROOT / "bot" / "queues" / "daily_clear_rules.py")
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)

TZ = "America/Chicago"
CHI = ZoneInfo(TZ)


def _local(y, m, d, hh, mm=0):
	return datetime(y, m, d, hh, mm, tzinfo=CHI)


def _nxt(now_local):
	return rules.next_clear_after(now_local.astimezone(timezone.utc), 4, TZ).astimezone(CHI)


def test_before_four_fires_today():
	assert _nxt(_local(2026, 10, 27, 1, 30)) == _local(2026, 10, 27, 4)


def test_after_four_fires_tomorrow_not_retroactively():
	# a bot booting at 10 AM must NOT clear immediately
	assert _nxt(_local(2026, 10, 27, 10)) == _local(2026, 10, 28, 4)


def test_exactly_four_fires_tomorrow():
	assert _nxt(_local(2026, 10, 27, 4)) == _local(2026, 10, 28, 4)


def test_result_is_utc_aware_and_correct_offset():
	out = rules.next_clear_after(_local(2026, 10, 27, 1).astimezone(timezone.utc), 4, TZ)
	assert out.tzinfo is not None and out.utcoffset().total_seconds() == 0
	assert out == datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)   # CDT = UTC-5


def test_fall_back_dst_change_keeps_four_am_local():
	# DST ends Sun Nov 1 2026 at 2:00 AM Central: the night is 25 hours long,
	# but the clear still lands on 4:00 AM LOCAL (now CST = UTC-6), not 3:00.
	before = _local(2026, 10, 31, 23)
	fire = _nxt(before)
	assert fire == _local(2026, 11, 1, 4)
	assert fire.utcoffset().total_seconds() == -6 * 3600
	assert (fire.astimezone(timezone.utc) - before.astimezone(timezone.utc)).total_seconds() == 6 * 3600


def test_spring_forward_dst_change_keeps_four_am_local():
	# DST starts Sun Mar 14 2027 at 2:00 AM Central (2:00 → 3:00 skipped).
	before = _local(2027, 3, 13, 23)
	fire = _nxt(before)
	assert fire == _local(2027, 3, 14, 4)
	assert fire.utcoffset().total_seconds() == -5 * 3600
	assert (fire.astimezone(timezone.utc) - before.astimezone(timezone.utc)).total_seconds() == 4 * 3600


def test_chain_of_days_never_drifts():
	t = _local(2026, 10, 20, 12)
	fires = []
	for _ in range(20):
		t = _nxt(t)
		fires.append(t)
	assert all(f.hour == 4 and f.minute == 0 for f in fires)
	assert [f.day for f in fires[:3]] == [21, 22, 23]


# ── wiring guards ─────────────────────────────────────────────────────────────

def _src(rel):
	return (ROOT / rel).read_text(encoding="utf-8")


def test_wired_into_tick_and_exported():
	assert "await bot.daily_clear.think(frame_time)" in _src("bot/events.py")
	assert "from .queues.daily_clear import daily_clear" in _src("bot/__init__.py")


def test_constants():
	src = _src("bot/constants.py")
	assert "QUEUE_CLEAR_HOUR     = 4" in src and 'QUEUE_CLEAR_TIMEZONE = "America/Chicago"' in src
