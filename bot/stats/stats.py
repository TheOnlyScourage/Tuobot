# -*- coding: utf-8 -*-
import time
import datetime
import asyncio
import bot
from core.console import log
from core.database import db
from core.utils import iter_to_dict, find, get_nick


# ══════════════════════════════════════════════════════════════════════════════
#  Custom MMR engine
# ══════════════════════════════════════════════════════════════════════════════

_MAX_TEAM_DIFF  = 1000.0   # rating point difference treated as maximum
_BASE_EQUAL     = 50.0     # base MMR for equal teams
_BASE_MIN       = 10.0     # base MMR when heavy favourite wins (expected)
_BASE_MAX       = 200.0    # base MMR when heavy underdog wins (upset)
_HARD_CAP       = 200      # absolute max MMR change after all factors
_CAPTAIN_BONUS  = 10       # flat extra MMR for the captain win OR loss
_STREAK_STEP    = 0.05     # bonus per streak game beyond 1
_STREAK_CAP     = 0.25     # maximum streak bonus (reached at 6 games)
_PICK_STEP      = 2.5      # MMR difference between adjacent pick slots


def _streak_multiplier(abs_streak: int) -> float:
    """1.0 at streak=1, +5% per game, capped at 1.25 (streak=6+)."""
    if abs_streak <= 1:
        return 1.0
    return 1.0 + min((abs_streak - 1) * _STREAK_STEP, _STREAK_CAP)


def _team_base_mmr(avg_winner: float, avg_loser: float) -> float:
    """
    Base MMR for this match outcome, purely from team average difference.

    Equal teams  (diff = 0)    →  50
    Max diff, favourite wins   →  10
    Max diff, underdog wins    → 200
    """
    diff  = abs(avg_winner - avg_loser)
    ratio = min(diff, _MAX_TEAM_DIFF) / _MAX_TEAM_DIFF   # 0.0 – 1.0

    if avg_winner <= avg_loser:                          # underdog or equal
        return _BASE_EQUAL + (_BASE_MAX - _BASE_EQUAL) * ratio   # 50 → 200
    else:                                                # favourite
        return _BASE_EQUAL - (_BASE_EQUAL - _BASE_MIN)  * ratio  # 50 → 10


def _calculate_mmr_changes(m, ratings_by_id: dict) -> dict:
    """
    Return {user_id: mmr_change} for every player in the match.

    Positive = gain (winner), negative = loss (loser).
    Draw → 0 for everyone.

    Formula per player:
      1. team base MMR   (from avg-rating difference)
      2. pick-order offset (captain gets +, last pick gets -)
      3. captain flat bonus (+10)
      4. streak multiplier  (1.0 – 1.25×)
      5. hard cap at 200
    """
    if m.winner is None:                        # draw → no rating changes
        return {p.id: 0 for p in m.players}

    winner_team = m.teams[m.winner]
    loser_team  = m.teams[1 - m.winner]

    def avg_r(team):
        vals = [ratings_by_id.get(p.id, {}).get('rating') or 1500 for p in team]
        return sum(vals) / max(len(vals), 1)

    base = _team_base_mmr(avg_r(winner_team), avg_r(loser_team))
    changes = {}

    for team, is_winner in [(winner_team, True), (loser_team, False)]:
        n    = len(team)
        half = (n - 1) / 2.0          # centre point so adjustments sum to 0

        for pick_pos, player in enumerate(team):
            # ── Pick-order offset ──────────────────────────────────────────
            # captain (pos 0) → +half*step, last pick → -half*step
            pick_offset    = (half - pick_pos) * _PICK_STEP
            captain_bonus  = float(_CAPTAIN_BONUS) if pick_pos == 0 else 0.0
            individual_base = base + pick_offset + captain_bonus

            # ── Streak multiplier ──────────────────────────────────────────
            cur_streak = (ratings_by_id.get(player.id) or {}).get('streak', 0)
            if is_winner:
                new_streak = cur_streak + 1 if cur_streak > 0 else 1
            else:
                new_streak = cur_streak - 1 if cur_streak < 0 else -1

            multiplier = _streak_multiplier(abs(new_streak))
            mmr = individual_base * multiplier

            # ── Hard cap & sign ───────────────────────────────────────────
            mmr = min(mmr, _HARD_CAP)
            mmr = max(round(mmr), 1)       # never 0 or negative magnitude
            changes[player.id] = mmr if is_winner else -mmr

    return changes


