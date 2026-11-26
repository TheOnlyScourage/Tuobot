# -*- coding: utf-8 -*-
"""Stats persistence and queries: table setup, ranked/unranked match
registration (MMR, streaks, rating history), admin undo/reset, leaderboard
queries, and the weekly decay job. The MMR formula lives in mmr_engine.py."""
from __future__ import annotations

import time
import datetime
import asyncio
import bot
from core.console import log
from core.database import db
from core.utils import iter_to_dict, get_nick


# ══════════════════════════════════════════════════════════════════════════════
#  Custom MMR engine
# ══════════════════════════════════════════════════════════════════════════════
# The MMR formula now lives in bot/stats/mmr_engine.py (single source of truth,
# testable in isolation). This adapter handles the match-level draw case and
# unpacks the match into the plain lists the engine expects, then delegates the
# actual math to compute_mmr_changes().
from bot.stats.mmr_engine import compute_mmr_changes


def _calculate_mmr_changes(m: bot.Match, ratings_by_id: dict) -> dict:
    """
    Return {user_id: mmr_change} for every player in the match.
    Positive = gain (winner), negative = loss (loser). Draw -> 0 for everyone.

    The formula lives in mmr_engine.compute_mmr_changes; this adapter only:
      - short-circuits draws (a match-level concept) to all-zero
      - unpacks winner/loser teams + captains from the match object
    """
    if m.winner is None:                        # abort (no winner) -> no rating changes
        return {p.id: 0 for p in m.players}

    winners = m.teams[m.winner]
    losers  = m.teams[1 - m.winner]

    return compute_mmr_changes(
        ratings_by_id=ratings_by_id,
        winners=winners,
        losers=losers,
        winner_captain=winners[0],
        loser_captain=losers[0],
        streaks_by_id=None,          # read streaks from each player's row
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Table initialisation
# ══════════════════════════════════════════════════════════════════════════════
# Match history is permanent and every 6v6 adds 12 rows to both
# qc_player_matches and qc_rating_history. Without these, /profile, /rank,
# milestone checks, captain eligibility (every queue pop) and undo were all
# full-table scans that grow forever.
_INDEXES = (
    ("qc_player_matches", "idx_pm_channel_user", ("channel_id", "user_id")),
    ("qc_rating_history", "idx_rh_channel_user", ("channel_id", "user_id")),
    ("qc_rating_history", "idx_rh_match",        ("match_id",)),
    ("qc_matches",        "idx_m_channel_season", ("channel_id", "season")),
)


async def _ensure_index(table: str, name: str, columns: tuple) -> None:
    """CREATE INDEX unless it already exists (MySQL has no IF NOT EXISTS for it)."""
    exists = await db.fetchone(
        "SELECT 1 AS present FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND INDEX_NAME = %s LIMIT 1",
        (db.dbName, table, name)
    )
    if not exists:
        cols = ", ".join(f"`{c}`" for c in columns)
        await db.execute(f"CREATE INDEX `{name}` ON `{table}` ({cols})")
        log.info(f"[stats] created index {name} on {table}")


async def init_stats_tables() -> None:
    """Create the stats tables if missing: players, qc_players, rating history,
    matches, the match-id counter and per-player match rows, plus their
    lookup indexes."""
    await db._ensure_table(dict(
        tname="players",
        columns=[
            dict(cname="user_id",   ctype=db.types.int),
            dict(cname="name",      ctype=db.types.str),
            dict(cname="allow_dm",  ctype=db.types.bool),
            dict(cname="expire",    ctype=db.types.int)
        ],
        primary_keys=["user_id"]
    ))
    await db._ensure_table(dict(
        tname="qc_players",
        columns=[
            dict(cname="channel_id",          ctype=db.types.int),
            dict(cname="user_id",              ctype=db.types.int),
            dict(cname="nick",                 ctype=db.types.str),
            dict(cname="is_hidden",            ctype=db.types.bool, default=0),
            dict(cname="rating",               ctype=db.types.int),
            dict(cname="deviation",            ctype=db.types.int),
            dict(cname="wins",                 ctype=db.types.int, notnull=True, default=0),
            dict(cname="losses",               ctype=db.types.int, notnull=True, default=0),
            dict(cname="draws",                ctype=db.types.int, notnull=True, default=0),
            dict(cname="streak",               ctype=db.types.int, notnull=True, default=0),
            dict(cname="last_ranked_match_at", ctype=db.types.int)
        ],
        primary_keys=["user_id", "channel_id"]
    ))
    await db._ensure_table(dict(
        tname="qc_rating_history",
        columns=[
            dict(cname="id",               ctype=db.types.int, autoincrement=True),
            dict(cname="channel_id",       ctype=db.types.int),
            dict(cname="user_id",          ctype=db.types.int),
            dict(cname="at",               ctype=db.types.int),
            dict(cname="rating_before",    ctype=db.types.int),
            dict(cname="rating_change",    ctype=db.types.int),
            dict(cname="deviation_before", ctype=db.types.int),
            dict(cname="deviation_change", ctype=db.types.int),
            dict(cname="match_id",         ctype=db.types.int),
            dict(cname="reason",           ctype=db.types.str)
        ],
        primary_keys=["id"]
    ))
    await db._ensure_table(dict(
        tname="qc_matches",
        columns=[
            dict(cname="match_id",    ctype=db.types.int),
            dict(cname="channel_id",  ctype=db.types.int),
            dict(cname="queue_id",    ctype=db.types.int),
            dict(cname="queue_name",  ctype=db.types.str),
            dict(cname="at",          ctype=db.types.int),
            dict(cname="season",      ctype=db.types.int),
            dict(cname="alpha_name",  ctype=db.types.str),
            dict(cname="beta_name",   ctype=db.types.str),
            dict(cname="ranked",      ctype=db.types.bool),
            dict(cname="winner",      ctype=db.types.bool),
            dict(cname="alpha_score", ctype=db.types.int),
            dict(cname="beta_score",  ctype=db.types.int)
        ],
        primary_keys=["match_id"]
    ))
    await db._ensure_table(dict(
        tname="qc_match_id_counter",
        columns=[dict(cname="next_id", ctype=db.types.int)]
    ))
    await db._ensure_table(dict(
        tname="qc_player_matches",
        columns=[
            dict(cname="match_id",   ctype=db.types.int),
            dict(cname="channel_id", ctype=db.types.int),
            dict(cname="user_id",    ctype=db.types.int),
            dict(cname="nick",       ctype=db.types.str),
            dict(cname="team",       ctype=db.types.bool)
        ],
        primary_keys=["match_id", "user_id"]
    ))
    # (The old `disabled_guilds` table was never read; it's no longer created.)
    for table, name, columns in _INDEXES:
        try:
            await _ensure_index(table, name, columns)
        except Exception as e:  # an index is an optimisation — never block the boot on it
            log.error(f"[stats] could not create index {name} on {table}: {e}")
    await _backfill_match_seasons()


async def _backfill_match_seasons() -> None:
    """One-time, idempotent migration: stamp legacy qc_matches rows (season
    NULL) with their channel's CURRENT season number.

    The season column was added mid-Season-19; without this backfill, that
    season's own end-of-season highlights would miss every match played
    before the column existed. Runs at every startup but no-ops instantly
    once no NULL rows remain. New matches are stamped at registration, so
    NULLs only ever reappear if a registration's season lookup failed — in
    which case this self-heals them on the next boot."""
    from bot.stats.season import get_current_season_number
    rows = await db.fetchall(
        "SELECT DISTINCT channel_id FROM qc_matches WHERE season IS NULL", ()
    )
    for r in rows:
        season = await get_current_season_number(r['channel_id'])
        await db.execute(
            "UPDATE qc_matches SET season=%s WHERE channel_id=%s AND season IS NULL",
            (season, r['channel_id'])
        )


async def check_match_id_counter() -> None:
    """Sync the match-id counter to one past the highest existing match,
    seeding the row if it doesn't exist yet."""
    m = await db.select_one(('match_id',), 'qc_matches', order_by='match_id', limit=1)
    next_known = m['match_id'] + 1 if m else 0
    counter = await db.select_one(('next_id',), 'qc_match_id_counter')
    if counter is None:
        await db.insert('qc_match_id_counter', dict(next_id=next_known))
    elif next_known > counter['next_id']:
        await db.update('qc_match_id_counter', dict(next_id=next_known))


_NEXT_MATCH_SQL = "UPDATE qc_match_id_counter SET next_id = LAST_INSERT_ID(next_id + 1)"


async def next_match() -> int:
    """Hand out the next match id in ONE atomic statement. It used to read the
    counter and then write it back, so two queues popping at the same moment
    could both get the same id (the second registration then failed). MySQL's
    LAST_INSERT_ID(expr) idiom returns the new value with the update itself."""
    new_next = await db.execute(_NEXT_MATCH_SQL)
    if not new_next:  # no counter row yet (fresh database) — seed it, then retry
        await check_match_id_counter()
        new_next = await db.execute(_NEXT_MATCH_SQL)
    log.debug(f"Current match_id is {new_next - 1}")
    return new_next - 1


# ══════════════════════════════════════════════════════════════════════════════
#  Match registration
# ══════════════════════════════════════════════════════════════════════════════
async def _season_now(channel_id: int) -> int | None:
    """The season a result belongs to: the one current when it is SAVED, since
    that's the board it rates into. It used to come from Match.new, so manual
    results (/match create) and matches restored after a redeploy were saved
    with no season, and a match running across /season_end was filed under
    the old season while rating into the new board. On a lookup failure the
    next boot's backfill stamps it."""
    from bot.stats.season import get_current_season_number
    try:
        return await get_current_season_number(channel_id)
    except Exception as e:
        log.error(f"[stats] season lookup failed for channel {channel_id}: {e}")
        return None


async def register_match_unranked(ctx: bot.Context, m: bot.Match) -> None:
    """Record an unranked match: the match row plus per-player rows, with no
    rating changes. All-or-nothing (one transaction)."""
    season = await _season_now(m.qc.id)
    async with db.transaction() as tx:
        await tx.insert('qc_matches', dict(
            match_id=m.id, channel_id=m.qc.id,
            queue_id=m.queue.cfg.p_key, queue_name=m.queue.name,
            alpha_name=m.teams[0].name, beta_name=m.teams[1].name,
            at=int(time.time()), season=season,
            ranked=0, winner=None
        ))
        await tx.insert_many('qc_players', (
            dict(channel_id=m.qc.id, user_id=p.id)
            for p in m.players
        ), on_dublicate="ignore")
        for p in m.players:
            nick = get_nick(p)
            await tx.update("qc_players", dict(nick=nick),
                            keys=dict(channel_id=m.qc.id, user_id=p.id))
            team = 0 if p in m.teams[0] else (1 if p in m.teams[1] else None)
            await tx.insert('qc_player_matches',
                            dict(match_id=m.id, channel_id=m.qc.id,
                                 user_id=p.id, nick=nick, team=team))


async def register_match_ranked(ctx: bot.Context, m: bot.Match) -> None:
    """Record a ranked match: apply MMR changes, update each player's
    rating/streak/W-L-D, and write rating history and per-player rows. A fill-in
    sub's loss penalty is redirected to the original player. Finishes by
    refreshing rating roles and posting the results embed.

    Every write happens in ONE transaction. It used to be ~38 separate
    auto-committed statements, so a failure partway (a DB blip, a redeploy
    closing the pool) left some players re-rated and the rest not, and the
    failure notice then invited a re-record that applied their MMR twice."""
    now = int(time.time())
    season = await _season_now(m.qc.id)
    rating_channel = m.qc.rating.channel_id
    # Ensure all players exist in qc_players. Idempotent (INSERT IGNORE), and
    # deliberately OUTSIDE the transaction: the rating read below goes through
    # another connection and must see these rows.
    init_dev = getattr(m.qc.rating, 'init_deviation', 350) or 350
    for channel_id in {m.qc.id, m.qc.rating.channel_id}:
        await db.insert_many('qc_players', (
            dict(channel_id=channel_id, user_id=p.id, nick=get_nick(p), deviation=init_dev)
            for p in m.players
        ), on_dublicate="ignore")

    # ── Fetch current player data (rating, streak, etc.) ──────────────────
    raw = await m.qc.rating.get_players((p.id for p in m.players))
    ratings_by_id = {r['user_id']: r for r in raw}

    # ── Calculate custom MMR changes ──────────────────────────────────────
    changes = _calculate_mmr_changes(m, ratings_by_id)

    # ── Fill-in subs (/match sub_player mid-match) ────────────────────────
    # A fill-in never risks their own rating: when their team LOSES, the loss
    # and its MMR go to the player they replaced. The engine's figure is saved
    # BEFORE it's zeroed for the results embed — it used to be zeroed first, so
    # the redirect applied 0 MMR (broken June–Nov 2026). An abort is a
    # non-event for everyone, so nobody is redirected (it used to hand the
    # original player a loss and break their streak).
    fill_subs = getattr(m, 'fill_subs', {})
    redirected = {}  # sub_id -> (original_id, the MMR the sub would have lost)
    for p in m.players:
        if p.id in fill_subs:
            orig_id, sub_team_idx = fill_subs[p.id]
            if m.winner is not None and m.winner != sub_team_idx:
                redirected[p.id] = (orig_id, changes.get(p.id, 0))
                changes[p.id] = 0

    # ── Build before / after dicts for print_rating_results ───────────────
    before, after = {}, {}

    for p in m.players:
        b           = ratings_by_id.get(p.id) or {}
        cur_rating  = b.get('rating') or 1500
        cur_streak  = b.get('streak', 0)
        cur_dev     = b.get('deviation') or init_dev
        team_idx    = 0 if p in m.teams[0] else 1
        is_winner   = (m.winner is not None and m.winner == team_idx)
        change      = changes.get(p.id, 0)
        before[p.id] = {
            'rating':    cur_rating,
            'deviation': cur_dev,
            'wins':      b.get('wins', 0),
            'losses':    b.get('losses', 0),
            'draws':     b.get('draws', 0),
            'streak':    cur_streak,
        }
        if m.winner is None:
            # ABORT (Q6 has no draws): the match is on the books but it's a
            # non-event competitively — the streak is PRESERVED, and the
            # `draws` column (schema name only) counts the abort.
            new_streak = cur_streak
            new_wins   = b.get('wins', 0)
            new_losses = b.get('losses', 0)
            new_draws  = b.get('draws', 0) + 1
        elif is_winner:
            new_streak = cur_streak + 1 if cur_streak > 0 else 1
            new_wins   = b.get('wins', 0) + 1
            new_losses = b.get('losses', 0)
            new_draws  = b.get('draws', 0)
        else:
            new_streak = cur_streak - 1 if cur_streak < 0 else -1
            new_wins   = b.get('wins', 0)
            new_losses = b.get('losses', 0) + 1
            new_draws  = b.get('draws', 0)
        after[p.id] = {
            'rating':    max(0, cur_rating + change),
            'deviation': cur_dev,
            'wins':      new_wins,
            'losses':    new_losses,
            'draws':     new_draws,
            'streak':    new_streak,
        }

    # ── Persist: all or nothing ───────────────────────────────────────────
    async with db.transaction() as tx:
        await tx.insert('qc_matches', dict(
            match_id=m.id, channel_id=m.qc.id,
            queue_id=m.queue.cfg.p_key, queue_name=m.queue.name,
            alpha_name=m.teams[0].name, beta_name=m.teams[1].name,
            at=now, season=season,
            ranked=1, winner=m.winner,
            alpha_score=m.scores[0], beta_score=m.scores[1]
        ))
        for p in m.players:
            nick     = get_nick(p)
            team_idx = 0 if p in m.teams[0] else 1
            a        = after[p.id]
            b        = before[p.id]
            change   = a['rating'] - b['rating']
            # ── Fill-in sub whose team lost → the original takes the loss ──
            if p.id in redirected:
                orig_id, penalty = redirected[p.id]
                p1 = await tx.select_one(
                    ('rating', 'deviation', 'losses', 'streak'),
                    'qc_players',
                    where=dict(channel_id=rating_channel, user_id=orig_id)
                )
                if p1 and p1.get('rating') is not None:
                    p1_streak = p1['streak'] or 0
                    new_rating = max(0, p1['rating'] + penalty)
                    await tx.update('qc_players', dict(
                        rating  = new_rating,
                        losses  = p1['losses'] + 1,
                        streak  = p1_streak - 1 if p1_streak < 0 else -1,
                        last_ranked_match_at=now,
                    ), keys=dict(channel_id=rating_channel, user_id=orig_id))
                    await tx.insert('qc_rating_history', dict(
                        channel_id      = rating_channel,
                        user_id         = orig_id,
                        at              = now,
                        rating_before   = p1['rating'],
                        rating_change   = new_rating - p1['rating'],
                        deviation_before= p1['deviation'],
                        deviation_change= 0,
                        match_id        = m.id,
                        reason          = f"{m.queue.name} (fill-in sub penalty)"
                    ))
                # The sub: match log only — their own record and rating are untouched
                # (so they also get no rating-history row; undo relies on that).
                await tx.insert('qc_player_matches', dict(
                    match_id=m.id, channel_id=m.qc.id,
                    user_id=p.id, nick=nick, team=team_idx
                ))
                continue
            # ── Normal update ─────────────────────────────────────────────
            await tx.update(
                "qc_players",
                dict(
                    nick      = nick,
                    rating    = a['rating'],
                    deviation = a['deviation'],
                    wins      = a['wins'],
                    losses    = a['losses'],
                    draws     = a['draws'],
                    streak    = a['streak'],
                    last_ranked_match_at=now,
                ),
                keys=dict(channel_id=rating_channel, user_id=p.id)
            )
            await tx.insert('qc_player_matches', dict(
                match_id=m.id, channel_id=m.qc.id,
                user_id=p.id, nick=nick, team=team_idx
            ))
            await tx.insert('qc_rating_history', dict(
                channel_id      = rating_channel,
                user_id         = p.id,
                at              = now,
                rating_before   = b['rating'],
                rating_change   = change,
                deviation_before= b['deviation'],
                deviation_change= 0,
                match_id        = m.id,
                reason          = m.queue.name
            ))
    await m.qc.update_rating_roles(*m.players)
    await m.print_rating_results(ctx, before, after)


# ══════════════════════════════════════════════════════════════════════════════
#  Career lookups
# ══════════════════════════════════════════════════════════════════════════════
async def career_games_by_id(channel_id: int, user_ids) -> dict:
    """{user_id: games played on this channel, all-time} for the given players —
    the same rows /profile is built from (every match they were seated on a
    team for, ranked or not, across all seasons). Players with no games are
    simply absent (callers treat missing as 0). Feeds captain eligibility."""
    ids = list(user_ids)
    if not ids:
        return {}
    ph = ", ".join(["%s"] * len(ids))
    rows = await db.fetchall(
        f"SELECT user_id, COUNT(*) AS games FROM qc_player_matches "
        f"WHERE channel_id = %s AND team IS NOT NULL AND user_id IN ({ph}) "
        f"GROUP BY user_id",
        (channel_id, *ids)
    )
    return {r['user_id']: int(r['games']) for r in rows}


# ══════════════════════════════════════════════════════════════════════════════
#  Admin operations
# ══════════════════════════════════════════════════════════════════════════════
async def _season_streaks_without(rating_channel_id: int, season: int, user_ids: list, match_id: int) -> dict:
    """Each player's streak over the season's remaining ranked results, as if
    `match_id` had never happened. Same walk as registration: a win extends a
    winning streak (or starts one at +1), a loss the opposite, an abort leaves
    it alone. Only results that actually changed the player's rating count (a
    fill-in sub absorbed nothing; the player they replaced took the loss), so
    this walks qc_rating_history joined to each match's outcome."""
    if not user_ids:
        return {}
    ph = ", ".join(["%s"] * len(user_ids))
    rows = await db.fetchall(
        f"SELECT h.user_id, pm.team, m.winner "
        f"FROM qc_rating_history h "
        f"JOIN qc_matches m ON m.match_id = h.match_id "
        f"LEFT JOIN qc_player_matches pm ON pm.match_id = h.match_id AND pm.user_id = h.user_id "
        f"WHERE h.channel_id = %s AND h.user_id IN ({ph}) AND m.ranked = 1 "
        f"AND m.season = %s AND h.match_id != %s "
        f"ORDER BY h.user_id, h.match_id",
        (rating_channel_id, *user_ids, season, match_id)
    )
    streaks = {user_id: 0 for user_id in user_ids}
    for r in rows:
        if r['team'] is None:          # fill-in penalty: the original player took a loss
            won = False
        elif r['winner'] is None:      # abort: streak preserved
            continue
        else:
            won = int(r['winner']) == int(r['team'])
        s = streaks[r['user_id']]
        streaks[r['user_id']] = (s + 1 if s > 0 else 1) if won else (s - 1 if s < 0 else -1)
    return streaks


async def undo_match(ctx: bot.Context, match_id: int) -> dict | None:
    """Reverse a match: roll each affected player's rating/record/streak back,
    reverse any Hogwarts house points via the house_awards ledger, delete the
    match's rows, and refresh roles.

    Returns None if the match isn't found on the channel; otherwise the
    {house: points_reverted} dict from the ledger (may be empty — aborts,
    unranked matches, and pre-ledger history awarded nothing to reverse).

    Rewritten Nov 2026. The old version looped over the match's player rows and
    crashed (KeyError) on any force-subbed match, because the sub has no
    rating-history row — after half the players were already rolled back, so a
    retry rolled them back a second time. It also never restored streaks,
    never reverted the original player's redirected loss, and would "undo" a
    previous season's match against the current board."""
    match = await db.select_one(
        ('ranked', 'winner', 'season'), 'qc_matches',
        where=dict(match_id=match_id, channel_id=ctx.qc.id)
    )
    if not match:
        return None

    from bot.stats.house_points import revert_for_match

    if not match['ranked']:
        reverted_houses = await revert_for_match(match_id)
        async with db.transaction() as tx:
            await tx.delete('qc_player_matches', where=dict(match_id=match_id))
            await tx.delete('qc_matches',        where=dict(match_id=match_id))
        return reverted_houses

    season = await _season_now(ctx.qc.id)
    if match['season'] is not None and season is not None and match['season'] != season:
        raise bot.Exc.ValueError(ctx.qc.gt(
            "Match {id} is from Season {season}. Undoing it now would take its rating "
            "changes off the current season's board, so it can't be undone."
        ).format(id=match_id, season=match['season']))

    rating_channel = ctx.qc.rating.channel_id
    # Read everything before writing anything.
    teams = {
        r['user_id']: r['team'] for r in
        await db.select(('user_id', 'team'), 'qc_player_matches', where=dict(match_id=match_id))
    }
    # Exactly the players whose season row this match changed have a history row
    # for it — including the original player of a fill-in sub (not in the match
    # rows). A fill-in who absorbed nothing has no history row: nothing to undo.
    changed = {
        h['user_id']: h for h in
        await db.select(
            ('user_id', 'rating_change', 'deviation_change'),
            'qc_rating_history', where=dict(match_id=match_id)
        )
    }
    rows = iter_to_dict(await ctx.qc.rating.get_players(list(changed)), key='user_id')
    streaks = await _season_streaks_without(
        rating_channel, match['season'] if match['season'] is not None else season, list(changed), match_id
    )

    # House points first: ledger-based and idempotent (the ledger rows go with
    # it), so if anything below fails a retry still finishes the whole undo.
    reverted_houses = await revert_for_match(match_id)

    async with db.transaction() as tx:
        for user_id, h in changed.items():
            row = dict(rows[user_id])
            team = teams.get(user_id)  # None → an original player's redirected fill-in loss
            if team is not None and match['winner'] is None:
                row['draws'] = max((row.get('draws') or 0) - 1, 0)
            elif team is not None and int(match['winner']) == int(team):
                row['wins'] = max((row.get('wins') or 0) - 1, 0)
            else:
                row['losses'] = max((row.get('losses') or 0) - 1, 0)
            row['rating'] = max((row.get('rating') or 0) - (h['rating_change'] or 0), 0)
            row['deviation'] = max((row.get('deviation') or 0) - (h['deviation_change'] or 0), 0)
            await tx.update("qc_players", dict(
                rating=row['rating'], deviation=row['deviation'], wins=row.get('wins') or 0,
                losses=row.get('losses') or 0, draws=row.get('draws') or 0,
                streak=streaks.get(user_id, 0),
            ), keys=dict(channel_id=rating_channel, user_id=user_id))
        await tx.delete("qc_rating_history", where=dict(match_id=match_id))
        await tx.delete('qc_player_matches', where=dict(match_id=match_id))
        await tx.delete('qc_matches',        where=dict(match_id=match_id))

    affected = set(teams) | set(changed)
    members = (ctx.channel.guild.get_member(user_id) for user_id in affected)
    await ctx.qc.update_rating_roles(*(m for m in members if m is not None))
    return reverted_houses


async def reset_channel(channel_id: int) -> None:
    """Season reset: clear the live season board (qc_players — ratings, W-L-D,
    streaks) so the new season starts fresh.

    Match history (qc_matches / qc_player_matches / qc_rating_history) is
    deliberately PRESERVED — it powers all-time stats (/profile, future
    milestones). Season-scoped consumers (season highlights, the season_end
    summary) filter on qc_matches.season instead of relying on the tables
    being emptied. This changed in July 2026; Seasons 1-18 predate the bot's
    data and are gone, so all-time history effectively begins at Season 19."""
    await db.delete("qc_players", where={'channel_id': channel_id})


async def wipe_channel(channel_id: int) -> None:
    """FULL wipe: ratings AND all-time match history for the channel.

    This is the explicit destructive tool behind `/stats nuclear_option`
    (owner-locked) — unlike
    the season reset (reset_channel), this permanently destroys the permanent
    history that powers /profile and future milestones. There is no undo."""
    where = {'channel_id': channel_id}
    await db.delete("qc_players",        where=where)
    await db.delete("qc_rating_history", where=where)
    await db.delete("qc_matches",        where=where)
    await db.delete("qc_player_matches", where=where)


async def reset_player(channel_id: int, user_id: int) -> None:
    """Reset one player's SEASON state (their qc_players row: rating, W-L-D,
    streak). Their match history and rating history are preserved — this is
    "start the season over", not "erase this player". A true erasure would be
    a deliberate manual DB operation."""
    await db.delete("qc_players", where={'channel_id': channel_id, 'user_id': user_id})


async def replace_player(channel_id: int, user_id1: int, user_id2: int, new_nick: str,
                         rating_channel_id: int | None = None) -> None:
    """Re-point one player's records (rating, history, match rows) onto a new
    user id and nick, dropping any rating the target id already had.

    Ratings and rating history live under the channel's RATING channel (the
    same channel unless `rating_channel` is set); match rows live under the
    channel itself. All four statements run in one transaction: they used to be
    separate, so a failure halfway could delete player2's rating without moving
    player1's onto it."""
    rating_channel_id = rating_channel_id or channel_id
    async with db.transaction() as tx:
        await tx.delete("qc_players", {'channel_id': rating_channel_id, 'user_id': user_id2})
        rated = {'channel_id': rating_channel_id, 'user_id': user_id1}
        await tx.update("qc_players",        {'user_id': user_id2, 'nick': new_nick}, rated)
        await tx.update("qc_rating_history", {'user_id': user_id2}, rated)
        await tx.update("qc_player_matches", {'user_id': user_id2}, {'channel_id': channel_id, 'user_id': user_id1})


# ══════════════════════════════════════════════════════════════════════════════
#  Query helpers
# ══════════════════════════════════════════════════════════════════════════════
async def qc_stats(channel_id: int) -> dict:
    """Return {total, queues}: match counts per queue for a channel."""
    data = await db.fetchall(
        "SELECT `queue_name`, COUNT(*) as count FROM `qc_matches` "
        "WHERE `channel_id`=%s GROUP BY `queue_name` ORDER BY count DESC",
        (channel_id,)
    )
    return dict(total=sum(i['count'] for i in data), queues=data)


async def user_stats(channel_id: int, user_id: int) -> dict:
    """Return {total, queues}: match counts per queue for one player."""
    data = await db.fetchall(
        "SELECT `queue_name`, COUNT(*) as count FROM `qc_player_matches` AS pm "
        "JOIN `qc_matches` AS m ON pm.match_id=m.match_id "
        "WHERE pm.channel_id=%s AND user_id=%s "
        "GROUP BY m.queue_name ORDER BY count DESC",
        (channel_id, user_id)
    )
    return dict(total=sum(i['count'] for i in data), queues=data)


async def top(channel_id: int, time_gap: int | None = None) -> dict:
    """Return {total, players}: the top 10 players by matches played, optionally
    limited to matches after the time_gap timestamp.

    Built from match history alone (the nick is the player's most recent one).
    It used to INNER JOIN the season board (qc_players) just for the nick, so
    right after /season_end every period came back empty, and mid-season
    anyone who hadn't played yet this season vanished from the all-time list."""
    since = " AND m.at > %s" if time_gap else ""
    args = (channel_id, time_gap) if time_gap else (channel_id,)
    total = await db.fetchone(
        "SELECT COUNT(*) AS count FROM `qc_matches` AS m WHERE m.channel_id = %s" + since,
        args
    )
    data = await db.fetchall(
        "SELECT pm.user_id, "
        "SUBSTRING_INDEX(GROUP_CONCAT(pm.nick ORDER BY pm.match_id DESC SEPARATOR '\\n'), '\\n', 1) AS nick, "
        "COUNT(*) AS count "
        "FROM `qc_player_matches` AS pm "
        "JOIN `qc_matches` AS m ON pm.match_id = m.match_id "
        "WHERE pm.channel_id = %s" + since + " "
        "GROUP BY pm.user_id ORDER BY count DESC LIMIT 10",
        args
    )
    return dict(total=total['count'], players=data)


async def last_games(channel_id: int) -> list[dict]:
    """Return each channel player's row plus the timestamp of their most recent
    ranked match (NULL if none)."""
    return await db.fetchall(
        "SELECT tmp.at, p.* "
        "FROM `qc_players` AS p "
        "LEFT JOIN ("
        "  SELECT MAX(h.at) AS at, h.user_id FROM `qc_rating_history` AS h"
        "    WHERE h.channel_id=%s AND h.match_id IS NOT NULL"
        "    GROUP BY h.user_id"
        ") AS tmp ON p.user_id=tmp.user_id "
        "WHERE p.channel_id=%s",
        (channel_id, channel_id)
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Background jobs
# ══════════════════════════════════════════════════════════════════════════════
class StatsJobs:
    """Scheduler for periodic stats jobs: currently the weekly rating deviation
    decay, applied each Monday."""

    def __init__(self):
        self.next_decay_at = int(self._next_monday().timestamp())
        self._task = None  # keep a reference: an unreferenced task can be garbage-collected mid-run

    @staticmethod
    def _next_monday() -> datetime.datetime:
        """Return next Monday at midnight."""
        d = datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
        d += datetime.timedelta(days=1)
        while d.weekday() != 0:
            d += datetime.timedelta(days=1)
        return d

    @staticmethod
    async def _apply_rating_decays() -> None:
        """Apply the weekly deviation decay across all queue channels."""
        log.info("--- Applying weekly deviation decays ---")
        for qc in list(bot.queue_channels.values()):
            try:
                await qc.apply_rating_decay()
            except Exception as e:  # one channel's failure must not skip the rest, or vanish
                log.error(f"[decay] channel {qc.id} failed: {e}")
            await asyncio.sleep(1)

    async def think(self, frame_time: int) -> None:
        """Frame tick: once the scheduled time passes, reschedule and kick off
        the weekly decay."""
        if frame_time > self.next_decay_at:
            self.next_decay_at = int(self._next_monday().timestamp())
            self._task = asyncio.create_task(self._apply_rating_decays())


jobs = StatsJobs()
