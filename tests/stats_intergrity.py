# -*- coding: utf-8 -*-
"""Guards for the Nov 2026 Batch 3 stats fixes: all-or-nothing saves, the
fill-in penalty, a safe undo, season stamping, /top after a reset, atomic match
ids, indexes, the real rank ladder for decay, and the /profile, /rank,
/season_end and /activity fixes.

These files need nextcord/aiomysql/Pillow, so the checks read the source with
ast. The behaviour was verified on a real MariaDB database (registration,
force-sub penalty, abort, undo incl. streaks, season guard, rollback on an
injected failure, 50 concurrent match ids) when the fixes were written."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(rel):
	return (ROOT / rel).read_text(encoding="utf-8")


def _tree(rel):
	return ast.parse(_src(rel))


def _func(tree, name, cls=None):
	scope = tree
	if cls:
		scope = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
	return next(
		n for n in ast.walk(scope)
		if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
	)


def _code(fn):
	"""fn's source without its docstring (which may describe the old bug)."""
	body = [
		s for s in fn.body
		if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))
	]
	return ast.unparse(ast.Module(body=body, type_ignores=[]))


def _writes_outside(fn, obj="db"):
	"""Calls like db.insert/update/delete/execute anywhere in fn."""
	return [
		ast.unparse(n.func) for n in ast.walk(fn)
		if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
		and isinstance(n.func.value, ast.Name) and n.func.value.id == obj
		and n.func.attr in {"insert", "insert_many", "update", "delete", "execute", "executemany"}
	]


def _with_tx_blocks(fn):
	return [
		w for w in ast.walk(fn)
		if isinstance(w, ast.AsyncWith) and "db.transaction()" in ast.unparse(w.items[0].context_expr)
	]


# ── core/DBAdapters/mysql.py ─────────────────────────────────────────────────

def test_adapter_has_transactions_and_one_query_path():
	tree = _tree("core/DBAdapters/mysql.py")
	adapter = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Adapter")
	names = {n.name for n in adapter.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
	assert {"transaction", "_cursor"} <= names
	for method in ("execute", "executemany", "fetchone", "fetchall"):
		src = ast.unparse(_func(tree, method, cls="Adapter"))
		assert "self._cursor()" in src and "pool.acquire" not in src, method
	tx = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Transaction")
	assert {n.name for n in tx.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))} >= {"_cursor", "transaction"}
	tx_src = ast.unparse(_func(tree, "transaction", cls="Adapter"))
	assert "conn.begin()" in tx_src and "conn.rollback()" in tx_src and "conn.commit()" in tx_src


def test_the_db_port_from_the_url_is_used():
	"""dbPort was parsed from DB_URI but never passed: every connection went to 3306."""
	assert "port=int(self.dbPort)" in ast.unparse(_func(_tree("core/DBAdapters/mysql.py"), "connect", cls="Adapter"))


# ── bot/stats/stats.py ───────────────────────────────────────────────────────

def test_registrations_write_inside_one_transaction():
	tree = _tree("bot/stats/stats.py")
	for name in ("register_match_ranked", "register_match_unranked"):
		fn = _func(tree, name)
		blocks = _with_tx_blocks(fn)
		assert len(blocks) == 1, name
		inside = ast.Module(body=blocks[0].body, type_ignores=[])
		assert not _writes_outside(inside), f"{name}: a db. write inside the transaction bypasses it"
	ranked = _func(tree, "register_match_ranked")
	# the only write allowed outside is the idempotent INSERT IGNORE of player rows
	outside = [w for w in _writes_outside(ranked)]
	assert outside == ["db.insert_many"], outside


def test_fill_in_penalty_is_saved_before_zeroing_and_only_on_a_real_loss():
	src = ast.unparse(_func(_tree("bot/stats/stats.py"), "register_match_ranked"))
	assert "if m.winner is not None and m.winner != sub_team_idx:" in src
	assert src.index("redirected[p.id] = (orig_id, changes.get(p.id, 0))") < src.index("changes[p.id] = 0")
	assert "rating_change=new_rating - p1['rating']" in src


