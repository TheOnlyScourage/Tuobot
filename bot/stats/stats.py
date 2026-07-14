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
    if m.winner is None:                        # draw -> no rating changes
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
async def init_stats_tables() -> None:
    """Create the stats tables if missing: players, qc_players, rating history,
    matches, the match-id counter, per-player match rows, and disabled guilds."""
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
    await db._ensure_table(dict(
        tname="disabled_guilds",
        columns=[dict(cname="guild_id", ctype=db.types.int)],
        primary_keys=["guild_id"]
    ))
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


async def next_match() -> int:
    """Increment the match-id counter and return the id for the next match."""
    counter = await db.select_one(('next_id',), 'qc_match_id_counter')
    await db.update('qc_match_id_counter', dict(next_id=counter['next_id'] + 1))
    log.debug(f"Current match_id is {counter['next_id']}")
    return counter['next_id']


# ══════════════════════════════════════════════════════════════════════════════
#  Match registration
# ══════════════════════════════════════════════════════════════════════════════
async def register_match_unranked(ctx: bot.Context, m: bot.Match) -> None:
    """Record an unranked match: the match row plus per-player rows, with no
    rating changes."""
    await db.insert('qc_matches', dict(
        match_id=m.id, channel_id=m.qc.id,
        queue_id=m.queue.cfg.p_key, queue_name=m.queue.name,
        alpha_name=m.teams[0].name, beta_name=m.teams[1].name,
        at=int(time.time()), season=getattr(m, 'season_number', None),
        ranked=0, winner=None
    ))
    await db.insert_many('qc_players', (
        dict(channel_id=m.qc.id, user_id=p.id)
        for p in m.players
    ), on_dublicate="ignore")
    for p in m.players:
        nick = get_nick(p)
        await db.update("qc_players", dict(nick=nick),
                        keys=dict(channel_id=m.qc.id, user_id=p.id))
        team = 0 if p in m.teams[0] else (1 if p in m.teams[1] else None)
        await db.insert('qc_player_matches',
                        dict(match_id=m.id, channel_id=m.qc.id,
                             user_id=p.id, nick=nick, team=team))


