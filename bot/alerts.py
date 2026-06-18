# -*- coding: utf-8 -*-
"""
41 Alert System
===============
Monitors active matches every second.  During the :33–:41 window, the moment
a draft finishes (match enters WAITING_REPORT) an alert is sent to the queue
channel warning players not to re-queue yet.  At :42 a "Safe to Queue"
message is sent — but only if an alert was triggered this hour.
"""

from datetime import datetime
from nextcord import Embed, Colour
import bot
from core.client import dc
from core.console import log


# ── Per-channel state ─────────────────────────────────────────────────────
# { qc_id: {'alert_hour': int, 'safe_sent': bool} }
_channel_state: dict = {}

# Match IDs that have already fired the alert (prevent double-firing)
_alerted_match_ids: set = set()


# ── Embed builders ────────────────────────────────────────────────────────

def _alert_embed(queue_name: str) -> Embed:
    return Embed(
        colour=Colour(0xe67e22),
        title="⚠️ 41 Alert — Draft Locked In!",
        description=(
            f"A **{queue_name}** draft has just completed near the top of the hour.\n\n"
            "**Do not add to the queue yet.** "
            "Wait for the ✅ **Safe to Queue** message at :42 before re-queuing."
        )
    )


def _safe_embed() -> Embed:
    return Embed(
        colour=Colour(0x27b75e),
        title="✅ Safe to Queue!",
        description="The active match is underway. It is now safe to add to the queue again."
    )


# ── Send helpers ──────────────────────────────────────────────────────────

async def _send_alert(m) -> None:
    channel = dc.get_channel(m.qc.id)
    if channel is None:
        return
    try:
        await channel.send(embed=_alert_embed(m.queue.name))
        log.info(f"[41 Alert] Fired for match {m.id} in #{channel.name}")
    except Exception as exc:
        log.error(f"[41 Alert] Send failed: {exc}")


async def _send_safe(qc_id: int) -> None:
    channel = dc.get_channel(qc_id)
    if channel is None:
        return
    try:
        await channel.send(embed=_safe_embed())
        log.info(f"[Safe to Queue] Sent to #{channel.name}")
    except Exception as exc:
        log.error(f"[Safe to Queue] Send failed: {exc}")


# ── Background think hook (called every ~1 s) ─────────────────────────────

async def think(frame_time: float) -> None:
    """
    Registered in dc.events['on_think'] by bot/events.py.

    :33–:41  Check every active match; when one enters WAITING_REPORT for
             the first time this hour, fire the 41 Alert immediately.
    :42      For every channel that received an alert this hour, send the
             Safe to Queue message (once per hour per channel).
    :00      Light cleanup — remove completed match IDs from the tracking set.
    """
    if not bot.bot_ready:
        return

    now    = datetime.fromtimestamp(frame_time)
    minute = now.minute
    hour   = now.hour

    # ── :33–:41 : watch for newly completed drafts ────────────────────────
    if 33 <= minute <= 41:
        for m in list(bot.active_matches):
            if m.id in _alerted_match_ids:
                continue                          # already alerted for this match
            if m.state != m.WAITING_REPORT:
                continue                          # draft not done yet

            # Draft just finished inside the alert window
            _alerted_match_ids.add(m.id)
            state = _channel_state.setdefault(
                m.qc.id, {'alert_hour': -1, 'safe_sent': False}
            )
            state['alert_hour'] = hour
            state['safe_sent']  = False
            await _send_alert(m)

    # ── :42 : Safe to Queue for any channel whose alert fired this hour ───
    elif minute == 42:
        for qc_id, state in list(_channel_state.items()):
            if state['alert_hour'] == hour and not state['safe_sent']:
                state['safe_sent'] = True
                await _send_safe(qc_id)

    # ── :00 : clean up completed match IDs ───────────────────────────────
    elif minute == 0:
        active_ids = {m.id for m in bot.active_matches}
        _alerted_match_ids.intersection_update(active_ids)
