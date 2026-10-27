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
	# 1380 (Silver) -> 1415 (Gold), never been higher: first time.
	lines = milestones.detect_milestones([P(old=1380, new=1415, peak=1380)])
	assert len(lines) == 1
	assert "Gold" in lines[0] and "for the first time" in lines[0]
	assert "Q6Gold" in lines[0]  # the rank emoji rides along


def test_rank_up_reclimb_has_no_first_time_suffix(milestones):
	# Peaked at 1550 (Gold) before a season reset; climbing back into Gold.
	lines = milestones.detect_milestones([P(old=1380, new=1415, peak=1550)])
	assert len(lines) == 1
	assert "Gold" in lines[0] and "for the first time" not in lines[0]


def test_rank_up_with_no_history_counts_as_first_time(milestones):
	lines = milestones.detect_milestones([P(old=1380, new=1415, peak=None)])
	assert "for the first time" in lines[0]


def test_rank_down_and_flat_stay_silent(milestones):
	assert milestones.detect_milestones([P(old=1415, new=1380, peak=1415)]) == []
	assert milestones.detect_milestones([P(old=1500, new=1520, peak=1500)]) == []


def test_multi_tier_jump_announces_the_landing_tier(milestones):
	# 1190 (Bronze) -> 1410 (Gold) skips Silver; the line names Gold.
	lines = milestones.detect_milestones([P(old=1190, new=1410, peak=1190)])
	assert "Gold" in lines[0] and "Silver" not in lines[0]


def test_platinum_rank_up(milestones):
	# 1590 (Gold) -> 1620 (Platinum): the new tier announces by name.
	lines = milestones.detect_milestones([P(old=1590, new=1620, peak=1590)])
	assert len(lines) == 1 and "Platinum" in lines[0] and "Q6Platinum" in lines[0]


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


def test_aborts_do_not_break_streaks(milestones):
	# Q6 has no draws — winner-NULL rows are ABORTS and are invisible to
	# streaks: W W [abort] W W merges into one W4 run.
	results = [R(True)] * 2 + [R(None)] + [R(True)] * 2
	lines = milestones.detect_milestones([P(results=results)])
	assert len(lines) == 1 and "W4" in lines[0]


def test_losses_still_reset_streaks(milestones):
	results = [R(True), R(False)] + [R(True)] * 4
	lines = milestones.detect_milestones([P(results=results)])
	assert len(lines) == 1 and "W4" in lines[0]


def test_aborts_count_toward_match_milestones(milestones):
	# An aborted match is still a match you showed up for.
	lines = milestones.detect_milestones([P(results=[R(None)] * 100)])
	assert len(lines) == 1 and "100th" in lines[0]


def test_all_aborts_never_fire_streaks(milestones):
	assert milestones.detect_milestones([P(results=[R(None)] * 10)]) == []


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
		P(nick="Scourage", results=results, old=1380, new=1415, peak=1380)
	])
	assert len(lines) == 3
	assert "250th" in lines[0] and "Gold" in lines[1] and "W4" in lines[2]


def test_empty_input_and_empty_results(milestones):
	assert milestones.detect_milestones([]) == []
	assert milestones.detect_milestones([P(results=[])]) == []


# ── tier helpers ──────────────────────────────────────────────────────────────

def test_rank_tier_boundaries(milestones):
	# Oct 2026 ladder: Chad 0 / Wood 600 / Iron 800 / Bronze 1000 / Silver 1200
	# / Gold 1400 / Platinum 1600 / Diamond 1800 / Champion 2000 / Star 2200
	assert milestones.rank_tier(599) == 0     # Chad
	assert milestones.rank_tier(600) == 1     # Wood begins
	assert milestones.rank_tier(1399) == 4    # Silver
	assert milestones.rank_tier(1400) == 5    # Gold begins
	assert milestones.rank_tier(1599) == 5    # still Gold
	assert milestones.rank_tier(1600) == 6    # Platinum begins
	assert milestones.rank_tier(1799) == 6    # still Platinum
	assert milestones.rank_tier(1800) == 7    # Diamond begins
	assert milestones.rank_tier(2400) == 9    # Star is the ceiling


def test_rank_label_names(milestones):
	assert milestones.rank_label(0)[1] == "Chad"
	assert milestones.rank_label(6)[1] == "Platinum"
	assert milestones.rank_label(7)[1] == "Diamond"
	assert milestones.rank_label(9)[1] == "Star"