async def register_match_ranked(ctx: bot.Context, m: bot.Match) -> None:
    """Record a ranked match: apply MMR changes, update each player's
    rating/streak/W-L-D, and write rating history and per-player rows. A fill-in
    sub's loss penalty is redirected to the original player. Finishes by
    refreshing rating roles and posting the results embed."""
    now = int(time.time())
    await db.insert('qc_matches', dict(
        match_id=m.id, channel_id=m.qc.id,
        queue_id=m.queue.cfg.p_key, queue_name=m.queue.name,
        alpha_name=m.teams[0].name, beta_name=m.teams[1].name,
        at=now, season=getattr(m, 'season_number', None),
        ranked=1, winner=m.winner,
        alpha_score=m.scores[0], beta_score=m.scores[1]
    ))
    # Ensure all players exist in qc_players
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

    # ── Build before / after dicts for print_rating_results ───────────────
    before, after = {}, {}
    fill_subs = getattr(m, 'fill_subs', {})
    # Zero out fill-in sub changes before building after dict so results
    # embed shows no change for the fill-in player when their team loses.
    for p in m.players:
        if p.id in fill_subs:
            _, sub_team_idx = fill_subs[p.id]
            team_won = (m.winner is not None and m.winner == sub_team_idx)
            if not team_won:
                changes[p.id] = 0

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
            new_streak = 0
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

    # ── Persist ratings ───────────────────────────────────────────────────
    for p in m.players:
        nick     = get_nick(p)
        team_idx = 0 if p in m.teams[0] else 1
        a        = after[p.id]
        b        = before[p.id]
        change   = a['rating'] - b['rating']
        # ── Fill-in sub: team lost → redirect penalty to original player ──
        if p.id in fill_subs:
            orig_id, sub_team_idx = fill_subs[p.id]
            team_won = (m.winner is not None and m.winner == sub_team_idx)
            if not team_won:
                # Apply loss to original (player1), skip player2's rating update
                p1 = await db.select_one(
                    ('rating', 'deviation', 'wins', 'losses', 'draws', 'streak'),
                    'qc_players',
                    where=dict(channel_id=m.qc.rating.channel_id, user_id=orig_id)
                )
                if p1 and p1.get('rating') is not None:
                    p1_streak = p1['streak']
                    p1_new_streak = p1_streak - 1 if p1_streak < 0 else -1
                    await db.update('qc_players', dict(
                        rating  = max(0, p1['rating'] + change),
                        losses  = p1['losses'] + 1,
                        streak  = p1_new_streak,
                        last_ranked_match_at=now,
                    ), keys=dict(channel_id=m.qc.rating.channel_id, user_id=orig_id))
                    await db.insert('qc_rating_history', dict(
                        channel_id      = m.qc.rating.channel_id,
                        user_id         = orig_id,
                        at              = now,
                        rating_before   = p1['rating'],
                        rating_change   = change,
                        deviation_before= p1['deviation'],
                        deviation_change= 0,
                        match_id        = m.id,
                        reason          = f"{m.queue.name} (fill-in sub penalty)"
                    ))
                # Player2: record in match log only, no rating change
                await db.insert('qc_player_matches', dict(
                    match_id=m.id, channel_id=m.qc.id,
                    user_id=p.id, nick=nick, team=team_idx
                ))
                continue   # skip normal update for player2
        # ── Normal update ─────────────────────────────────────────────────
        await db.update(
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
            keys=dict(channel_id=m.qc.rating.channel_id, user_id=p.id)
        )
        await db.insert('qc_player_matches', dict(
            match_id=m.id, channel_id=m.qc.id,
            user_id=p.id, nick=nick, team=team_idx
        ))
        await db.insert('qc_rating_history', dict(
            channel_id      = m.qc.rating.channel_id,
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
#  Admin operations
# ══════════════════════════════════════════════════════════════════════════════
async def undo_match(ctx: bot.Context, match_id: int) -> dict | None:
    """Reverse a match: roll each player's rating/record back using the stored
    history, reverse any Hogwarts house points via the house_awards ledger,
    delete the match's history and per-player rows, and refresh roles.

    Returns None if the match isn't found on the channel; otherwise the
    {house: points_reverted} dict from the ledger (may be empty — draws,
    unranked matches, and pre-ledger history awarded nothing to reverse)."""
    match = await db.select_one(
        ('ranked', 'winner'), 'qc_matches',
        where=dict(match_id=match_id, channel_id=ctx.qc.id)
    )
    if not match:
        return None
    if match['ranked']:
        p_matches = await db.select(
            ('user_id', 'team'), 'qc_player_matches', where=dict(match_id=match_id)
        )
        p_history = iter_to_dict(
            await db.select(
                ('user_id', 'rating_change', 'deviation_change'),
                'qc_rating_history', where=dict(match_id=match_id)
            ), key='user_id'
        )
        stats = iter_to_dict(
            await ctx.qc.rating.get_players((p['user_id'] for p in p_matches)),
            key='user_id'
        )
        for p in p_matches:
            new     = stats[p['user_id']]
            changes = p_history[p['user_id']]
            if match['winner'] is None:
                new['draws']  = max(new['draws']  - 1, 0)
            elif match['winner'] == p['team']:
                new['wins']   = max(new['wins']   - 1, 0)
            else:
                new['losses'] = max(new['losses'] - 1, 0)
            new['rating']    = max(new['rating']    - changes['rating_change'],    0)
            new['deviation'] = max(new['deviation'] - changes['deviation_change'], 0)
            await db.update(
                "qc_players", new,
                keys=dict(channel_id=ctx.qc.rating.channel_id, user_id=p['user_id'])
            )
        await db.delete("qc_rating_history", where=dict(match_id=match_id))
        members = (ctx.channel.guild.get_member(p['user_id']) for p in p_matches)
        await ctx.qc.update_rating_roles(*(m for m in members if m is not None))

    # Reverse any Hogwarts house points this match awarded. Ledger-based, so
    # matches with no house_awards rows are a clean no-op.
    from bot.stats.house_points import revert_for_match
    reverted_houses = await revert_for_match(match_id)

    await db.delete('qc_player_matches', where=dict(match_id=match_id))
    await db.delete('qc_matches',        where=dict(match_id=match_id))
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

    This is the explicit destructive tool behind `/admin stats nuclear_option`
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


async def replace_player(channel_id: int, user_id1: int, user_id2: int, new_nick: str) -> None:
    """Re-point one player's records (rating, history, match rows) onto a new
    user id and nick, dropping any existing rows for the target id."""
    await db.delete("qc_players", {'channel_id': channel_id, 'user_id': user_id2})
    where = {'channel_id': channel_id, 'user_id': user_id1}
    await db.update("qc_players",        {'user_id': user_id2, 'nick': new_nick}, where)
    await db.update("qc_rating_history", {'user_id': user_id2}, where)
    await db.update("qc_player_matches", {'user_id': user_id2}, where)


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
    limited to matches after the time_gap timestamp."""
    total = await db.fetchone(
        "SELECT COUNT(*) as count FROM `qc_matches` WHERE channel_id=%s"
        + (f" AND at>{time_gap}" if time_gap else ""),
        (channel_id,)
    )
    data = await db.fetchall(
        "SELECT p.nick as nick, COUNT(*) as count FROM `qc_player_matches` AS pm "
        "JOIN `qc_players` AS p ON pm.user_id=p.user_id AND pm.channel_id=p.channel_id "
        "JOIN `qc_matches` AS m ON pm.match_id=m.match_id "
        "WHERE pm.channel_id=%s "
        + (f"AND m.at>{time_gap} " if time_gap else "")
        + "GROUP BY p.user_id ORDER BY count DESC LIMIT 10",
        (channel_id,)
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
        for qc in bot.queue_channels.values():
            await qc.apply_rating_decay()
            await asyncio.sleep(1)

    async def think(self, frame_time: int) -> None:
        """Frame tick: once the scheduled time passes, reschedule and kick off
        the weekly decay."""
        if frame_time > self.next_decay_at:
            self.next_decay_at = int(self._next_monday().timestamp())
            asyncio.create_task(self._apply_rating_decays())


jobs = StatsJobs()
