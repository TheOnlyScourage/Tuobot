# -*- coding: utf-8 -*-
"""Milestone & rank-up detection tests — pure logic against the REAL rank
thresholds from bot/constants.py (loaded via the conftest stub pattern)."""


def R(win, team: int = 0):
	"""One ranked result from `team`'s perspective.
	win: True = won, False = lost, None = draw."""
	if win is None:
		return (None, team)
	return (team if win else 1 - team, team)


def P(*, nick="Player", results=(), old=None, new=None, peak=None):
	return dict(nick=nick, results=list(results), old_rating=old,
				new_rating=new, peak_before=peak)


# ── match-count milestones ────────────────────────────────────────────────────

def test_count_milestone_fires_even_on_a_loss(milestones):
	results = [R(False)] * 100
	lines = milestones.detect_milestones([P(results=results)])
	assert len(lines) == 1
	assert "100th" in lines[0] and "🎖️" in lines[0]


def test_non_milestone_counts_stay_silent(milestones):
	for n in (49, 99, 101, 249):
		assert milestones.detect_milestones([P(results=[R(False)] * n)]) == []


def test_every_configured_milestone_fires(milestones):
	for n in milestones.MATCH_MILESTONES:
		lines = milestones.detect_milestones([P(results=[R(False)] * n)])
		assert any(f"{n}th" in line for line in lines)


# ── rank-ups ──────────────────────────────────────────────────────────────────

def test_rank_up_first_time_and_name_parsing(milestones):
	# 1580 (Silver) -> 1615 (Gold), never been higher: first time.
	lines = milestones.detect_milestones([P(old=1580, new=1615, peak=1580)])
	assert len(lines) == 1
	assert "Gold" in lines[0] and "for the first time" in lines[0]
	assert "Q6Gold" in lines[0]  # the rank emoji rides along


def test_rank_up_reclimb_has_no_first_time_suffix(milestones):
	# Peaked at 1750 (Gold) before a season reset; climbing back into Gold.
	lines = milestones.detect_milestones([P(old=1580, new=1615, peak=1750)])
	assert len(lines) == 1
	assert "Gold" in lines[0] and "for the first time" not in lines[0]


def test_rank_up_with_no_history_counts_as_first_time(milestones):
	lines = milestones.detect_milestones([P(old=1580, new=1615, peak=None)])
	assert "for the first time" in lines[0]


def test_rank_down_and_flat_stay_silent(milestones):
	assert milestones.detect_milestones([P(old=1615, new=1580, peak=1615)]) == []
	assert milestones.detect_milestones([P(old=1500, new=1520, peak=1500)]) == []


def test_multi_tier_jump_announces_the_landing_tier(milestones):
	# 1390 (Bronze) -> 1610 (Gold) skips Silver; the line names Gold.
	lines = milestones.detect_milestones([P(old=1390, new=1610, peak=1390)])
	assert "Gold" in lines[0] and "Silver" not in lines[0]


# ── best-streak records ───────────────────────────────────────────────────────

def test_new_best_streak_at_floor_fires(milestones):
	lines = milestones.detect_milestones([P(results=[R(True)] * 4)])
	assert len(lines) == 1 and "W4" in lines[0] and "🔥" in lines[0]


def test_below_floor_record_stays_silent(milestones):
	assert milestones.detect_milestones([P(results=[R(True)] * 3)]) == []


def test_only_the_record_setting_match_fires(milestones):
	# Record of 5 set long ago; today's win only reaches a run of 4.
	results = [R(True)] * 5 + [R(False)] + [R(True)] * 4
	assert milestones.detect_milestones([P(results=results)]) == []


def test_tying_the_record_is_not_a_new_record(milestones):
	results = [R(True)] * 4 + [R(False)] + [R(True)] * 4
	assert milestones.detect_milestones([P(results=results)]) == []


def test_record_from_earlier_run_needs_a_final_win(milestones):
	# The W4 record was set, then the FINAL result is a loss: nothing fires.
	results = [R(True)] * 4 + [R(False)]
	assert milestones.detect_milestones([P(results=results)]) == []


def test_draws_break_streaks(milestones):
	# W W W D W W W W -> runs of 3 then 4; the final win sets a W4 record.
	results = [R(True)] * 3 + [R(None)] + [R(True)] * 4
	lines = milestones.detect_milestones([P(results=results)])
	assert len(lines) == 1 and "W4" in lines[0]


def test_team1_perspective_wins_count(milestones):
	results = [R(True, team=1)] * 4
	lines = milestones.detect_milestones([P(results=results)])
	assert len(lines) == 1 and "W4" in lines[0]


# ── stacking & shape ──────────────────────────────────────────────────────────

def test_one_player_can_stack_all_three(milestones):
	# 250th match, the win crosses into Gold for the first time, AND it
	# extends the all-time best streak to W4.
	results = [R(False)] * 246 + [R(True)] * 4
	lines = milestones.detect_milestones([
		P(nick="Scourage", results=results, old=1580, new=1615, peak=1580)
	])
	assert len(lines) == 3
	assert "250th" in lines[0] and "Gold" in lines[1] and "W4" in lines[2]


def test_empty_input_and_empty_results(milestones):
	assert milestones.detect_milestones([]) == []
	assert milestones.detect_milestones([P(results=[])]) == []


# ── tier helpers ──────────────────────────────────────────────────────────────

def test_rank_tier_boundaries(milestones):
	assert milestones.rank_tier(799) == 0     # Chad
	assert milestones.rank_tier(800) == 1     # Wood begins
	assert milestones.rank_tier(1599) == 4    # Silver
	assert milestones.rank_tier(1600) == 5    # Gold begins
	assert milestones.rank_tier(2400) == 8    # Star is the ceiling


def test_rank_label_names(milestones):
	assert milestones.rank_label(0)[1] == "Chad"
	assert milestones.rank_label(6)[1] == "Diamond"
	assert milestones.rank_label(8)[1] == "Star"