# ══════════════════════════════════════════════════════════════════════════════
#  Table initialisation
# ══════════════════════════════════════════════════════════════════════════════

async def init_stats_tables():
    await db.ensure_table(dict(
        tname="players",
        columns=[
            dict(cname="user_id",   ctype=db.types.int),
            dict(cname="name",      ctype=db.types.str),
            dict(cname="allow_dm",  ctype=db.types.bool),
            dict(cname="expire",    ctype=db.types.int)
        ],
        primary_keys=["user_id"]
    ))
    await db.ensure_table(dict(
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
    await db.ensure_table(dict(
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
    await db.ensure_table(dict(
        tname="qc_matches",
        columns=[
            dict(cname="match_id",    ctype=db.types.int),
            dict(cname="channel_id",  ctype=db.types.int),
            dict(cname="queue_id",    ctype=db.types.int),
            dict(cname="queue_name",  ctype=db.types.str),
            dict(cname="at",          ctype=db.types.int),
            dict(cname="alpha_name",  ctype=db.types.str),
            dict(cname="beta_name",   ctype=db.types.str),
            dict(cname="ranked",      ctype=db.types.bool),
            dict(cname="winner",      ctype=db.types.bool),
            dict(cname="alpha_score", ctype=db.types.int),
            dict(cname="beta_score",  ctype=db.types.int),
            dict(cname="maps",        ctype=db.types.str)
        ],
        primary_keys=["match_id"]
    ))
    await db.ensure_table(dict(
        tname="qc_match_id_counter",
        columns=[dict(cname="next_id", ctype=db.types.int)]
    ))
    await db.ensure_table(dict(
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
    await db.ensure_table(dict(
        tname="disabled_guilds",
        columns=[dict(cname="guild_id", ctype=db.types.int)],
        primary_keys=["guild_id"]
    ))


async def check_match_id_counter():
    m = await db.select_one(('match_id',), 'qc_matches', order_by='match_id', limit=1)
    next_known = m['match_id'] + 1 if m else 0
    counter = await db.select_one(('next_id',), 'qc_match_id_counter')
    if counter is None:
        await db.insert('qc_match_id_counter', dict(next_id=next_known))
    elif next_known > counter['next_id']:
        await db.update('qc_match_id_counter', dict(next_id=next_known))


async def next_match():
    counter = await db.select_one(('next_id',), 'qc_match_id_counter')
    await db.update('qc_match_id_counter', dict(next_id=counter['next_id'] + 1))
    log.debug(f"Current match_id is {counter['next_id']}")
    return counter['next_id']


# ══════════════════════════════════════════════════════════════════════════════
#  Match registration
# ══════════════════════════════════════════════════════════════════════════════

async def register_match_unranked(ctx, m):
    await db.insert('qc_matches', dict(
        match_id=m.id, channel_id=m.qc.id,
        queue_id=m.queue.cfg.p_key, queue_name=m.queue.name,
        alpha_name=m.teams[0].name, beta_name=m.teams[1].name,
        at=int(time.time()), ranked=0, winner=None, maps="\n".join(m.maps)
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


async def register_match_ranked(ctx, m):
    now = int(time.time())

    await db.insert('qc_matches', dict(
        match_id=m.id, channel_id=m.qc.id,
        queue_id=m.queue.cfg.p_key, queue_name=m.queue.name,
        alpha_name=m.teams[0].name, beta_name=m.teams[1].name,
        at=now, ranked=1, winner=m.winner,
        alpha_score=m.scores[0], beta_score=m.scores[1],
        maps="\n".join(m.maps)
    ))

    # Ensure all players exist in qc_players
    for channel_id in {m.qc.id, m.qc.rating.channel_id}:
        await db.insert_many('qc_players', (
            dict(channel_id=channel_id, user_id=p.id, nick=get_nick(p))
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

    for p in m.players:
        b           = ratings_by_id.get(p.id) or {}
        cur_rating  = b.get('rating') or 1500
        cur_streak  = b.get('streak', 0)
        team_idx    = 0 if p in m.teams[0] else 1
        is_winner   = (m.winner is not None and m.winner == team_idx)

        before[p.id] = {
            'rating':    cur_rating,
            'deviation': b.get('deviation', 0),
            'wins':      b.get('wins', 0),
            'losses':    b.get('losses', 0),
            'draws':     b.get('draws', 0),
            'streak':    cur_streak,
        }

        change = changes.get(p.id, 0)

        if m.winner is None:        # draw
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
            'deviation': b.get('deviation', 0),
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
                nick    = nick,
                rating  = a['rating'],
                wins    = a['wins'],
                losses  = a['losses'],
                draws   = a['draws'],
                streak  = a['streak'],
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

async def undo_match(ctx, match_id):
    match = await db.select_one(
        ('ranked', 'winner'), 'qc_matches',
        where=dict(match_id=match_id, channel_id=ctx.qc.id)
    )
    if not match:
        return False
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
    await db.delete('qc_player_matches', where=dict(match_id=match_id))
    await db.delete('qc_matches',        where=dict(match_id=match_id))
    return True


async def reset_channel(channel_id):
    where = {'channel_id': channel_id}
    await db.delete("qc_players",        where=where)
    await db.delete("qc_rating_history", where=where)
    await db.delete("qc_matches",        where=where)
    await db.delete("qc_player_matches", where=where)


async def reset_player(channel_id, user_id):
    where = {'channel_id': channel_id, 'user_id': user_id}
    await db.delete("qc_players",        where=where)
    await db.delete("qc_rating_history", where=where)
    await db.delete("qc_player_matches", where=where)


async def replace_player(channel_id, user_id1, user_id2, new_nick):
    await db.delete("qc_players", {'channel_id': channel_id, 'user_id': user_id2})
    where = {'channel_id': channel_id, 'user_id': user_id1}
    await db.update("qc_players",        {'user_id': user_id2, 'nick': new_nick}, where)
    await db.update("qc_rating_history", {'user_id': user_id2}, where)
    await db.update("qc_player_matches", {'user_id': user_id2}, where)


# ══════════════════════════════════════════════════════════════════════════════
#  Query helpers
# ══════════════════════════════════════════════════════════════════════════════

async def qc_stats(channel_id):
    data = await db.fetchall(
        "SELECT `queue_name`, COUNT(*) as count FROM `qc_matches` "
        "WHERE `channel_id`=%s GROUP BY `queue_name` ORDER BY count DESC",
        (channel_id,)
    )
    return dict(total=sum(i['count'] for i in data), queues=data)


async def user_stats(channel_id, user_id):
    data = await db.fetchall(
        "SELECT `queue_name`, COUNT(*) as count FROM `qc_player_matches` AS pm "
        "JOIN `qc_matches` AS m ON pm.match_id=m.match_id "
        "WHERE pm.channel_id=%s AND user_id=%s "
        "GROUP BY m.queue_name ORDER BY count DESC",
        (channel_id, user_id)
    )
    return dict(total=sum(i['count'] for i in data), queues=data)


async def top(channel_id, time_gap=None):
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


async def last_games(channel_id):
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

    def __init__(self):
        self.next_decay_at = int(self._next_monday().timestamp())

    @staticmethod
    def _next_monday():
        d = datetime.datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
        d += datetime.timedelta(days=1)
        while d.weekday() != 0:
            d += datetime.timedelta(days=1)
        return d

    @staticmethod
    async def _apply_rating_decays():
        log.info("--- Applying weekly deviation decays ---")
        for qc in bot.queue_channels.values():
            await qc.apply_rating_decay()
            await asyncio.sleep(1)

    async def think(self, frame_time):
        if frame_time > self.next_decay_at:
            self.next_decay_at = int(self._next_monday().timestamp())
            asyncio.create_task(self._apply_rating_decays())


jobs = StatsJobs()