def test_season_is_stamped_at_registration():
	tree = _tree("bot/stats/stats.py")
	for name in ("register_match_ranked", "register_match_unranked"):
		src = ast.unparse(_func(tree, name))
		assert "season = await _season_now(m.qc.id)" in src and "season_number" not in src, name


def test_undo_is_safe():
	tree = _tree("bot/stats/stats.py")
	src = ast.unparse(_func(tree, "undo_match"))
	assert "match['season'] != season" in src, "season guard"
	assert "for user_id, h in changed.items():" in src, "iterate the players the match actually changed"
	assert "streak=streaks.get(user_id, 0)" in src, "streaks recomputed"
	assert len(_with_tx_blocks(_func(tree, "undo_match"))) == 2  # unranked + ranked paths
	walk = ast.unparse(_func(tree, "_season_streaks_without"))
	assert "LEFT JOIN qc_player_matches" in walk and "h.match_id != %s" in walk


def test_top_does_not_depend_on_the_season_board():
	src = _code(_func(_tree("bot/stats/stats.py"), "top"))
	assert "qc_players" not in src
	assert "f\" AND at>" not in src  # parameterised now


def test_match_ids_are_atomic():
	src = _src("bot/stats/stats.py")
	assert 'LAST_INSERT_ID(next_id + 1)' in src
	assert "select_one(('next_id',), 'qc_match_id_counter')" not in ast.unparse(_func(_tree("bot/stats/stats.py"), "next_match"))


def test_history_indexes_are_created_at_boot():
	tree = _tree("bot/stats/stats.py")
	init = ast.unparse(_func(tree, "init_stats_tables"))
	assert "await _ensure_index(table, name, columns)" in init
	assert "disabled_guilds" not in init
	src = _src("bot/stats/stats.py")
	for name in ("idx_pm_channel_user", "idx_rh_channel_user", "idx_rh_match", "idx_m_channel_season"):
		assert f'"{name}"' in src, name


def test_weekly_decay_task_is_kept_and_failures_logged():
	tree = _tree("bot/stats/stats.py")
	assert "self._task = asyncio.create_task(" in ast.unparse(_func(tree, "think", cls="StatsJobs"))
	assert "except Exception" in ast.unparse(_func(tree, "_apply_rating_decays", cls="StatsJobs"))


# ── bot/stats/rating.py ──────────────────────────────────────────────────────

def test_decay_and_snap_use_the_real_ladder():
	tree = _tree("bot/stats/rating.py")
	assert "RANK_EMOJIS" in ast.unparse(_func(tree, "_ladder_floors"))
	for name in ("apply_decay", "snap_ratings"):
		src = ast.unparse(_func(tree, name, cls="Rating"))
		assert "ranks = _ladder_floors()" in src and "ranks_table if" not in src, name


# ── bot/commands/stats.py ────────────────────────────────────────────────────

def test_profile_passes_career_matches():
	src = ast.unparse(_func(_tree("bot/commands/stats.py"), "profile"))
	assert "career_matches=ranked_total" in src


def test_rank_win_rate_ignores_aborts():
	src = ast.unparse(_func(_tree("bot/commands/stats.py"), "rank"))
	assert "(p['wins'] + p['losses'] or 1)" in src


def test_season_end_guards_and_reads_the_board_directly():
	src = ast.unparse(_func(_tree("bot/commands/stats.py"), "season_end"))
	assert "if not ranked_queues:" in src
	assert src.index("if not ranked_queues:") < src.index("qualified = ")
	assert "get_lb()" not in src
	assert "except Exception:\n            pass" not in src


def test_activity_is_pillow_on_central_time():
	tree = _tree("bot/commands/stats.py")
	code = _code(_func(tree, "activity")) + _code(_func(tree, "_render_activity_heatmap"))
	assert "+05:30" not in code and "CONVERT_TZ" not in code
	assert "ZoneInfo(QUEUE_CLEAR_TIMEZONE)" in code
	for path in (ROOT / "bot").rglob("*.py"):
		for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
			if isinstance(node, (ast.Import, ast.ImportFrom)):
				mods = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
				assert not any(m and m.startswith("matplotlib") for m in mods), path
